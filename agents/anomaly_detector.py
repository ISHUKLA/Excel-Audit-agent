"""Agent 2 — flags hardcoded assumptions, inconsistencies, and silent-risk
patterns in a parsed workbook.

Rule-based. No LLM.

Circular reference detection reads `cell_dependency_graph`, never
`tab_dependency_graph`. Two tabs referencing each other is ordinary — Provisions
reads an assumption from Hypotheses, Hypotheses reads a total back from
Provisions — and nothing about that is circular unless a single cell ends up
depending on itself. A tab-level check calls that a cycle and is wrong.
"""

import re
from collections import defaultdict
from typing import Optional

import networkx as nx

from core.models import AnomalyFinding, ParsedFile

_SEVERITY_RANK = {"blocker": 0, "warning": 1, "info": 2}

# A bare number in a formula, not part of a cell reference: the "1" in A1 is
# preceded by a letter, the "10" in B10 likewise, so neither matches.
_LITERAL_PATTERN = re.compile(r"(?<![A-Za-z0-9_$])(\d+\.?\d*)(?![A-Za-z0-9_])")
_ALLOWED_LITERALS = {0.0, 1.0, 100.0}

_SUM_PATTERN = re.compile(r"SUM\(([^()]*)\)", re.IGNORECASE)
_RANGE_PATTERN = re.compile(r"^([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)$")

_DEFINITION_PATTERN = re.compile(r"^'?([^'!]+)'?!(.+)$")


def detect_anomalies(parsed_file: ParsedFile) -> list[AnomalyFinding]:
    """Every anomaly found, blockers first.

    Findings carry no human decision — that is Gate 2's job, and a finding that
    arrives pre-dispositioned would defeat the point of the gate.
    """
    findings: list[AnomalyFinding] = []
    findings.extend(_detect_hardcoded_literals(parsed_file))
    findings.extend(_detect_cross_tab_inconsistencies(parsed_file))
    findings.extend(_detect_excluded_sum_rows(parsed_file))
    findings.extend(_detect_circular_references(parsed_file))

    findings.sort(key=lambda finding: _SEVERITY_RANK[finding.severity])
    for index, finding in enumerate(findings, start=1):
        finding.finding_id = f"F{index:04d}"

    return findings


def _formulas(parsed_file: ParsedFile):
    """Every formula cell, as (tab, cell_ref, formula)."""
    for key, record in parsed_file.cells.items():
        if record.formula is None:
            continue
        tab, cell_ref = key.split("!", 1)
        yield tab, cell_ref, record.formula


# ---------------------------------------------------------------------------
# 1. hardcoded literals
# ---------------------------------------------------------------------------


def _detect_hardcoded_literals(parsed_file: ParsedFile) -> list[AnomalyFinding]:
    findings = []
    for tab, cell_ref, formula in _formulas(parsed_file):
        for match in _LITERAL_PATTERN.finditer(formula):
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
                    raw_value=formula,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 2. cross-tab inconsistency
# ---------------------------------------------------------------------------


def _split_definition(definition: str) -> Optional[tuple[str, str]]:
    match = _DEFINITION_PATTERN.match(definition.replace("$", ""))
    if not match:
        return None
    tab, cell_ref = match.group(1), match.group(2).split(":")[0]
    return tab, cell_ref


def _resolve_named_range_value(definition: str, cells: dict) -> Optional[float]:
    split = _split_definition(definition)
    if split is None:
        return None
    tab, cell_ref = split
    record = cells.get(f"{tab}!{cell_ref}")
    if record is None:
        return None
    value = record.cached_value
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
    """Named ranges that resolve to different numbers in different tabs.

    SCOPE NARROWING, deliberate and visible: this compares named ranges only.
    The step also mentions "cell labels" appearing in multiple tabs, but
    identifying a label for a value requires an adjacency convention — is the
    label the cell to the left, above, in column A? — that has never been
    specified. Guessing one would produce findings whose meaning nobody agreed
    on. Left unimplemented rather than approximated.
    """
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


# ---------------------------------------------------------------------------
# 3. excluded rows in sums
# ---------------------------------------------------------------------------


def _detect_excluded_sum_rows(parsed_file: ParsedFile) -> list[AnomalyFinding]:
    """A SUM built from several ranges in one column that skips rows between them.

    =SUM(A1:A5,A7:A10) omits A6. That may be deliberate, but it is invisible on
    the face of the spreadsheet, which is what makes it worth surfacing.
    """
    findings = []
    for tab, cell_ref, formula in _formulas(parsed_file):
        for sum_match in _SUM_PATTERN.finditer(formula):
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
                            raw_value=formula,
                        )
                    )
    return findings


# ---------------------------------------------------------------------------
# 4. circular references — cell level only
# ---------------------------------------------------------------------------


def _detect_circular_references(parsed_file: ParsedFile) -> list[AnomalyFinding]:
    """Genuine cycles in the cell-level dependency graph.

    Reads `cell_dependency_graph` and never `tab_dependency_graph`. The two
    answer different questions, and substituting the coarse one produces false
    blockers on workbooks that are perfectly sound: Tab A reading one cell from
    Tab B while Tab B reads a different cell from Tab A is a tab-level cycle and
    a cell-level nothing.

    Every cell in the cycle is listed, because "there is a circular reference
    somewhere in this workbook" is not something a human can act on.
    """
    findings = []
    graph = nx.DiGraph(parsed_file.cell_dependency_graph)

    # Deterministic ordering so the same workbook always produces the same
    # finding_ids. No cap on the number of cycles: dropping findings to keep a
    # list short would hide exactly what this check exists to surface.
    cycles = sorted(nx.simple_cycles(graph), key=sorted)

    for cycle in cycles:
        path = cycle + [cycle[0]]
        tab = cycle[0].split("!", 1)[0] if "!" in cycle[0] else ""
        findings.append(
            AnomalyFinding(
                finding_id="",
                severity="blocker",
                tab=tab,
                cell_ref=cycle[0].split("!", 1)[-1],
                description=(
                    f"Circular reference between cells: {' -> '.join(path)}"
                    if len(cycle) > 1
                    else f"Cell refers to itself: {cycle[0]}"
                ),
                raw_value=" -> ".join(path),
            )
        )
    return findings
