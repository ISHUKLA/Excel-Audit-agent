"""Step 10 tests for HTML contracts, terminology, separation, and PDF bytes."""

import json
from datetime import datetime, timezone

import pytest

from core.models import (
    AccountingProvenance,
    AccountMapping,
    AnomalyFinding,
    AuditReport,
    CellRecord,
    DerivationStep,
    FileContext,
    LLMDataManifestEntry,
    ParsedFile,
    ReconciliationLine,
    ReferenceFigureLine,
    ReferenceFigures,
    TabDocumentation,
    TraceabilityEntry,
    WorkbookMeta,
)
from report.generator import generate_report_pdf, render_report_html

GENERATED_AT = datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc)
APPROVED_AT = datetime(2026, 8, 11, 10, 15, tzinfo=timezone.utc)
WORKBOOK_HASH = "a1" * 32


def _file_context() -> FileContext:
    return FileContext(
        filename="reserves_q4.xlsx",
        description="Q4 reserve calculation workbook",
        user_role="cfo",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        basis="IFRS 17",
        uploaded_at=GENERATED_AT,
    )


def _parsed_file() -> ParsedFile:
    return ParsedFile(
        tab_names=["Reserves", "Assumptions"],
        cells={
            "Reserves!B1": CellRecord(
                cell_ref="Reserves!B1",
                cached_value=1000.0,
                data_type="number",
                number_format="#,#00.00",
                is_error=False,
                is_stale=False,
            ),
            "Reserves!B2": CellRecord(
                cell_ref="Reserves!B2",
                formula="=B1*1.25",
                cached_value=1250.0,
                data_type="number",
                number_format="#,#00.00",
                is_error=False,
                is_stale=False,
            ),
        },
        named_ranges={},
        external_links=[],
        has_vba=False,
        workbook_meta=WorkbookMeta(
            calc_mode="automatic",
            workbook_hash=WORKBOOK_HASH,
            app_version="Microsoft Excel",
            fully_calculated_on_load=True,
        ),
        tab_dependency_graph={"Reserves": [], "Assumptions": []},
        cell_dependency_graph={"Reserves!B2": ["Reserves!B1"], "Reserves!B1": []},
        warnings=[],
    )


def _references() -> ReferenceFigures:
    return ReferenceFigures(
        source_label="Q4 trial balance extract",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        basis="IFRS 17",
        control_total=1750.0,
        control_total_confirmed_by_human=False,
        lines=[
            ReferenceFigureLine(
                line_id="GL-001",
                account_number="3000",
                label="Approved reserve mapping",
                entity="Acme Life SA",
                period="2025-Q4",
                currency="EUR",
                ledger_source="SAP FI Q4 close",
                debit_credit="credit",
                amount=1255.0,
                evidence_ref="trial-balance.csv row 2",
            ),
            ReferenceFigureLine(
                line_id="GL-002",
                account_number="3001",
                label="Proposed reserve mapping",
                entity="Acme Life SA",
                period="2025-Q4",
                currency="EUR",
                ledger_source="SAP FI Q4 close",
                debit_credit="credit",
                amount=495.0,
                evidence_ref="trial-balance.csv row 3",
            ),
        ],
        uploaded_at=GENERATED_AT,
    )


def _derivation() -> list[DerivationStep]:
    return [
        DerivationStep(
            cell_ref="Reserves!B2",
            formula="=B1*1.25",
            depends_on=["Reserves!B1"],
            resolved_value=1250.0,
            is_supported=True,
        ),
        DerivationStep(
            cell_ref="Reserves!B1",
            formula=None,
            depends_on=[],
            resolved_value=1000.0,
            is_supported=True,
        ),
    ]


def _line(**overrides) -> ReconciliationLine:
    values = dict(
        check_type="excel_vs_python",
        label="Internal reserve output",
        source_value=1250.0,
        target_value=1250.0,
        delta=0.0,
        delta_pct=0.0,
        verdict="pass",
        pct_threshold=0.01,
        absolute_threshold=100.0,
        threshold_is_default=True,
        completeness="complete",
        reconstruction_coverage_pct=100.0,
        unsupported_elements=[],
        derivation=_derivation(),
        mapping_id=None,
    )
    return ReconciliationLine(**{**values, **overrides})


