"""Acceptance evidence for the two user-facing formula and impact cases.

These tests use the same parser, anomaly detector, reconciliation, gate,
orchestrator and PDF paths as the Streamlit application. They do not invoke
LibreOffice or Microsoft Excel and they explicitly decline AI documentation.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import demo_cases
from agents.anomaly_detector import detect_anomalies
from agents.orchestrator import Orchestrator
from agents.parser import parse_workbook
from agents.reconciliation import run_reconciliation
from core.audit_log import AuditLog
from core.formula_catalogue import SUPPORTED_FUNCTIONS
from core.models import FileContext, MappingReviewDecision
from core.state_store import StateStore
from core.ui_inputs import build_reference_figures
from core.workbook_identity import sha256_bytes
from report.generator import generate_report_pdf

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
EXPECTED = DEMO / "expected_results"
OUTPUT_PACK = ROOT / "outputs" / "ai2_2026_demo_pack_20260824"
ACTOR = "Isaac Shukla"
NOW = datetime.now(timezone.utc)


class _ForbiddenMessages:
    def create(self, **_kwargs):  # pragma: no cover - a failure sentinel
        raise AssertionError("The Anthropic API must not be called in demo acceptance tests")


class _ForbiddenClient:
    messages = _ForbiddenMessages()


def _expected(number: int) -> dict:
    return json.loads((EXPECTED / f"case_{number}_expected.json").read_text())


def _reference(number: int, expected: dict):
    case = demo_cases.load_case(number)
    with Path(case["reference_csv_path"]).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    context = expected["gate1_context"]
    return build_reference_figures(
        source_label=rows[0]["ledger_source"],
        entity=context["entity"],
        period=context["period"],
        currency=context["currency"],
        basis=context["basis"],
        control_total=expected["reference_control_total"],
        control_total_confirmed_by_human=True,
        rows=rows,
        require_account_number=True,
        uploaded_at=NOW,
    )


@pytest.fixture(scope="module")
def parsed_case_5():
    return parse_workbook(demo_cases.load_case(5)["workbook_bytes"])


@pytest.fixture(scope="module")
def parsed_case_6():
    return parse_workbook(demo_cases.load_case(6)["workbook_bytes"])


def test_case_5_covers_the_live_supported_formula_catalogue(parsed_case_5):
    expected = _expected(5)
    demonstrated = {item["primary_function"] for item in expected["formula_cases"]}
    assert demonstrated == set(SUPPORTED_FUNCTIONS)
    assert [item["cell"] for item in expected["formula_cases"]] == expected[
        "authoritative_output_cells"
    ]
    assert parsed_case_5.workbook_meta.calc_mode == "automatic"


def test_case_5_all_supported_outputs_match_and_boundaries_fail_closed(parsed_case_5):
    expected = _expected(5)
    assert not [
        ref for ref, cell in parsed_case_5.cells.items() if cell.formula and cell.is_error
    ]
    result = run_reconciliation(parsed_case_5, expected["authoritative_output_cells"])
    lines = [line for line in result.lines if line.check_type == "excel_vs_python"]
    assert len(lines) == len(SUPPORTED_FUNCTIONS)
    for case, line in zip(expected["formula_cases"], lines):
        assert line.completeness == "complete", case["cell"]
        assert line.reconstruction_coverage_pct == pytest.approx(100.0)
        assert line.source_value == pytest.approx(case["expected_value"])
        assert line.target_value == pytest.approx(case["expected_value"])
        assert line.delta == pytest.approx(0.0)
        assert line.verdict == "pass"

    boundary = run_reconciliation(parsed_case_5, expected["unsupported_boundary_cells"])
    boundary_lines = [line for line in boundary.lines if line.check_type == "excel_vs_python"]
    assert len(boundary_lines) == 2
    for line in boundary_lines:
        assert line.completeness == "partial"
        assert line.verdict == "incomplete"
        assert line.target_value is None
        assert line.unsupported_elements


def test_case_5_has_nested_cross_tab_blank_zero_and_rounding_evidence(parsed_case_5):
    formulas = {
        ref: record.formula or "" for ref, record in parsed_case_5.cells.items()
    }
    assert "MATCH(" in formulas["Calculations!B23"].upper()
    assert "INDEX(" in formulas["Calculations!B23"].upper()
    assert "INPUTS!" in formulas["Calculations!B4"].upper()
    assert formulas["Assumptions!B15"].upper() == "=TRUE()"
    assert formulas["Assumptions!B16"].upper() == "=FALSE()"
    assert "Inputs!H4" not in parsed_case_5.cells
    assert parsed_case_5.cells["Inputs!H7"].cached_value == 0
    assert _expected(5)["formula_cases"][4]["expected_value"] < 0


def test_case_6_is_realistically_sized_and_matches_exact_business_impact(parsed_case_6):
    expected = _expected(6)
    formula_cells = [cell for cell in parsed_case_6.cells.values() if cell.formula]
    assert len(formula_cells) == expected["formula_cell_count"]
    assert len(formula_cells) >= 3_000
    assert len(parsed_case_6.tab_names) == 9
    assert detect_anomalies(parsed_case_6) == []
    assert not [
        ref for ref, cell in parsed_case_6.cells.items() if cell.formula and cell.is_error
    ]

    impact = list(expected["business_impact"].values())
    result = run_reconciliation(parsed_case_6, [item["cell"] for item in impact])
    lines = [line for line in result.lines if line.check_type == "excel_vs_python"]
    for item, line in zip(impact, lines):
        assert line.completeness == "complete", item["cell"]
        assert line.source_value == pytest.approx(item["expected_value"])
        assert line.target_value == pytest.approx(item["expected_value"])
        assert line.delta == pytest.approx(0.0)
        assert line.verdict == "pass"

    assert expected["business_impact"]["technical_provisions_increase"]["expected_value"] == pytest.approx(2_330_154.10)
    assert expected["business_impact"]["available_own_funds_reduction"]["expected_value"] == pytest.approx(2_330_154.10)
    assert expected["business_impact"]["solvency_ratio_deterioration"]["expected_value"] == pytest.approx(0.0665)


@pytest.mark.parametrize("number", [5, 6])
def test_cases_complete_all_four_gates_and_only_then_generate_pdf(number, tmp_path):
    case = demo_cases.load_case(number)
    expected = _expected(number)
    reference = _reference(number, expected)
    workbook_hash = sha256_bytes(case["workbook_bytes"])
    file_context = FileContext(
        filename=Path(expected["workbook_path"]).name,
        description=case["description"],
        user_role="actuary",
        entity=case["entity"],
        period=case["period"],
        currency=case["currency"],
        basis=case["basis"],
        confirmed_workbook_hash=workbook_hash,
        uploaded_at=NOW,
    )
    db_path = str(tmp_path / f"case_{number}_audit.db")
    audit_log = AuditLog(db_path)
    orchestrator = Orchestrator(
        audit_log=audit_log,
        state_store=StateStore(db_path, audit_log=audit_log),
        documentation_client=_ForbiddenClient(),
        code_version=f"case-{number}-acceptance",
    )

    report_id, _, findings = orchestrator.run(
        case["workbook_bytes"],
        file_context,
        reference,
        expected_workbook_hash=workbook_hash,
        context_confirmed=True,
        actor=ACTOR,
    )
    assert findings == []

    label, preview = orchestrator.submit_gate2_decisions(
        report_id,
        findings,
        expected["authoritative_output_cells"],
        actor=ACTOR,
    )
    assert label == "Preview, pending Gate 3 approval"
    assert preview.mappings
    assert all(not mapping.is_approved for mapping in preview.mappings)
    assert preview.unmatched_reference_items == []
    assert preview.unmapped_python_outputs == []

    decisions = [
        MappingReviewDecision(mapping_id=mapping.mapping_id, action="approve")
        for mapping in preview.mappings
    ]
    internal, external, final_result = orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=decisions,
        internal_pct_threshold=0.01,
        internal_absolute_threshold=100.0,
        external_pct_threshold=0.01,
        external_absolute_threshold=100.0,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=False,
        ai_transmission_acknowledged=False,
        acknowledge_incomplete=False,
    )
    assert internal == expected["expected_internal_verdict"] == "pass"
    assert external == expected["expected_external_verdict"] == "pass"
    assert final_result.verdicts_are_final is True
    assert all(mapping.is_approved and mapping.approved_by == ACTOR for mapping in final_result.mappings)

    report_before_gate4 = orchestrator.get_report(report_id)
    assert report_before_gate4.report_approval_name is None
    with pytest.raises(ValueError, match="named approval record"):
        generate_report_pdf(report_before_gate4, orchestrator.get_audit_rows(report_id))

    report = orchestrator.submit_approval_record(report_id, ACTOR, "actuary")
    pdf = generate_report_pdf(report, orchestrator.get_audit_rows(report_id))
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1_000

    gate_numbers = [
        payload["gate"]
        for row in orchestrator.get_audit_rows(report_id)
        if (payload := json.loads(row["payload_json"])).get("gate") is not None
    ]
    assert gate_numbers == [1, 2, 3, 4]


def test_case_6_benchmark_and_output_pack_are_committed_and_consistent():
    summary = json.loads((ROOT / "benchmark" / "case_6_summary.json").read_text())
    expected = _expected(6)
    assert summary["warm_up_runs"] == 1
    assert summary["measured_runs"] >= 5
    assert summary["workbook"]["formula_cell_count"] == expected["formula_cell_count"]
    assert summary["observed"]["mappings_all_unapproved"] is True
    assert summary["limitations"]

    for filename in (
        "case_5_supported_formula_demonstration.xlsx",
        "case_5_reference_figures.csv",
        "case_5_expected.json",
        "case_6_reserve_stress_business_impact.xlsx",
        "case_6_reference_figures.csv",
        "case_6_expected.json",
        "CASE_6_BENCHMARK_REPORT.md",
        "case_6_summary.json",
    ):
        assert (OUTPUT_PACK / filename).exists(), filename

    for number in (5, 6):
        expected = _expected(number)
        canonical = ROOT / expected["workbook_path"]
        copied = OUTPUT_PACK / canonical.name
        canonical_hash = hashlib.sha256(canonical.read_bytes()).hexdigest()
        assert expected["sha256"] == canonical_hash
        assert canonical_hash == hashlib.sha256(copied.read_bytes()).hexdigest()
        assert (EXPECTED / f"case_{number}_expected.json").read_bytes() == (
            OUTPUT_PACK / f"case_{number}_expected.json"
        ).read_bytes()

    for filename in ("CASE_6_BENCHMARK_REPORT.md", "case_6_summary.json"):
        assert (ROOT / "benchmark" / filename).read_bytes() == (
            OUTPUT_PACK / filename
        ).read_bytes()
