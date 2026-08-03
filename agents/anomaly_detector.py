"""Flags hardcoded assumptions, undocumented logic, and other silent-risk patterns in the parsed workbook."""

import re
from collections import defaultdict

import networkx as nx

from core.models import AnomalyFinding, ParsedFile

_SEVERITY_RANK = {"blocker": 0, "warning": 1, "info": 2}

_LITERAL_PATTERN = re.compile(r"(?<![A-Za-z0-9_$])(\d+\.?\d*)(?![A-Za-z0-9_])")
_ALLOWED_LITERALS = {0.0, 1.0, 100.0}

_SUM_PATTERN = re.compile(r"SUM\(([^()]*)\)", re.IGNORECASE)
_RANGE_PATTERN = re.compile(r"^([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)$")

_DEFINITION_PATTERN = re.compile(r"^'?([^'!]+)'?!(.+)$")


def detect_anomalies(parsed_file: ParsedFile) -> list[AnomalyFinding]:
    findings: list[AnomalyFinding] = []
    findings.extend(_detect_hardcoded_literals(parsed_file))
    findings.extend(_detect_cross_tab_inconsistencies(parsed_file))
    findings.extend(_detect_excluded_sum_rows(parsed_file))
    findings.extend(_detect_circular_references(parsed_file))

    findings.sort(key=lambda finding: _SEVERITY_RANK[finding.severity])
    for index, finding in enumerate(findings, start=1):
        finding.finding_id = f"F{index:04d}"

    return findings


def _detect_hardcoded_literals(parsed_file: ParsedFile) -> list[AnomalyFinding]:
    findings = []
    for key, value in parsed_file.cells.items():
        if not (isinstance(value, str) and value.startswith("=")):
            continue
        tab, cell_ref = key.split("!", 1)
        for match in _LITERAL_PATTERN.finditer(value):
            literal = float(match.group(1))
            if literal in _ALLOWED_LITERALS:
                continue
            findings.append(
                AnomalyFinding(
                    finding_id="",
                    severity="warning",
                    tab=tab,
                    cell_ref=cell_ref,
                    description=f"Hardcoded literal {match.group(1)} embedded in formula",
                    raw_value=value,
                )
            )
    return findings


def _split_definition(definition: str) -> tuple[str, str] | None:
    match = _DEFINITION_PATTERN.match(definition.replace("$", ""))
    if not match:
        return None
    tab, cell_ref = match.group(1), match.group(2).split(":")[0]
    return tab, cell_ref


def _resolve_named_range_value(definition: str, cells: dict) -> float | None:
    split = _split_definition(definition)
    if split is None:
        return None
    tab, cell_ref = split
    value = cells.get(f"{tab}!{cell_ref}")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").replace("%", ""))
        except ValueError:
            return None
    return None


def _detect_cross_tab_inconsistencies(parsed_file: ParsedFile) -> list[AnomalyFinding]:
    # Only named ranges are compared here (not "cell labels" -- that half of the
    # spec needs an adjacency convention that wasn't specified, so it's skipped
    # rather than guessed).
    findings = []
    by_name: dict[str, dict[str, float]] = defaultdict(dict)
    for scoped_key, definition in parsed_file.named_ranges.items():
        scope, sep, name = scoped_key.partition("::")
        if not sep or scope == "workbook":
            continue
        value = _resolve_named_range_value(definition, parsed_file.cells)
        if value is not None:
            by_name[name][scope] = round(value, 9)

    for name, tab_values in by_name.items():
        if len(set(tab_values.values())) <= 1:
            continue
        first_tab = next(iter(tab_values))
        split = _split_definition(parsed_file.named_ranges[f"{first_tab}::{name}"])
        cell_ref = split[1] if split else ""
        details = ", ".join(f"{tab}={val}" for tab, val in tab_values.items())
        findings.append(
            AnomalyFinding(
                finding_id="",
                severity="blocker",
                tab=first_tab,
                cell_ref=cell_ref,
                description=f"Named range '{name}' has inconsistent values across tabs: {details}",
                raw_value=str(tab_values),
            )
        )
    return findings


def _detect_excluded_sum_rows(parsed_file: ParsedFile) -> list[AnomalyFinding]:
    findings = []
    for key, value in parsed_file.cells.items():
        if not (isinstance(value, str) and value.startswith("=")):
            continue
        tab, cell_ref = key.split("!", 1)

        for sum_match in _SUM_PATTERN.finditer(value):
            by_col: dict[str, list[tuple[int, int]]] = defaultdict(list)
            for arg in sum_match.group(1).split(","):
                range_match = _RANGE_PATTERN.match(arg.strip().replace("$", ""))
                if not range_match:
                    continue
                col_start, row_start, col_end, row_end = range_match.groups()
                if col_start.upper() == col_end.upper():
                    by_col[col_start.upper()].append((int(row_start), int(row_end)))

            for col, spans in by_col.items():
                if len(spans) < 2:
                    continue
                spans.sort()
                skipped = []
                for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
                    if next_start > prev_end + 1:
                        skipped.extend(f"{col}{r}" for r in range(prev_end + 1, next_start))
                if skipped:
                    findings.append(
                        AnomalyFinding(
                            finding_id="",
                            severity="warning",
                            tab=tab,
                            cell_ref=cell_ref,
                            description=f"SUM formula skips rows: {', '.join(skipped)}",
                            raw_value=value,
                        )
                    )
    return findings


def _detect_circular_references(parsed_file: ParsedFile) -> list[AnomalyFinding]:
    # Tab-level cycles from the parser's dependency graph -- a coarser signal
    # than cell-level circularity, but it's the only graph ParsedFile exposes.
    findings = []
    graph = nx.DiGraph(parsed_file.dependency_graph)
    for cycle in nx.simple_cycles(graph):
        if len(cycle) < 2:
            continue
        path = cycle + [cycle[0]]
        findings.append(
            AnomalyFinding(
                finding_id="",
                severity="blocker",
                tab=cycle[0],
                cell_ref="",
                description=f"Circular reference between tabs: {' -> '.join(path)}",
                raw_value=str(parsed_file.dependency_graph),
            )
        )
    return findings
