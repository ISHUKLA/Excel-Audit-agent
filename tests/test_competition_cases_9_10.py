"""Competition acceptance evidence for life-pricing Case 9 and context Case 10.

These tests consume the saved XLSX/CSV evidence through the production parser,
detector, reconciliation, gates and orchestrator. No LLM provider is permitted.
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

ROOT = Path(__file__).resolve().parents[1]
COMPETITION = ROOT / "demo" / "final cases"
ACTOR = "Isaac Shukla"
NOW = datetime.now(timezone.utc)

# Technical fixture inputs only. They exercise Gate 3 and are not defaults,
# recommendations, or a materiality decision made by the tool.
TECHNICAL_FIXTURE_PCT_THRESHOLD = 0.01
TECHNICAL_FIXTURE_ABSOLUTE_THRESHOLD = 100.0


class _ForbiddenMessages:
    def create(self, **_kwargs):  # pragma: no cover - failure sentinel
        raise AssertionError("Competition acceptance tests must not call an LLM provider")


class _ForbiddenClient:
    messages = _ForbiddenMessages()


def _expected(case_folder: str, case_id: str) -> dict:
    return json.loads(
        (
            COMPETITION
            / case_folder
            / "expected_results"
            / f"{case_id}_expected.json"
        ).read_text(encoding="utf-8")
    )


def _workbook_bytes(expected: dict) -> bytes:
    return (ROOT / expected["workbook_file"]).read_bytes()


def _reference(expected: dict):
    if expected["reference_file"] is None:
        return None
    path = ROOT / expected["reference_file"]
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    context = expected["reference_context"]
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
        code_version="competition-cases-9-10-uncommitted",
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


def _run_to_gate3(orchestrator: Orchestrator, expected: dict):
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


def _gate3_kwargs():
    return {
        "internal_pct_threshold": TECHNICAL_FIXTURE_PCT_THRESHOLD,
        "internal_absolute_threshold": TECHNICAL_FIXTURE_ABSOLUTE_THRESHOLD,
        "external_pct_threshold": TECHNICAL_FIXTURE_PCT_THRESHOLD,
        "external_absolute_threshold": TECHNICAL_FIXTURE_ABSOLUTE_THRESHOLD,
        "internal_threshold_deviation_reason": None,
        "external_threshold_deviation_reason": None,
        "actor": ACTOR,
        "use_ai_documentation": False,
        "ai_transmission_acknowledged": False,
        "acknowledge_incomplete": False,
    }


def test_case_9_supported_intermediates_reconstruct_but_designated_chain_is_partial():
    expected = _expected("case_9_life_pricing", "case_9_life_pricing")
    parsed = parse_workbook(_workbook_bytes(expected))

    supported_cells = [
        expected["summary_outputs"]["opening_premium_volume"]["cell"],
        expected["summary_outputs"]["included_projected_premium"]["cell"],
        expected["summary_outputs"]["included_projection_periods"]["cell"],
    ]
    supported = run_reconciliation(parsed, supported_cells)
    for line in supported.lines:
        assert line.completeness == "complete"
        assert line.verdict == "pass"
        assert line.source_value == pytest.approx(line.target_value)

    result = run_reconciliation(parsed, expected["authoritative_output_cells"])
    line = result.lines[0]
    assert line.source_value == pytest.approx(4_600_000, abs=0.01)
    assert line.target_value is None
    assert line.delta is None
    assert line.completeness == "partial"
    assert line.verdict == "incomplete"
    assert any("IRR (unsupported)" in item for item in line.unsupported_elements)


def test_case_9_inventory_and_finding_preserve_unsupported_and_hardcoded_evidence():
    expected = _expected("case_9_life_pricing", "case_9_life_pricing")
    inventory = json.loads((ROOT / expected["formula_inventory_file"]).read_text())
    unsupported = {
        function
        for item in inventory
        for function in item["unsupported_functions"]
    }
    assert unsupported == {"IRR"}
    assert expected["unsupported_formula_count"] == 1

    findings = detect_anomalies(parse_workbook(_workbook_bytes(expected)))
    assert len(findings) == 1
    assert f"{findings[0].tab}!{findings[0].cell_ref}" == "Policy Projection!C4"
    assert "0.03" in findings[0].description
    assert expected["approved_lapse"] == pytest.approx(0.06)
    assert expected["embedded_lapse"] == pytest.approx(0.03)


def test_case_9_no_reference_is_not_performed_and_unacknowledged_incomplete_blocks(tmp_path):
    expected = _expected("case_9_life_pricing", "case_9_life_pricing")
    assert expected["reference_file"] is None
    assert (
        COMPETITION / "case_9_life_pricing/reference_figures/README.md"
    ).is_file()
    orchestrator = _orchestrator(tmp_path, "case9")
    report_id, _, findings, preview = _run_to_gate3(orchestrator, expected)

    assert len(findings) == 1
    assert orchestrator.get_context_match_verdict(report_id) == "not_checked"
    assert preview.mappings == []
    assert not any(line.check_type == "python_vs_accounts" for line in preview.lines)

    with pytest.raises(
        GateBlockedError,
        match="internal_verdict=incomplete, external_verdict=not_performed",
    ):
        orchestrator.submit_gate3_decisions(
            report_id,
            preview,
            mapping_decisions=[],
            **_gate3_kwargs(),
        )

    assert orchestrator.get_stage(report_id) == "post_reconciliation"
    with pytest.raises(PipelineStateError, match="expected one of"):
        orchestrator.submit_approval_record(report_id, ACTOR)
    with pytest.raises(PipelineStateError, match="not been assembled"):
        orchestrator.get_report(report_id)


def test_case_10_saved_outputs_are_complete_and_reference_duplicates_stay_distinct():
    expected = _expected("case_10_accounting_context", "case_10_accounting_context")
    parsed = parse_workbook(_workbook_bytes(expected))
    reference = _reference(expected)
    assert reference is not None
    assert [line.line_id for line in reference.lines] == [
        "REF-0001", "REF-0002", "REF-0003", "REF-0004", "REF-0005"
    ]
    duplicate_lines = [
        line for line in reference.lines if line.label == "Other technical provisions"
    ]
    assert len(duplicate_lines) == 2
    assert sum(line.amount for line in duplicate_lines) == pytest.approx(1_250_000)

    result = run_reconciliation(
        parsed,
        expected["authoritative_output_cells"],
        reference,
    )
    internal = [line for line in result.lines if line.check_type == "excel_vs_python"]
    assert len(internal) == 4
    assert all(line.completeness == "complete" and line.verdict == "pass" for line in internal)
    assert all(line.delta == pytest.approx(0.0) for line in internal)
    assert result.unmatched_reference_items == ["REF-0004", "REF-0005"]
    assert result.unmapped_python_outputs == ["Accounting Bridge!B7"]


def test_case_10_fuzzy_mapping_needs_human_and_zero_delta_cannot_cure_context(tmp_path):
    expected = _expected("case_10_accounting_context", "case_10_accounting_context")
    orchestrator = _orchestrator(tmp_path, "case10")
    report_id, _, findings, preview = _run_to_gate3(orchestrator, expected)

    assert findings == []
    assert orchestrator.get_context_match_verdict(report_id) == "mismatch"
    assert len(preview.mappings) == 3
    claims_mapping = next(
        mapping for mapping in preview.mappings if mapping.reference_line_id == "REF-0001"
    )
    assert claims_mapping.suggested_by == "fuzzy_match"
    assert 60 <= claims_mapping.suggested_confidence < 85
    assert claims_mapping.is_approved is False
    assert claims_mapping.approved_by is None

    decisions = [
        MappingReviewDecision(mapping_id=mapping.mapping_id, action="approve")
        for mapping in preview.mappings
    ]
    internal_verdict, external_verdict, reviewed = orchestrator.preview_gate3_decisions(
        report_id,
        preview,
        decisions,
        internal_pct_threshold=TECHNICAL_FIXTURE_PCT_THRESHOLD,
        internal_absolute_threshold=TECHNICAL_FIXTURE_ABSOLUTE_THRESHOLD,
        external_pct_threshold=TECHNICAL_FIXTURE_PCT_THRESHOLD,
        external_absolute_threshold=TECHNICAL_FIXTURE_ABSOLUTE_THRESHOLD,
        actor=ACTOR,
    )
    assert internal_verdict == "pass"
    assert external_verdict == "block"
    assert all(mapping.is_approved for mapping in reviewed.mappings)
    assert all(mapping.approved_by == ACTOR for mapping in reviewed.mappings)
    external_lines = [
        line for line in reviewed.lines if line.check_type == "python_vs_accounts"
    ]
    assert len(external_lines) == 3
    assert all(line.delta == pytest.approx(0.0) for line in external_lines)
    assert reviewed.unmatched_reference_items == ["REF-0004", "REF-0005"]
    assert reviewed.unmapped_python_outputs == ["Accounting Bridge!B7"]

    with pytest.raises(GateBlockedError, match="external_verdict=block"):
        orchestrator.submit_gate3_decisions(
            report_id,
            preview,
            mapping_decisions=decisions,
            **_gate3_kwargs(),
        )
    assert orchestrator.get_stage(report_id) == "post_reconciliation"
    with pytest.raises(PipelineStateError, match="expected one of"):
        orchestrator.submit_approval_record(report_id, ACTOR)
