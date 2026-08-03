"""Tests for report/generator.py: PDF rendering via Jinja2 + weasyprint, and the sign-off guard."""

from datetime import datetime, timezone

import pytest

from core.models import (
    AnomalyFinding,
    AuditReport,
    FileContext,
    ParsedFile,
    ReconciliationLine,
    ReferenceFigures,
    TabDocumentation,
    TraceabilityEntry,
)
from report.generator import generate_report_pdf


def _file_context() -> FileContext:
    return FileContext(
        filename="reserves_q4.xlsx",
        description="Q4 reserve calculation workbook",
        user_role="actuary",
        uploaded_at=datetime.now(),
    )


def _parsed_file() -> ParsedFile:
    return ParsedFile(
        tab_names=["Reserves"],
        cells={"Reserves!B2": "=B1*1.25"},
        cached_values={"Reserves!B1": 1000, "Reserves!B2": 1250},
        named_ranges={},
        external_links=[],
        has_vba=False,
        dependency_graph={"Reserves": []},
        warnings=[],
    )


def _signed_report(**overrides) -> AuditReport:
    defaults = dict(
        file_context=_file_context(),
        reference_figures=ReferenceFigures(
            source_label="Q4 trial balance extract",
            line_items={"Net premium reserves": 1250.0},
            uploaded_at=datetime.now(),
        ),
        parsed_file=_parsed_file(),
        findings=[
            AnomalyFinding(
                finding_id="F0001",
                severity="warning",
                tab="Reserves",
                cell_ref="B2",
                description="Hardcoded literal 1.25 embedded in formula",
                raw_value="=B1*1.25",
                human_decision="confirmed",
                human_reason="Known growth assumption",
                decided_by="apoorva",
                decided_at=datetime.now(),
            )
        ],
        reconciliation=[
            ReconciliationLine(
                check_type="excel_vs_python",
                label="Net premium reserves",
                source_value=1250.0,
                target_value=1250.0,
                delta=0.0,
                delta_pct=0.0,
                verdict="pass",
                materiality_threshold=0.01,
                source_cell="Reserves!B2",
            ),
            ReconciliationLine(
                check_type="python_vs_accounts",
                label="Net premium reserves",
                source_value=1250.0,
                target_value=1255.0,
                delta=5.0,
                delta_pct=0.004,
                verdict="warn",
                materiality_threshold=0.01,
                source_cell="Reserves!B2",
                match_note="Ambiguous match (72% similarity)...",
            ),
        ],
        unmatched_reference_items=["Unrelated currency adjustment"],
        traceability_index=[
            TraceabilityEntry(
                report_figure_label="Net premium reserves",
                report_value=1250.0,
                source_tab="Reserves",
                source_cell="B2",
                source_formula="=B1*1.25",
                derivation_note="Read directly from cell (Excel's cached formula result).",
            ),
            TraceabilityEntry(
                report_figure_label="Net premium reserves",
                report_value=1255.0,
                source_tab=None,
                source_cell=None,
                source_formula=None,
                derivation_note="External reference figure supplied by the user, not sourced from the workbook.",
            ),
        ],
        documentation=[
            TabDocumentation(
                tab_name="Reserves",
                method_summary="Applies a 1.25x growth factor to the base reserve.",
                assumptions=["25% growth factor"],
                data_sources=["Q4 trial balance"],
                anomalies_noted=["F0001"],
                role_notes="Actuary should confirm the growth factor is current.",
            )
        ],
        overall_verdict="warn",
        internal_verdict="pass",
        external_verdict="warn",
        signed_by="Apoorva Ranjan",
        signed_at=datetime.now(timezone.utc),
        signed_role="cfo",
        report_id="RPT-001",
    )
    defaults.update(overrides)
    return AuditReport(**defaults)


def _decisions() -> list[dict]:
    return [
        {
            "report_id": "RPT-001",
            "gate_number": 1,
            "finding_id": None,
            "action": "context_confirmed",
            "reason": "Confirmed context for 'reserves_q4.xlsx'",
            "user_name": "apoorva",
            "timestamp": "2026-08-03T10:00:00",
        },
        {
            "report_id": "RPT-001",
            "gate_number": 4,
            "finding_id": None,
            "action": "signed_off",
            "reason": "Signed off by Apoorva Ranjan (cfo)",
            "user_name": "Apoorva Ranjan",
            "timestamp": "2026-08-03T10:05:00",
        },
    ]


def test_generates_valid_pdf_bytes_from_a_signed_report():
    pdf_bytes = generate_report_pdf(_signed_report(), _decisions())

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes.startswith(b"%PDF")


def test_generates_valid_pdf_when_no_reference_figures_were_provided():
    report = _signed_report(
        reference_figures=None,
        reconciliation=[
            line
            for line in _signed_report().reconciliation
            if line.check_type == "excel_vs_python"
        ],
        unmatched_reference_items=[],
        external_verdict="pass",
    )

    pdf_bytes = generate_report_pdf(report, _decisions())

    assert pdf_bytes.startswith(b"%PDF")


def test_raises_if_report_is_not_signed():
    unsigned = _signed_report(signed_by=None, signed_at=None, signed_role=None)

    with pytest.raises(ValueError):
        generate_report_pdf(unsigned, _decisions())


def test_generates_valid_pdf_with_empty_decisions_and_no_findings():
    report = _signed_report(findings=[])

    pdf_bytes = generate_report_pdf(report, [])

    assert pdf_bytes.startswith(b"%PDF")
