"""Builds the cell-level traceability index an auditor can follow independently."""

import re

from core.models import AnomalyFinding, FileContext, ParsedFile, ReconciliationLine, TraceabilityEntry

_FIRST_CELL_REF = re.compile(
    r"(?:'([^']+)'!|([A-Za-z_][A-Za-z0-9_. ]*)!)?\$?([A-Za-z]{1,3})\$?(\d{1,7})"
)
_NUMERIC_LITERAL = re.compile(r"^-?[\d,]+\.?\d*%?$")


def build_traceability_index(
    parsed_file: ParsedFile,
    reconciliation: list[ReconciliationLine],
    findings: list[AnomalyFinding],
    file_context: FileContext,
) -> list[TraceabilityEntry]:
    entries: dict[tuple, TraceabilityEntry] = {}

    for line in reconciliation:
        _add_entry(entries, _trace_source_value(parsed_file, line))
        _add_entry(entries, _trace_target_value(parsed_file, line))

    for finding in findings:
        entry = _trace_finding(parsed_file, finding)
        if entry is not None:
            _add_entry(entries, entry)

    return list(entries.values())


def _add_entry(entries: dict[tuple, TraceabilityEntry], entry: TraceabilityEntry) -> None:
    key = (
        entry.source_tab,
        entry.source_cell,
        entry.report_figure_label if entry.source_cell is None else None,
        round(entry.report_value, 9),
    )
    entries.setdefault(key, entry)


def _trace_source_value(parsed_file: ParsedFile, line: ReconciliationLine) -> TraceabilityEntry:
    if line.check_type == "excel_vs_python":
        # Excel's own cached result for the formula cell -- a direct read.
        return _entry_for_cell(
            parsed_file,
            label=line.label,
            value=line.source_value,
            cell_key=line.source_cell,
            derivation_note="Read directly from cell (Excel's cached formula result).",
        )
    # python_vs_accounts: source_value is the Pass 1 Python reconstruction,
    # not a direct cell read -- trace to its primary input cell instead.
    return _entry_for_primary_input(
        parsed_file, label=line.label, value=line.source_value, formula_cell_key=line.source_cell
    )


def _trace_target_value(parsed_file: ParsedFile, line: ReconciliationLine) -> TraceabilityEntry:
    if line.check_type == "excel_vs_python":
        return _entry_for_primary_input(
            parsed_file, label=line.label, value=line.target_value, formula_cell_key=line.source_cell
        )
    # python_vs_accounts: target_value is an external reference figure,
    # never present in the workbook at all.
    return TraceabilityEntry(
        report_figure_label=line.label,
        report_value=line.target_value,
        source_tab=None,
        source_cell=None,
        source_formula=None,
        derivation_note="External reference figure supplied by the user, not sourced from the workbook.",
    )


def _entry_for_cell(
    parsed_file: ParsedFile, label: str, value: float, cell_key: str | None, derivation_note: str
) -> TraceabilityEntry:
    if not cell_key or "!" not in cell_key:
        return TraceabilityEntry(
            report_figure_label=label,
            report_value=value,
            source_tab=None,
            source_cell=None,
            source_formula=None,
            derivation_note="No source cell could be identified for this figure.",
        )
    tab, cell_ref = cell_key.split("!", 1)
    formula = parsed_file.cells.get(cell_key)
    formula_text = formula if isinstance(formula, str) and formula.startswith("=") else None
    return TraceabilityEntry(
        report_figure_label=label,
        report_value=value,
        source_tab=tab,
        source_cell=cell_ref,
        source_formula=formula_text,
        derivation_note=derivation_note,
    )


def _entry_for_primary_input(
    parsed_file: ParsedFile, label: str, value: float, formula_cell_key: str | None
) -> TraceabilityEntry:
    if not formula_cell_key or "!" not in formula_cell_key:
        return TraceabilityEntry(
            report_figure_label=label,
            report_value=value,
            source_tab=None,
            source_cell=None,
            source_formula=None,
            derivation_note="No source cell could be identified for this computed figure.",
        )

    tab, cell_ref = formula_cell_key.split("!", 1)
    formula = parsed_file.cells.get(formula_cell_key)

    if isinstance(formula, str) and formula.startswith("="):
        match = _FIRST_CELL_REF.search(formula[1:])
        if match:
            quoted_tab, unquoted_tab, col, row = match.groups()
            input_tab = quoted_tab or unquoted_tab or tab
            return TraceabilityEntry(
                report_figure_label=label,
                report_value=value,
                source_tab=input_tab,
                source_cell=f"{col.upper()}{row}",
                source_formula=formula,
                derivation_note=(
                    f"Computed as {formula} (see Agent 3 reconstruction); cell shown is its "
                    f"primary input, not a direct read."
                ),
            )

    return TraceabilityEntry(
        report_figure_label=label,
        report_value=value,
        source_tab=tab,
        source_cell=cell_ref,
        source_formula=formula if isinstance(formula, str) else None,
        derivation_note=(
            "Computed in Python; no formula was found on its originating cell to identify a "
            "primary input."
        ),
    )


def _trace_finding(parsed_file: ParsedFile, finding: AnomalyFinding) -> TraceabilityEntry | None:
    raw = finding.raw_value.strip()
    if raw.startswith("=") or not _NUMERIC_LITERAL.match(raw):
        return None  # not a single traceable figure (a formula, error code, or non-numeric note)

    value = float(raw.replace(",", "").replace("%", ""))
    cell_key = f"{finding.tab}!{finding.cell_ref}"
    formula = parsed_file.cells.get(cell_key)

    return TraceabilityEntry(
        report_figure_label=finding.description,
        report_value=value,
        source_tab=finding.tab,
        source_cell=finding.cell_ref,
        source_formula=formula if isinstance(formula, str) and formula.startswith("=") else None,
        derivation_note="Read directly from cell.",
    )