def _mapping(mapping_id, line_id, approved, **overrides) -> AccountMapping:
    values = dict(
        mapping_id=mapping_id,
        python_output_cell_ref="Reserves!B2",
        reference_line_id=line_id,
        mapping_type="one_to_one",
        suggested_by="fuzzy_match",
        suggested_confidence=96.0 if approved else 72.0,
        approved_by="Header Approver" if approved else None,
        approved_at=APPROVED_AT if approved else None,
        is_approved=approved,
    )
    return AccountMapping(**{**values, **overrides})


def _report(**overrides) -> AuditReport:
    approved_mapping = _mapping("MAP-001", "GL-001", True)
    proposed_mapping = _mapping("MAP-002", "GL-002", False)
    internal = _line()
    approved_external = _line(
        check_type="python_vs_accounts",
        label="Approved reserve mapping",
        target_value=1255.0,
        delta=5.0,
        delta_pct=5.0 / 1255.0,
        verdict="warn",
        mapping_id="MAP-001",
    )
    proposed_external = _line(
        check_type="python_vs_accounts",
        label="Proposed reserve mapping",
        source_value=500.0,
        target_value=495.0,
        delta=5.0,
        delta_pct=0.01,
        verdict="incomplete",
        mapping_id="MAP-002",
    )
    defaults = dict(
        file_context=_file_context(),
        reference_figures=_references(),
        authoritative_outputs=["Reserves!B2"],
        parsed_file=_parsed_file(),
        findings=[
            AnomalyFinding(
                finding_id="F0001",
                severity="warning",
                tab="Reserves",
                cell_ref="B2",
                description="Hardcoded literal in formula",
                raw_value="=B1*1.25",
                human_decision="overridden",
                human_reason="Documented management assumption",
                decided_by="Reviewer One",
                decided_at=GENERATED_AT,
            )
        ],
        mappings=[approved_mapping, proposed_mapping],
        reconciliation=[internal, approved_external, proposed_external],
        unmatched_reference_items=["GL-099"],
        unmapped_python_outputs=["Reserves!C9"],
        context_match_verdict="match",
        traceability_index=[
            TraceabilityEntry(
                report_figure_label="Approved reserve mapping",
                report_value=1255.0,
                derivation=_derivation(),
                accounting_provenance=AccountingProvenance(
                    reference_line_id="GL-001",
                    account_number="3000",
                    ledger_source="SAP FI Q4 close",
                    entity="Acme Life SA",
                    period="2025-Q4",
                    currency="EUR",
                    evidence_ref="trial-balance.csv row 2",
                    mapping_id="MAP-001",
                    approved_by="Header Approver",
                ),
                trace_status="traced",
            )
        ],
        documentation=[
            TabDocumentation(
                tab_name="Reserves",
                method_summary="Applies a growth factor to a base reserve.",
                assumptions=["Growth factor supplied in cell B2"],
                data_sources=["Reserves!B1"],
                anomalies_noted=["F0001"],
                role_notes="Tie the output to the provision account.",
            )
        ],
        llm_data_manifest=[
            LLMDataManifestEntry(
                tab_name="Reserves",
                cell_refs_included=["Reserves!B1", "Reserves!B2"],
                cell_refs_excluded=["Reserves!A9"],
                exclusion_reasons={
                    "Reserves!A9": "free text over length threshold, possible PII"
                },
                sent_at=GENERATED_AT,
                prompt_char_count=240,
            )
        ],
        translation_and_reconciliation_verdict="warn",
        internal_verdict="pass",
        external_verdict="warn",
        workbook_hash=WORKBOOK_HASH,
        code_version="commit-abc123-dirty",
        validation_run_id="RUN-2026-0811",
        disclaimer=(
            "This report describes translation and reconciliation procedures. "
            "It does not constitute actuarial validation of the underlying model."
        ),
        independence_disclosure=(
            "The preparer and the approver were the same individual for this report. "
            "No independent review was performed."
        ),
        report_approval_name="Header Approver",
        report_approval_at=APPROVED_AT,
        report_approval_role="Financial Controller",
        generated_at=GENERATED_AT,
        report_id="RPT-2026-001",
        audit_log_verification_note=(
            "This report's underlying evidence trail is hash-chained in a local, "
            "tamper-evident log. Re-run verify_chain() against audit.db."
        ),
    )
    return AuditReport(**{**defaults, **overrides})


