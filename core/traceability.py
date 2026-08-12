"""Builds the traceability index — where every figure in the report came from.

There is no value matching anywhere in this module, and that absence is the
point. The original design located a figure's source by searching the workbook
for a cell holding the same number, which picks the wrong cell whenever a value
repeats — and zeros, round numbers, and coincidental equalities are ordinary in
a real workbook. Every entry here is built from a chain something upstream
already constructed while it knew exactly where the number came from.

`trace_status` carries a specific reason rather than a yes/no. When an auditor
hits a gap in the index, "there is no trace" is not the useful fact; "the
mapping exists but nobody has approved it" is.
"""

from typing import Optional

from core.accounting import signed_reference_amount
from core.models import (
    AccountingProvenance,
    AccountMapping,
    AnomalyFinding,
    DerivationStep,
    ParsedFile,
    ReconciliationLine,
    ReconciliationResult,
    ReferenceFigures,
    TraceabilityEntry,
)


def build_traceability_index(
    parsed_file: ParsedFile,
    result: ReconciliationResult,
    findings: list[AnomalyFinding],
    reference_figures: Optional[ReferenceFigures] = None,
) -> list[TraceabilityEntry]:
    """One entry per figure the report depends on. Nothing is ever omitted."""
    entries: list[TraceabilityEntry] = []
    mappings_by_id = {mapping.mapping_id: mapping for mapping in result.mappings}
    reference_lines_by_id = (
        {line.line_id: line for line in reference_figures.lines} if reference_figures else {}
    )

    for line in result.lines:
        if line.check_type == "excel_vs_python":
            entries.append(_excel_entry(line))
        else:
            entries.append(_accounts_entry(line, mappings_by_id, reference_lines_by_id))

    for line_id in result.unmatched_reference_items:
        reference_line = reference_lines_by_id.get(line_id)
        entries.append(
            TraceabilityEntry(
                report_figure_label=(
                    f"{reference_line.label} ({line_id})" if reference_line else line_id
                ),
                report_value=(
                    signed_reference_amount(reference_line) if reference_line else None
                ),
                derivation=[],
                accounting_provenance=None,
                # The ledger side of the same gap Step 7 recorded in
                # unmatched_reference_items, made visible here too.
                trace_status="unmapped",
            )
        )

    for finding in findings:
        entries.append(_finding_entry(finding, parsed_file))

    return entries


def _excel_entry(line: ReconciliationLine) -> TraceabilityEntry:
    """A reconstruction chain is already a trace. Nothing more is needed.

    A partial chain is included in full, with its unsupported nodes visible as
    is_supported=False rather than dropped — a reviewer needs to see how far the
    reconstruction got and precisely where it stopped.
    """
    return TraceabilityEntry(
        report_figure_label=line.label,
        report_value=line.target_value if line.target_value is not None else line.source_value,
        derivation=line.derivation,
        accounting_provenance=None,
        trace_status="traced" if line.completeness == "complete" else "partially_traced",
    )


def _accounts_entry(
    line: ReconciliationLine,
    mappings_by_id: dict[str, AccountMapping],
    reference_lines_by_id: dict,
) -> TraceabilityEntry:
    """The accounting side, which needs its own provenance to point at.

    Checked in precedence order, stopping at the first that applies, so each
    gap reports the specific reason it is a gap.
    """
    entry = TraceabilityEntry(
        report_figure_label=line.label,
        report_value=line.target_value,
        derivation=line.derivation,
        accounting_provenance=None,
        trace_status="not_traceable",
    )

    if line.mapping_id is None:
        entry.trace_status = "unmapped"
        return entry

    mapping = mappings_by_id.get(line.mapping_id)
    if mapping is None:
        # A line naming a mapping that isn't in the result. Recorded as a
        # specific fact rather than quietly dropped.
        entry.trace_status = "not_traceable"
        return entry

    if not mapping.is_approved:
        entry.trace_status = "mapping_pending_approval"
        return entry

    if mapping.mapping_type != "one_to_one":
        # Aggregation is explicitly not computed by this tool, so an approved
        # aggregate mapping still cannot produce a traced figure.
        entry.trace_status = "mapping_rejected"
        return entry

    reference_line = reference_lines_by_id.get(mapping.reference_line_id)
    if reference_line is None:
        entry.trace_status = "not_traceable"
        return entry

    # Built from the two objects that were actually used, never a fresh lookup.
    entry.accounting_provenance = AccountingProvenance(
        reference_line_id=reference_line.line_id,
        account_number=reference_line.account_number,
        ledger_source=reference_line.ledger_source,
        entity=reference_line.entity,
        period=reference_line.period,
        currency=reference_line.currency,
        evidence_ref=reference_line.evidence_ref,
        mapping_id=mapping.mapping_id,
        approved_by=mapping.approved_by or "",
    )
    entry.trace_status = "traced"
    return entry


def _finding_entry(finding: AnomalyFinding, parsed_file: ParsedFile) -> TraceabilityEntry:
    """A finding already knows its exact cell. No reconstruction is involved."""
    full_ref = f"{finding.tab}!{finding.cell_ref}" if finding.cell_ref else finding.tab
    record = parsed_file.cells.get(full_ref)

    return TraceabilityEntry(
        report_figure_label=f"Finding {finding.finding_id}: {finding.description}",
        report_value=None,
        derivation=[
            DerivationStep(
                cell_ref=full_ref,
                formula=record.formula if record else None,
                depends_on=list(parsed_file.cell_dependency_graph.get(full_ref, [])),
                resolved_value=None,
                is_supported=True,
            )
        ],
        accounting_provenance=None,
        trace_status="traced",
    )
