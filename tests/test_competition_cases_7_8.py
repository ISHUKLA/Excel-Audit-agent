"""Competition acceptance evidence for IFRS 17 Case 7 and capital Case 8.

The saved XLSX and CSV artifacts run through the production parser, anomaly
detector, reconciliation, gates, orchestrator and PDF generator. LibreOffice is
not invoked by these tests, and the optional documentation provider is forbidden.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.anomaly_detector import detect_anomalies
from agents.orchestrator import Orchestrator, PipelineStateError
from agents.parser import parse_workbook
from agents.reconciliation import run_reconciliation
from core.audit_log import AuditLog
from core.gates import GateBlockedError
from core.models import FileContext, MappingReviewDecision
from core.state_store import StateStore
from core.ui_inputs import build_reference_figures
from core.workbook_identity import sha256_bytes
from report.generator import generate_report_pdf

ROOT = Path(__file__).resolve().parents[1]
COMPETITION = ROOT / "demo" / "final cases"
ACTOR = "Isaac Shukla"
NOW = datetime.now(timezone.utc)

# Technical fixture inputs only. These exercise the gate and are not defaults,
# recommendations or a materiality decision made by the tool.
TECHNICAL_FIXTURE_PCT_THRESHOLD = 0.01
TECHNICAL_FIXTURE_ABSOLUTE_THRESHOLD = 100.0


class _ForbiddenMessages:
    def create(self, **_kwargs):  # pragma: no cover - failure sentinel
        raise AssertionError("Competition acceptance tests must not call an LLM provider")


class _ForbiddenClient:
    messages = _ForbiddenMessages()


def _expected(folder: str, case_id: str) -> dict:
    return json.loads(
        (
            COMPETITION
            / folder
            / "expected_results"
            / f"{case_id}_expected.json"
        ).read_text(encoding="utf-8")
    )


def _workbook_bytes(expected: dict) -> bytes:
    return (ROOT / expected["workbook_file"]).read_bytes()


def _reference(expected: dict):
    path = ROOT / expected["reference_file"]
    with path.open(newline="", encoding="utf-8") as handle:
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


def _orchestrator(tmp_path: Path, name: str) -> Orchestrator:
    db_path = str(tmp_path / f"{name}.db")
    audit_log = AuditLog(db_path)
    return Orchestrator(
        audit_log=audit_log,
        state_store=StateStore(db_path, audit_log=audit_log),
        documentation_client=_ForbiddenClient(),
        code_version="competition-cases-7-8-uncommitted",
    )


def _file_context(expected: dict, workbook_hash: str) -> FileContext:
    context = expected["gate1_context"]
    return FileContext(
        filename=Path(expected["workbook_file"]).name,
        description=f"Synthetic competition demonstration {expected['case_id']}",
        user_role="actuary",
        confirmed_workbook_hash=workbook_hash,
        entity=context["entity"],
        period=context["period"],
        currency=context["currency"],
        basis=context["basis"],
        uploaded_at=NOW,
    )


def _review(findings):
    return [
        finding.model_copy(
            update={
                "human_decision": "confirmed",
                "human_reason": "Confirmed for the synthetic competition acceptance run.",
                "decided_by": ACTOR,
                "decided_at": NOW,
            }
        )
        for finding in findings
    ]


def _run_to_gate3_preview(orchestrator: Orchestrator, expected: dict):
    workbook_bytes = _workbook_bytes(expected)
    workbook_hash = sha256_bytes(workbook_bytes)
    report_id, parsed, findings = orchestrator.run(
        workbook_bytes,
        _file_context(expected, workbook_hash),
        _reference(expected),
        expected_workbook_hash=workbook_hash,
        context_confirmed=True,
        actor=ACTOR,
    )
    _, preview = orchestrator.submit_gate2_decisions(
        report_id,
        _review(findings),
        expected["authoritative_output_cells"],
        actor=ACTOR,
    )
    return report_id, parsed, findings, preview


@pytest.mark.parametrize(
    ("case_id", "claims", "expenses", "premiums", "fcf", "closing"),
    [
        ("case_7a_ifrs17_clean", 104_200_000, 12_600_000, -18_400_000, 98_400_000, 128_400_000),
        ("case_7b_ifrs17_missing_cohort", 98_000_000, 12_600_000, -18_400_000, 92_200_000, 122_200_000),
    ],
)
def test_case_7_exact_saved_outputs(case_id, claims, expenses, premiums, fcf, closing):
    expected = _expected("case_7_ifrs17", case_id)
    parsed = parse_workbook(_workbook_bytes(expected))
    assert parsed.workbook_meta.calc_mode == "automatic"

    checks = {
        "present_value_claims": claims,
        "present_value_expenses": expenses,
        "present_value_premiums": premiums,
        "fulfilment_cash_flows": fcf,
        "risk_adjustment": 7_800_000,
        "contractual_service_margin": 22_200_000,
        "closing_liability": closing,
        "signed_closing_liability": -closing,
    }
    result = run_reconciliation(
        parsed,
        [expected["summary_outputs"][key]["cell"] for key in checks],
    )
    lines = [line for line in result.lines if line.check_type == "excel_vs_python"]
    assert len(lines) == len(checks)
    for (key, value), line in zip(checks.items(), lines):
        assert expected["summary_outputs"][key]["expected_value"] == value
        assert line.source_value == pytest.approx(value)
        assert line.target_value == pytest.approx(value)
        assert line.delta == pytest.approx(0.0)
        assert line.completeness == "complete"
        assert line.verdict == "pass"


def test_case_7_defect_is_exactly_the_omitted_eur_6_2_million_cohort():
    clean = _expected("case_7_ifrs17", "case_7a_ifrs17_clean")
    defective = _expected("case_7_ifrs17", "case_7b_ifrs17_missing_cohort")
    clean_closing = clean["summary_outputs"]["closing_liability"]["expected_value"]
    defective_closing = defective["summary_outputs"]["closing_liability"]["expected_value"]

    assert clean_closing - defective_closing == 6_200_000
    assert defective["omitted_cohort_amount"] == 6_200_000

    findings = detect_anomalies(parse_workbook(_workbook_bytes(defective)))
    assert len(findings) == 1
    assert f"{findings[0].tab}!{findings[0].cell_ref}" == "Reserve Summary!B5"
    assert "Cash Flow Calculation!B9" in findings[0].description


def test_case_7_clean_reaches_named_approval_and_pdf(tmp_path):
    expected = _expected("case_7_ifrs17", "case_7a_ifrs17_clean")
    orchestrator = _orchestrator(tmp_path, "case7-clean")
    report_id, _, findings, preview = _run_to_gate3_preview(orchestrator, expected)
    assert findings == []
    assert len(preview.mappings) == 1
    assert preview.mappings[0].is_approved is False
    internal_line = next(line for line in preview.lines if line.check_type == "excel_vs_python")
    assert internal_line.verdict == "pass"
    assert internal_line.completeness == "complete"

    mapping = preview.mappings[0]
    internal, external, final_result = orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[
            MappingReviewDecision(mapping_id=mapping.mapping_id, action="approve")
        ],
        internal_pct_threshold=TECHNICAL_FIXTURE_PCT_THRESHOLD,
        internal_absolute_threshold=TECHNICAL_FIXTURE_ABSOLUTE_THRESHOLD,
        external_pct_threshold=TECHNICAL_FIXTURE_PCT_THRESHOLD,
        external_absolute_threshold=TECHNICAL_FIXTURE_ABSOLUTE_THRESHOLD,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=False,
        ai_transmission_acknowledged=False,
        acknowledge_incomplete=False,
    )
    assert (internal, external) == ("pass", "pass")
    assert final_result.verdicts_are_final is True
    with pytest.raises(ValueError, match="named approval record"):
        generate_report_pdf(
            orchestrator.get_report(report_id), orchestrator.get_audit_rows(report_id)
        )

    report = orchestrator.submit_approval_record(report_id, ACTOR, "actuary")
    pdf = generate_report_pdf(report, orchestrator.get_audit_rows(report_id))
    assert pdf.startswith(b"%PDF")


def test_case_7_defective_internal_pass_external_block_and_no_gate4(tmp_path):
    expected = _expected("case_7_ifrs17", "case_7b_ifrs17_missing_cohort")
    orchestrator = _orchestrator(tmp_path, "case7-defective")
    report_id, _, findings, preview = _run_to_gate3_preview(orchestrator, expected)
    assert len(findings) == 1
    internal_line = next(line for line in preview.lines if line.check_type == "excel_vs_python")
    external_line = next(line for line in preview.lines if line.check_type == "python_vs_accounts")
    assert internal_line.delta == pytest.approx(0.0)
    assert internal_line.verdict == "pass"
    assert external_line.delta == pytest.approx(6_200_000)

    with pytest.raises(GateBlockedError, match="external_verdict=block"):
        orchestrator.submit_gate3_decisions(
            report_id,
            preview,
            mapping_decisions=[
                MappingReviewDecision(mapping_id=preview.mappings[0].mapping_id, action="approve")
            ],
            internal_pct_threshold=TECHNICAL_FIXTURE_PCT_THRESHOLD,
            internal_absolute_threshold=TECHNICAL_FIXTURE_ABSOLUTE_THRESHOLD,
            external_pct_threshold=TECHNICAL_FIXTURE_PCT_THRESHOLD,
            external_absolute_threshold=TECHNICAL_FIXTURE_ABSOLUTE_THRESHOLD,
            internal_threshold_deviation_reason=None,
            external_threshold_deviation_reason=None,
            actor=ACTOR,
            use_ai_documentation=False,
            ai_transmission_acknowledged=False,
            acknowledge_incomplete=False,
        )
    with pytest.raises(PipelineStateError):
        orchestrator.submit_approval_record(report_id, ACTOR, "actuary")
    with pytest.raises(PipelineStateError):
        orchestrator.get_report(report_id)


def test_case_8_exact_capital_results_and_hardcoded_assumption():
    expected = _expected("case_8_solvency_capital", "case_8_solvency_capital")
    parsed = parse_workbook(_workbook_bytes(expected))
    results = expected["summary_outputs"]
    reconstructed = run_reconciliation(
        parsed, [item["cell"] for item in results.values()]
    )
    lines = [line for line in reconstructed.lines if line.check_type == "excel_vs_python"]
    for item, line in zip(results.values(), lines):
        assert line.source_value == pytest.approx(item["expected_value"])
        assert line.target_value == pytest.approx(item["expected_value"])
        assert line.completeness == "complete"
        assert line.verdict == "pass"

    assert results["defective_final_scr"]["expected_value"] == 111_000_000
    assert results["approved_final_scr"]["expected_value"] == 123_000_000
    assert results["defective_solvency_ratio"]["expected_value"] == pytest.approx(1.622)
    assert results["approved_solvency_ratio"]["expected_value"] == pytest.approx(1.463)
    findings = detect_anomalies(parsed)
    assert len(findings) == 1
    assert f"{findings[0].tab}!{findings[0].cell_ref}" == "Capital Summary!C9"
    assert "47000000" in findings[0].description


def test_case_8_separate_verdicts_external_block_and_no_gate4(tmp_path):
    expected = _expected("case_8_solvency_capital", "case_8_solvency_capital")
    orchestrator = _orchestrator(tmp_path, "case8")
    report_id, _, findings, preview = _run_to_gate3_preview(orchestrator, expected)
    assert len(findings) == 1
    internal_line = next(line for line in preview.lines if line.check_type == "excel_vs_python")
    external_line = next(line for line in preview.lines if line.check_type == "python_vs_accounts")
    assert internal_line.source_value == pytest.approx(111_000_000)
    assert internal_line.target_value == pytest.approx(111_000_000)
    assert internal_line.verdict == "pass"
    assert external_line.target_value == pytest.approx(123_000_000)
    assert external_line.delta == pytest.approx(12_000_000)

    with pytest.raises(GateBlockedError, match="internal_verdict=pass, external_verdict=block"):
        orchestrator.submit_gate3_decisions(
            report_id,
            preview,
            mapping_decisions=[
                MappingReviewDecision(mapping_id=preview.mappings[0].mapping_id, action="approve")
            ],
            internal_pct_threshold=TECHNICAL_FIXTURE_PCT_THRESHOLD,
            internal_absolute_threshold=TECHNICAL_FIXTURE_ABSOLUTE_THRESHOLD,
            external_pct_threshold=TECHNICAL_FIXTURE_PCT_THRESHOLD,
            external_absolute_threshold=TECHNICAL_FIXTURE_ABSOLUTE_THRESHOLD,
            internal_threshold_deviation_reason=None,
            external_threshold_deviation_reason=None,
            actor=ACTOR,
            use_ai_documentation=False,
            ai_transmission_acknowledged=False,
            acknowledge_incomplete=False,
        )
    with pytest.raises(PipelineStateError):
        orchestrator.submit_approval_record(report_id, ACTOR, "actuary")
    with pytest.raises(PipelineStateError):
        orchestrator.get_report(report_id)
