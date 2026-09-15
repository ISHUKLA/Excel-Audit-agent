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

from agents.parser import _CELL_REF_PATTERN, _expand_range, _normalize
from core.formula_catalogue import FUNCTION_ARG_SPECS
from core.models import AnomalyFinding, ParsedFile

_SEVERITY_RANK = {"blocker": 0, "warning": 1, "info": 2}

# A bare number in a formula, not part of a cell reference: the "1" in A1 is
# preceded by a letter, the "10" in B10 likewise, so neither matches.
_LITERAL_PATTERN = re.compile(r"(?<![A-Za-z0-9_$])(\d+\.?\d*)(?![A-Za-z0-9_])")
_ALLOWED_LITERALS = {0.0, 1.0, 100.0}

# Argument roles (from core/formula_catalogue.py's FUNCTION_ARG_SPECS) that
# describe HOW to compute rather than WHAT business number to use. A digit
# count or significance is structural to the formula the same way a cell
# reference is — flagging ROUND(C1, 2)'s "2" as a hardcoded assumption is a
# false positive of exactly the kind that trains a reviewer to stop reading
# findings. "value", "criteria", "condition", "flag", and "range" are
# deliberately NOT in this set: a criteria threshold like SUMIF's ">1000" can
# itself be a hardcoded business assumption worth surfacing, so only the
# roles that are unambiguously structural in every function that uses them
# are excluded here.
_STRUCTURAL_ARG_ROLES = {"digit_count", "significance", "index"}
_FUNCTION_CALL_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*$")

_SUM_PATTERN = re.compile(r"SUM\(([^()]*)\)", re.IGNORECASE)
_RANGE_PATTERN = re.compile(r"^([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)$")

_DEFINITION_PATTERN = re.compile(r"^'?([^'!]+)'?!(.+)$")


def _references_cell(
    formula: str, *, own_tab: str, target_tab: str, target_cell: str
) -> bool:
    """Whether ``formula`` refers to one exact cell, directly or in a range."""
    normalized_target = _normalize(target_cell)
    for quoted, plain, start, end in _CELL_REF_PATTERN.findall(formula):
        referenced_tab = quoted or plain or own_tab
        if referenced_tab != target_tab:
            continue
        normalized_start = _normalize(start)
        if not end and normalized_start == normalized_target:
            return True
        if end:
            expanded = _expand_range(normalized_start, _normalize(end)) or []
            if normalized_target in expanded:
                return True
    return False


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


def _iter_function_calls(formula: str) -> list[tuple[str, int, int, str]]:
    """Every function call in `formula`, at every nesting depth, as
    (NAME, args_start, args_end, args_text) — found by explicit paren
    matching rather than a regex per nesting level, so ROUND nested inside
    SUM is found exactly the same way a top-level ROUND would be.

    `args_start`/`args_end` are offsets into the ORIGINAL formula string, so
    a literal's own match offset can be tested against them directly.
    """
    calls: list[tuple[str, int, int, str]] = []
    stack: list[tuple[int, Optional[str]]] = []
    for i, ch in enumerate(formula):
        if ch == "(":
            name_match = _FUNCTION_CALL_NAME_PATTERN.search(formula[:i])
            name = name_match.group(0) if name_match else None
            stack.append((i + 1, name))
        elif ch == ")" and stack:
            args_start, name = stack.pop()
            if name:
                calls.append((name.upper(), args_start, i, formula[args_start:i]))
    return calls


