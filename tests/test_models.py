"""Instantiates every model in core/models.py with dummy data to confirm the shapes hold together."""

from datetime import datetime

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


def test_file_context():
    FileContext(
        filename="reserves_q4.xlsx",
        description="Q4 reserve calculation workbook",
        user_role="actuary",
        uploaded_at=datetime.now(),
    )


def test_reference_figures():
    ReferenceFigures(
        source_label="Q4 trial balance extract",
        line_items={"Net premium reserves": 1_250_000.0},
        uploaded_at=datetime.now(),
    )


def test_parsed_file():
    ParsedFile(
        tab_names=["Reserves", "Assumptions"],
        cells={"Reserves!A1": "1250000", "Reserves!B1": "=A1*1.02"},
        cached_values={"Reserves!A1": 1250000, "Reserves!B1": 1275000},
        named_ranges={"NetReserves": "Reserves!A1"},
        external_links=[],
        has_vba=False,
        dependency_graph={"Reserves!B1": ["Reserves!A1"]},
        warnings=[],
    )


def test_anomaly_finding():
    AnomalyFinding(
        finding_id="F-001",
        severity="warning",
        tab="Reserves",
        cell_ref="B1",
        description="Hardcoded growth assumption embedded in formula",
        raw_value="=A1*1.02",
    )
    AnomalyFinding(
        finding_id="F-002",
        severity="blocker",
        tab="Reserves",
        cell_ref="C1",
        description="Circular reference",
        raw_value="=C1+1",
        human_decision="overridden",
        human_reason="Known false positive, confirmed with actuary",
        decided_by="apoorva",
        decided_at=datetime.now(),
    )


def test_reconciliation_line_excel_vs_python():
    line = ReconciliationLine(
        check_type="excel_vs_python",
        label="Net premium reserves",
        source_value=1_250_000.0,
        target_value=1_250_000.0,
        delta=0.0,
        delta_pct=0.0,
        verdict="pass",
        materiality_threshold=1000.0,
    )
    assert line.check_type == "excel_vs_python"


def test_reconciliation_line_python_vs_accounts():
    line = ReconciliationLine(
        check_type="python_vs_accounts",
        label="Net premium reserves",
        source_value=1_250_000.0,
        target_value=1_249_500.0,
        delta=500.0,
        delta_pct=0.04,
        verdict="warn",
        materiality_threshold=1000.0,
    )
    assert line.check_type == "python_vs_accounts"


def test_traceability_entry():
    TraceabilityEntry(
        report_figure_label="Net premium reserves",
        report_value=1_250_000.0,
        source_tab="Reserves",
        source_cell="B1",
        source_formula="=A1*1.02",
        derivation_note="Base reserve grown by 2% assumption in B1",
    )


def test_tab_documentation():
    TabDocumentation(
        tab_name="Reserves",
        method_summary="Applies a flat 2% growth assumption to base reserves",
        assumptions=["2% annual growth"],
        data_sources=["Q4 trial balance extract"],
        anomalies_noted=["F-001"],
        role_notes="CRO should confirm growth assumption is still current",
    )


def test_audit_report():
    file_context = FileContext(
        filename="reserves_q4.xlsx",
        description="Q4 reserve calculation workbook",
        user_role="actuary",
        uploaded_at=datetime.now(),
    )
    reference_figures = ReferenceFigures(
        source_label="Q4 trial balance extract",
        line_items={"Net premium reserves": 1_250_000.0},
        uploaded_at=datetime.now(),
    )
    parsed_file = ParsedFile(
        tab_names=["Reserves"],
        cells={"Reserves!A1": "1250000"},
        cached_values={"Reserves!A1": 1250000},
        named_ranges={},
        external_links=[],
        has_vba=False,
        dependency_graph={},
        warnings=[],
    )
    finding = AnomalyFinding(
        finding_id="F-001",
        severity="info",
        tab="Reserves",
        cell_ref="A1",
        description="Base reserve figure",
        raw_value="1250000",
    )
    internal_line = ReconciliationLine(
        check_type="excel_vs_python",
        label="Net premium reserves",
        source_value=1_250_000.0,
        target_value=1_250_000.0,
        delta=0.0,
        delta_pct=0.0,
        verdict="pass",
        materiality_threshold=1000.0,
    )
    external_line = ReconciliationLine(
        check_type="python_vs_accounts",
        label="Net premium reserves",
        source_value=1_250_000.0,
        target_value=1_249_500.0,
        delta=500.0,
        delta_pct=0.04,
        verdict="warn",
        materiality_threshold=1000.0,
    )
    traceability_entry = TraceabilityEntry(
        report_figure_label="Net premium reserves",
        report_value=1_250_000.0,
        source_tab="Reserves",
        source_cell="A1",
        source_formula=None,
        derivation_note="Directly read from base reserve cell",
    )
    documentation = TabDocumentation(
        tab_name="Reserves",
        method_summary="Base reserve figures, no adjustments",
        assumptions=[],
        data_sources=["Q4 trial balance extract"],
        anomalies_noted=[],
        role_notes="",
    )

    report = AuditReport(
        file_context=file_context,
        reference_figures=reference_figures,
        parsed_file=parsed_file,
        findings=[finding],
        reconciliation=[internal_line, external_line],
        unmatched_reference_items=[],
        traceability_index=[traceability_entry],
        documentation=[documentation],
        overall_verdict="warn",
        internal_verdict="pass",
        external_verdict="warn",
        signed_by=None,
        signed_at=None,
        report_id="RPT-2026-08-03-001",
    )

    assert report.internal_verdict != report.external_verdict
    check_types = {line.check_type for line in report.reconciliation}
    assert check_types == {"excel_vs_python", "python_vs_accounts"}
