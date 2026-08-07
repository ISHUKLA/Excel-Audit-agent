"""Integration test for agents/orchestrator.py: runs the real pipeline end-to-end, verifying it pauses
correctly at each of the four gates and carries state between them. Only the Anthropic client (Agent 4)
is mocked, since that's the one collaborator that would otherwise require a live network call."""

import json
from datetime import datetime
from types import SimpleNamespace

import openpyxl
import pytest

from agents.orchestrator import Orchestrator
from core.audit_log import AuditLog
from core.gates import GateBlockedError
from core.models import FileContext, ReconciliationLine


class _FakeMessagesAPI:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=next(self._responses))])


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessagesAPI(responses)


def _valid_tab_doc_json():
    return json.dumps(
        {
            "method_summary": "Applies a growth factor to the base reserve.",
            "assumptions": ["1.25x growth factor"],
            "data_sources": ["Reserves tab"],
            "anomalies_noted": [],
            "role_notes": "Actuary should confirm the growth factor.",
        }
    )


def _make_workbook(path):
    wb = openpyxl.Workbook()
    reserves = wb.active
    reserves.title = "Reserves"
    reserves["A2"] = "Net premium reserves"
    reserves["B1"] = 1000
    reserves["B2"] = "=B1*1.25"
    wb.save(path)


@pytest.fixture
def workbook_path(tmp_path) -> str:
    path = tmp_path / "reserves.xlsx"
    _make_workbook(path)
    return str(path)


@pytest.fixture
def orchestrator(tmp_path):
    audit_log = AuditLog(str(tmp_path / "audit.db"))
    client = _FakeClient([_valid_tab_doc_json()])
    return Orchestrator(audit_log=audit_log, documentation_client=client)


def _file_context() -> FileContext:
    return FileContext(
        filename="reserves.xlsx",
        description="Q4 reserve calculation; final output on the Reserves tab",
        user_role="actuary",
        uploaded_at=datetime.now(),
    )


def test_full_pipeline_runs_through_all_four_gates(workbook_path, orchestrator):
    # Gate 1 -> Agent 1 (parser) -> Agent 2 (anomaly_detector)
    report_id, findings = orchestrator.run(
        workbook_path, _file_context(), user_name="apoorva", context_confirmed=True
    )
    assert report_id in orchestrator._state
    assert len(findings) >= 1  # the hardcoded literal 1.25 in =B1*1.25

    # Gate 2 -> Agent 3 (reconciliation)
    decided = [f.model_copy(update={"human_decision": "confirmed"}) for f in findings]
    lines, unmatched = orchestrator.submit_findings_decisions(report_id, decided)
    assert unmatched == []  # no reference_figures were supplied

    # Gate 3 -> traceability -> Agent 4 (documentation, mocked)
    internal_verdict, external_verdict = orchestrator.submit_reconciliation_decisions(
        report_id, lines, internal_threshold=0.01, external_threshold=0.01, user_name="apoorva"
    )
    # This synthetic workbook has no Excel-cached formula value (openpyxl never
    # computes formulas), so Pass 1 also produces zero lines here -- both sides
    # correctly report "not_performed" rather than silently defaulting to "pass".
    assert internal_verdict == "not_performed"
    assert external_verdict == "not_performed"
    assert "report" in orchestrator._state[report_id]

    # Gate 4 -> final signed AuditReport
    report = orchestrator.submit_signoff(report_id, signed_by="Apoorva Ranjan", role="cfo")
    assert report.report_id == report_id
    assert report.signed_by == "Apoorva Ranjan"
    assert report.signed_at is not None
    assert report.internal_verdict == internal_verdict
    assert report.external_verdict == external_verdict
    # "not_performed" on both sides isn't a finding -- overall_verdict still passes.
    assert report.overall_verdict == "pass"
    assert len(report.documentation) == 1
    assert report.documentation[0].tab_name == "Reserves"

    # get_report/get_decisions give read-only access without re-running any gate.
    assert orchestrator.get_report(report_id).report_id == report_id
    decisions = orchestrator.get_decisions(report_id)
    assert any(d["action"] == "context_confirmed" for d in decisions)
    assert any(d["action"] == "signed_off" for d in decisions)


def test_gate1_blocks_before_any_parsing_or_state_is_created(workbook_path, orchestrator):
    with pytest.raises(GateBlockedError):
        orchestrator.run(workbook_path, _file_context(), user_name="apoorva", context_confirmed=False)

    assert orchestrator._state == {}


def test_gate2_blocks_on_undecided_finding_and_does_not_advance_state(workbook_path, orchestrator):
    report_id, findings = orchestrator.run(
        workbook_path, _file_context(), user_name="apoorva", context_confirmed=True
    )
    assert findings  # sanity: something to review

    with pytest.raises(GateBlockedError):
        orchestrator.submit_findings_decisions(report_id, findings)  # human_decision still None

    # Agent 3 must never have run -- pipeline state shows no trace of it.
    assert "unmatched_reference_items" not in orchestrator._state[report_id]


def test_gate3_blocks_on_a_blocking_line_and_agent4_never_runs(workbook_path, orchestrator):
    report_id, findings = orchestrator.run(
        workbook_path, _file_context(), user_name="apoorva", context_confirmed=True
    )
    decided = [f.model_copy(update={"human_decision": "dismissed"}) for f in findings]
    orchestrator.submit_findings_decisions(report_id, decided)

    blocking_line = ReconciliationLine(
        check_type="excel_vs_python",
        label="Some figure",
        source_value=100.0,
        target_value=50.0,
        delta=50.0,
        delta_pct=0.5,
        verdict="pass",  # deliberately wrong -- apply_thresholds must reclassify it to "block"
        materiality_threshold=0.01,
    )

    with pytest.raises(GateBlockedError):
        orchestrator.submit_reconciliation_decisions(
            report_id, [blocking_line], internal_threshold=0.01, external_threshold=0.01, user_name="apoorva"
        )

    assert "report" not in orchestrator._state[report_id]
    # Agent 4 is mocked but must never have been called -- Gate 3 blocked first.
    assert orchestrator._documentation_client.messages.calls == []


def test_gate4_blocks_on_empty_signed_by(workbook_path, orchestrator):
    report_id, findings = orchestrator.run(
        workbook_path, _file_context(), user_name="apoorva", context_confirmed=True
    )
    decided = [f.model_copy(update={"human_decision": "dismissed"}) for f in findings]
    lines, _ = orchestrator.submit_findings_decisions(report_id, decided)
    orchestrator.submit_reconciliation_decisions(
        report_id, lines, internal_threshold=0.01, external_threshold=0.01, user_name="apoorva"
    )

    with pytest.raises(GateBlockedError):
        orchestrator.submit_signoff(report_id, signed_by="", role="cfo")
