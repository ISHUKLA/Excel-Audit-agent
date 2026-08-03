"""Tests for core/gates.py: each of the four gates blocks progress until its human decision is recorded."""

from datetime import datetime

import pytest

from core.audit_log import AuditLog
from core.gates import (
    GateBlockedError,
    context_gate,
    findings_review_gate,
    reconciliation_gate,
    sign_off_gate,
)
from core.models import (
    AnomalyFinding,
    AuditReport,
    FileContext,
    ParsedFile,
    ReconciliationLine,
    ReferenceFigures,
)


@pytest.fixture
def audit_log(tmp_path):
    return AuditLog(str(tmp_path / "audit.db"))


def _file_context():
    return FileContext(
        filename="reserves_q4.xlsx",
        description="Q4 reserve calculation workbook",
        user_role="actuary",
        uploaded_at=datetime.now(),
    )


def test_context_gate_blocks_when_not_confirmed(audit_log):
    with pytest.raises(GateBlockedError):
        context_gate(
            _file_context(),
            confirmed=False,
            report_id="RPT-001",
            user_name="apoorva",
            audit_log=audit_log,
        )
    assert audit_log.get_decisions("RPT-001") == []


def test_context_gate_passes_and_logs_when_confirmed(audit_log):
    result = context_gate(
        _file_context(),
        confirmed=True,
        report_id="RPT-001",
        user_name="apoorva",
        audit_log=audit_log,
    )

    assert result is True
    decisions = audit_log.get_decisions("RPT-001")
    assert len(decisions) == 1
    assert decisions[0]["action"] == "context_confirmed"
    assert decisions[0]["gate_number"] == 1


def _reference_figures():
    return ReferenceFigures(
        source_label="Q4 trial balance extract",
        line_items={"Net premium reserves": 1_250_000.0},
        uploaded_at=datetime.now(),
    )


def test_context_gate_blocks_when_reference_figures_provided_but_not_confirmed(audit_log):
    with pytest.raises(GateBlockedError):
        context_gate(
            _file_context(),
            confirmed=True,
            report_id="RPT-001",
            user_name="apoorva",
            audit_log=audit_log,
            reference_figures=_reference_figures(),
            reference_figures_confirmed=False,
        )
    # Context confirmation itself must not have been logged either -- the whole gate blocks.
    assert audit_log.get_decisions("RPT-001") == []


def test_context_gate_passes_and_logs_both_confirmations_when_reference_figures_confirmed(audit_log):
    result = context_gate(
        _file_context(),
        confirmed=True,
        report_id="RPT-001",
        user_name="apoorva",
        audit_log=audit_log,
        reference_figures=_reference_figures(),
        reference_figures_confirmed=True,
    )

    assert result is True
    decisions = audit_log.get_decisions("RPT-001")
    assert [d["action"] for d in decisions] == ["context_confirmed", "reference_figures_confirmed"]


def _finding(**overrides):
    defaults = dict(
        finding_id="F0001",
        severity="warning",
        tab="TabA",
        cell_ref="B1",
        description="Hardcoded literal",
        raw_value="=A1*1.75",
    )
    defaults.update(overrides)
    return AnomalyFinding(**defaults)


def test_findings_review_gate_blocks_on_undecided_finding():
    findings = [_finding(human_decision=None), _finding(finding_id="F0002", human_decision="confirmed")]
    with pytest.raises(GateBlockedError):
        findings_review_gate(findings, report_id="RPT-001")


def test_findings_review_gate_passes_when_all_decided():
    findings = [
        _finding(human_decision="confirmed"),
        _finding(finding_id="F0002", human_decision="dismissed"),
    ]
    result = findings_review_gate(findings, report_id="RPT-001")
    assert result == findings


def _line(**overrides):
    defaults = dict(
        check_type="excel_vs_python",
        label="Net premium reserves",
        source_value=100.0,
        target_value=100.0,
        delta=0.0,
        delta_pct=0.0,
        verdict="pass",
        materiality_threshold=1000.0,
    )
    defaults.update(overrides)
    return ReconciliationLine(**defaults)


def test_reconciliation_gate_blocks_on_either_check_type_blocking(audit_log):
    lines = [
        _line(check_type="excel_vs_python", verdict="pass"),
        _line(check_type="python_vs_accounts", verdict="block"),
    ]
    with pytest.raises(GateBlockedError):
        reconciliation_gate(lines, report_id="RPT-001", user_name="apoorva", audit_log=audit_log)


def test_reconciliation_gate_passes_and_logs_warnings_with_check_type(audit_log):
    lines = [
        _line(check_type="excel_vs_python", verdict="warn", label="Internal check"),
        _line(check_type="python_vs_accounts", verdict="pass", label="External check"),
    ]

    internal_verdict, external_verdict = reconciliation_gate(
        lines, report_id="RPT-001", user_name="apoorva", audit_log=audit_log
    )

    assert internal_verdict == "warn"
    assert external_verdict == "pass"

    decisions = audit_log.get_decisions("RPT-001")
    assert len(decisions) == 1
    assert decisions[0]["action"] == "reconciliation_warning"
    assert "excel_vs_python" in decisions[0]["reason"]


def test_reconciliation_gate_reports_not_performed_rather_than_defaulting_to_pass(audit_log):
    # Only excel_vs_python lines exist -- e.g. no reference_figures were provided,
    # so python_vs_accounts was never attempted. That must not silently read as "pass".
    lines = [_line(check_type="excel_vs_python", verdict="pass")]

    internal_verdict, external_verdict = reconciliation_gate(
        lines, report_id="RPT-001", user_name="apoorva", audit_log=audit_log
    )

    assert internal_verdict == "pass"
    assert external_verdict == "not_performed"


def _report(**overrides):
    defaults = dict(
        file_context=_file_context(),
        reference_figures=None,
        parsed_file=ParsedFile(
            tab_names=["TabA"],
            cells={},
            cached_values={},
            named_ranges={},
            external_links=[],
            has_vba=False,
            dependency_graph={},
            warnings=[],
        ),
        findings=[],
        reconciliation=[],
        unmatched_reference_items=[],
        traceability_index=[],
        documentation=[],
        overall_verdict="pass",
        internal_verdict="pass",
        external_verdict="pass",
        signed_by=None,
        signed_at=None,
        report_id="RPT-001",
    )
    defaults.update(overrides)
    return AuditReport(**defaults)


def test_sign_off_gate_blocks_on_empty_signed_by(audit_log):
    with pytest.raises(GateBlockedError):
        sign_off_gate(_report(), signed_by="", role="cfo", audit_log=audit_log)


def test_sign_off_gate_passes_and_logs(audit_log):
    report = sign_off_gate(_report(), signed_by="Apoorva Ranjan", role="cfo", audit_log=audit_log)

    assert report.signed_by == "Apoorva Ranjan"
    assert report.signed_at is not None
    assert report.signed_role == "cfo"

    decisions = audit_log.get_decisions("RPT-001")
    assert len(decisions) == 1
    assert decisions[0]["action"] == "signed_off"
    assert "cfo" in decisions[0]["reason"]