def _audit_rows() -> list[dict]:
    return [
        {
            "row_id": 1,
            "event_type": "gate_decision",
            "payload_json": json.dumps(
                {"gate": 3, "action": "reconciliation_reviewed", "acknowledge_incomplete": True}
            ),
            "actor": "Reviewer One",
            "timestamp": "2026-08-11T10:00:00+00:00",
            "row_hash": "c" * 64,
        },
        {
            "row_id": 2,
            "event_type": "report_approved",
            "payload_json": json.dumps({"gate": 4, "action": "approval_record_created"}),
            "actor": "Header Approver",
            "timestamp": "2026-08-11T10:15:00+00:00",
            "row_hash": "d" * 64,
        },
    ]


def test_generates_valid_pdf_bytes_from_an_approved_report():
    pdf_bytes = generate_report_pdf(_report(), _audit_rows())

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 1000
    assert pdf_bytes.startswith(b"%PDF")


def test_incomplete_report_has_coverage_explanation_and_no_capitalized_validation_terms():
    partial = _line(
        target_value=None,
        delta=None,
        delta_pct=None,
        verdict="incomplete",
        completeness="partial",
        reconstruction_coverage_pct=62.5,
        unsupported_elements=["Reserves!B2 uses VLOOKUP (unsupported)"],
    )
    report = _report(
        reconciliation=[partial],
        translation_and_reconciliation_verdict="incomplete",
        internal_verdict="incomplete",
        external_verdict="not_performed",
        reference_figures=None,
        mappings=[],
        unmatched_reference_items=[],
        unmapped_python_outputs=[],
    )

    html = render_report_html(report, _audit_rows())

    assert "37.5% of this calculation could not be reconstructed" in html
    assert "Validated" not in html
    assert "Validation" not in html
    assert "not comparable" in html


def test_report_language_uses_only_bounded_approval_and_integrity_terms():
    html = render_report_html(_report(), _audit_rows()).lower()

    assert "signed by" not in html
    assert "attested by" not in html
    assert "tamper-proof" not in html
    assert "named approval record" in html
    assert "tamper-evident" in html


def test_approved_and_proposed_mappings_are_separate_and_mismatch_is_prominent():
    html = render_report_html(_report(), _audit_rows())
    approved_start = html.index('id="approved-mappings-table"')
    approved_block = html[approved_start : html.index("</table>", approved_start)]
    proposed_start = html.index('id="proposed-mappings-table"')
    proposed_block = html[proposed_start : html.index("</table>", proposed_start)]

    assert "For CFO review" in html
    assert "Proposed but not yet approved" in html
    assert "Approved reserve mapping" in approved_block
    assert "Proposed reserve mapping" not in approved_block
    assert "Proposed reserve mapping" in proposed_block

    mismatch_html = render_report_html(
        _report(context_match_verdict="mismatch"),
        _audit_rows(),
    )
    assert 'id="context-mismatch-banner"' in mismatch_html
    assert "cannot be relied upon" in mismatch_html
    assert 'id="context-mismatch-banner"' not in html


def test_header_contract_and_findings_wording_match_the_models():
    html = render_report_html(_report(), _audit_rows())

    assert GENERATED_AT.isoformat() in html
    assert "Header Approver" in html
    assert "Financial Controller" in html
    findings_start = html.index('id="section-2-findings"')
    findings_end = html.index('id="section-3a-internal"')
    findings_block = html[findings_start:findings_end].lower()
    assert "reviewed and dispositioned" in findings_block
    assert "approved" not in findings_block


def test_no_reference_figures_is_explicit_not_silently_omitted():
    report = _report(
        reference_figures=None,
        mappings=[],
        reconciliation=[_line()],
        unmatched_reference_items=[],
        unmapped_python_outputs=[],
        external_verdict="not_performed",
    )

    html = render_report_html(report, _audit_rows())

    assert html.count("Not performed - no reference figures provided") >= 2


def test_pdf_generation_requires_a_complete_named_approval_record():
    report = _report(
        report_approval_name=None,
        report_approval_at=None,
        report_approval_role=None,
    )

    with pytest.raises(ValueError, match="named approval record"):
        generate_report_pdf(report, _audit_rows())