def _split_top_level_args(args_text: str, offset_base: int) -> list[tuple[int, int]]:
    """Absolute (start, end) spans for each top-level-comma-separated
    argument in `args_text`, which begins at `offset_base` in the formula
    this text was sliced from. A comma inside a nested function call's own
    arguments is not top-level and does not split anything here."""
    spans = []
    depth = 0
    start = 0
    for idx, ch in enumerate(args_text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            spans.append((offset_base + start, offset_base + idx))
            start = idx + 1
    spans.append((offset_base + start, offset_base + len(args_text)))
    return spans


def _structural_literal_spans(formula: str) -> list[tuple[int, int]]:
    """Offset spans of every literal position that falls inside a
    structural (not "value") argument of a known function call — the
    formula-catalogue-driven fix for the false positives a role-blind
    literal scan would otherwise produce on ROUND's digit count, CEILING's
    significance, and (once Group E lands) a lookup's column index.

    Calls are processed innermost-first (by argument-text length) so a
    literal inside a nested call is matched against ITS OWN enclosing
    call's argument roles, not an outer call's.
    """
    calls = sorted(_iter_function_calls(formula), key=lambda c: len(c[3]))
    structural_spans = []
    for name, args_start, args_end, args_text in calls:
        spec = FUNCTION_ARG_SPECS.get(name)
        if not spec:
            continue
        for index, (span_start, span_end) in enumerate(_split_top_level_args(args_text, args_start)):
            role = spec[index] if index < len(spec) else None
            if role in _STRUCTURAL_ARG_ROLES:
                structural_spans.append((span_start, span_end))
    return structural_spans


def _is_within_any_span(position: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def _detect_hardcoded_literals(parsed_file: ParsedFile) -> list[AnomalyFinding]:
    findings = []
    for tab, cell_ref, formula in _formulas(parsed_file):
        structural_spans = _structural_literal_spans(formula)
        for match in _LITERAL_PATTERN.finditer(formula):
            literal = float(match.group(1))
            if literal in _ALLOWED_LITERALS:
                continue
            if _is_within_any_span(match.start(), structural_spans):
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

    A second, deliberately narrow check covers a common end-of-range defect:
    a single SUM range whose next cell is a populated numeric row on the same
    source column. The ordinary total-below-detail pattern is excluded when
    that next cell is the formula cell itself. This is still a review finding,
    not a claim that the row must be included.
    """
    findings = []
    for tab, cell_ref, formula in _formulas(parsed_file):
        for sum_match in _SUM_PATTERN.finditer(formula):
            by_col: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
            parsed_ranges: list[tuple[str, str, int, int]] = []
            sum_arguments = sum_match.group(1).split(",")
            for arg in sum_arguments:
                cleaned = arg.strip().replace("$", "")
                source_tab = tab
                if "!" in cleaned:
                    sheet_part, cleaned = cleaned.rsplit("!", 1)
                    source_tab = sheet_part.strip("'").replace("''", "'")
                range_match = _RANGE_PATTERN.match(cleaned)
                if not range_match:
                    continue
                col_start, row_start, col_end, row_end = range_match.groups()
                if col_start.upper() == col_end.upper():
                    start, end = sorted((int(row_start), int(row_end)))
                    key = (source_tab, col_start.upper())
                    by_col[key].append((start, end))
                    parsed_ranges.append((source_tab, col_start.upper(), start, end))

            for (_, col), spans in by_col.items():
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

            # Exactly one argument and that argument is one contiguous range.
            # ``SUM(B8:B9,B11)`` is an explicit multi-argument selection, not
            # the silent single-range end-point pattern this rule covers.
            if len(sum_arguments) == 1 and len(parsed_ranges) == 1:
                source_tab, col, _, end = parsed_ranges[0]
                adjacent_cell = f"{col}{end + 1}"
                adjacent_ref = f"{source_tab}!{adjacent_cell}"
                if adjacent_ref == f"{tab}!{cell_ref}":
                    continue
                # A row outside this particular SUM is not omitted when the
                # surrounding formula uses it explicitly, e.g.
                # ``=control_total-SUM(detail_rows)``. Remove only the SUM
                # currently being assessed, then check the remaining formula.
                # This preserves the end-of-range finding for a genuinely
                # silent adjacent row without flagging an explicit control.
                outside_sum = formula[: sum_match.start()] + formula[sum_match.end() :]
                if _references_cell(
                    outside_sum,
                    own_tab=tab,
                    target_tab=source_tab,
                    target_cell=adjacent_cell,
                ):
                    continue
                record = parsed_file.cells.get(adjacent_ref)
                value = record.cached_value if record is not None else None
                is_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
                if record is not None and (is_numeric or record.formula is not None):
                    findings.append(
                        AnomalyFinding(
                            finding_id="",
                            severity="warning",
                            tab=tab,
                            cell_ref=cell_ref,
                            description=(
                                f"SUM range ends before adjacent populated row: "
                                f"{adjacent_ref} is not included"
                            ),
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
