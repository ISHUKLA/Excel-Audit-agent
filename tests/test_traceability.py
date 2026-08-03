"""Tests for core/traceability.py: cell-level index construction on clean and messy workbooks."""

from datetime import datetime

from core.models import AnomalyFinding, FileContext, ParsedFile, ReconciliationLine
from core.traceability import build_traceability_index


def _parsed_file() -> ParsedFile:
    return ParsedFile(
        tab_names=["Reserves"],
        cells={
            "Reserves!A2": "Net premium reserves",
            "Reserves!B1": 1000,
            "Reserves!B2": "=B1*1.25",
        },
        cached_values={
            "Reserves!B1": 1000,
            "Reserves!B2": 1250,
        },
        named_ranges={},
        external_links=[],
        has_vba=False,
        dependency_graph={"Reserves": []},
        warnings=[],
    )


def _file_context() -> FileContext:
    return FileContext(
        filename="reserves.xlsx",
        description="Q4 reserve calculation",
        user_role="actuary",
        uploaded_at=datetime.now(),
    )


def test_every_reconciliation_value_produces_a_traceability_entry():
    internal_line = ReconciliationLine(
        check_type="excel_vs_python",
        label="Net premium reserves",
        source_value=1250.0,
        target_value=1250.0,
        delta=0.0,
        delta_pct=0.0,
        verdict="pass",
        materiality_threshold=0.02,
        source_cell="Reserves!B2",
    )
    external_line = ReconciliationLine(
        check_type="python_vs_accounts",
        label="Net premium reserves",
        source_value=1250.0,
        target_value=1255.0,
        delta=5.0,
        delta_pct=0.004,
        verdict="warn",
        materiality_threshold=0.05,
        source_cell="Reserves!B2",
    )
    finding = AnomalyFinding(
        finding_id="F0001",
        severity="info",
        tab="Reserves",
        cell_ref="B1",
        description="Base reserve input value",
        raw_value="1000",
    )

    entries = build_traceability_index(
        _parsed_file(), [internal_line, external_line], [finding], _file_context()
    )

    for line in (internal_line, external_line):
        assert any(e.report_value == line.source_value for e in entries), line
        assert any(e.report_value == line.target_value for e in entries), line

    # The Excel-cached value is a direct read of the formula cell itself.
    direct_entry = next(e for e in entries if e.report_value == 1250.0 and e.source_cell == "B2")
    assert direct_entry.source_tab == "Reserves"
    assert direct_entry.source_formula == "=B1*1.25"
    assert "cached formula result" in direct_entry.derivation_note.lower()

    # The Python-reconstructed value traces to its primary input cell, not the formula cell.
    computed_entry = next(e for e in entries if e.report_value == 1250.0 and e.source_cell == "B1")
    assert computed_entry.source_tab == "Reserves"
    assert "computed" in computed_entry.derivation_note.lower()

    # An AnomalyFinding's plain numeric raw_value is traced directly to its own cell.
    finding_entry = next(e for e in entries if e.report_figure_label == finding.description)
    assert finding_entry.report_value == 1000.0
    assert finding_entry.source_tab == "Reserves"
    assert finding_entry.source_cell == "B1"


def test_value_with_no_findable_source_cell_is_never_silently_dropped():
    external_line = ReconciliationLine(
        check_type="python_vs_accounts",
        label="Net premium reserves",
        source_value=1250.0,
        target_value=1255.0,
        delta=5.0,
        delta_pct=0.004,
        verdict="warn",
        materiality_threshold=0.05,
        source_cell="Reserves!B2",
    )

    entries = build_traceability_index(_parsed_file(), [external_line], [], _file_context())

    # target_value=1255.0 is an external reference figure -- never in the workbook.
    external_entry = next(e for e in entries if e.report_value == 1255.0)
    assert external_entry.source_tab is None
    assert external_entry.source_cell is None
    assert external_entry.derivation_note  # explains why, never blank


def test_reconciliation_line_missing_a_source_cell_still_produces_an_explained_entry():
    # Defensive case: a line Agent 3 couldn't tie back to any cell at all.
    orphan_line = ReconciliationLine(
        check_type="excel_vs_python",
        label="Unknown figure",
        source_value=42.0,
        target_value=42.0,
        delta=0.0,
        delta_pct=0.0,
        verdict="pass",
        materiality_threshold=0.02,
        source_cell=None,
    )

    entries = build_traceability_index(_parsed_file(), [orphan_line], [], _file_context())

    orphan_entry = next(e for e in entries if e.report_value == 42.0)
    assert orphan_entry.source_cell is None
    assert orphan_entry.source_tab is None
    assert orphan_entry.derivation_note
