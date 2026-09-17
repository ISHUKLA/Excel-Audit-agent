"""Tests for core/flight_recorder_state.py.

Covers: state machine construction, cascading locks, node definitions, and
descriptive output for UI/logging purposes.

Does not cover: all possible malformed state combinations (those are
flight_recorder_queries.py tests).
"""

import pytest

from core.audit_log import AuditLog
from core.flight_recorder_queries import get_report_events
from core.flight_recorder_state import (
    NODE_ORDER,
    PipelineState,
    build_pipeline_state,
    compute_cascading_lock,
    compute_node_status,
    describe_node,
    describe_pipeline,
    is_downstream_locked,
    node_definitions,
)

CONTEXT = {"workbook_hash": "abc123", "code_version": "test"}


@pytest.fixture
def audit_log(tmp_path):
    return AuditLog(str(tmp_path / "audit.db"))


def _log_clean_full_run(audit_log: AuditLog) -> None:
    """Log a complete run through all gates with no blocks."""
    audit_log.log_event(
        report_id="report_1",
        event_type="gate_decision",
        payload={"gate": 1, "action": "context_confirmed", "context_match_verdict": "match"},
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    audit_log.log_event(
        report_id="report_1",
        event_type="gate_decision",
        payload={
            "gate": 2,
            "action": "findings_reviewed_and_outputs_designated",
            "dispositions": [{"finding_id": "F1", "disposition": "confirmed"}],
        },
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    audit_log.log_event(
        report_id="report_1",
        event_type="gate_decision",
        payload={
            "gate": 3,
            "action": "reconciliation_reviewed",
            "internal_verdict": "pass",
            "external_verdict": "pass",
        },
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    audit_log.log_event(
        report_id="report_1",
        event_type="llm_use_decision",
        payload={"decision": "decline"},
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    audit_log.log_event(
        report_id="report_1",
        event_type="report_approved",
        payload={"gate": 4, "action": "approval_record_created", "approval_name": "Divyank"},
        actor="Divyank",
        context=CONTEXT,
    )


def test_node_definitions_includes_all_nodes():
    definitions = node_definitions()
    for node in NODE_ORDER:
        assert node in definitions
        assert definitions[node].name == node


def test_node_definitions_human_nodes_have_actor_type_human():
    definitions = node_definitions()
    for node in ["gate_1", "gate_2", "gate_3", "gate_4"]:
        assert definitions[node].actor_type == "human"


def test_node_definitions_agent_nodes_have_actor_type_agent():
    definitions = node_definitions()
    for node in ["anomaly_detector", "internal_reconciliation", "external_reconciliation", "pdf_export"]:
        assert definitions[node].actor_type == "agent"


def test_node_definitions_ai_node_has_actor_type_ai():
    definitions = node_definitions()
    assert definitions["optional_ai_doc"].actor_type == "ai"


def test_node_definitions_gates_have_predecessors():
    definitions = node_definitions()
    assert len(definitions["gate_1"].predecessor_nodes) == 0
    assert len(definitions["gate_2"].predecessor_nodes) > 0
    assert len(definitions["gate_3"].predecessor_nodes) > 0


def test_compute_node_status_complete_when_event_exists(audit_log):
    _log_clean_full_run(audit_log)
    events = get_report_events(audit_log, "report_1")
    assert compute_node_status(events, "gate_1") == "complete"
    assert compute_node_status(events, "gate_4") == "complete"


def test_compute_node_status_waiting_when_predecessor_done_but_node_not_started(audit_log):
    audit_log.log_event(
        report_id="report_1",
        event_type="gate_decision",
        payload={"gate": 1, "action": "context_confirmed"},
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    events = get_report_events(audit_log, "report_1")
    assert compute_node_status(events, "gate_2") == "waiting"


def test_compute_node_status_blocked_when_gate_3_blocks(audit_log):
    audit_log.log_event(
        report_id="report_1",
        event_type="gate_decision",
        payload={
            "gate": 3,
            "action": "reconciliation_reviewed",
            "internal_verdict": "block",
            "external_verdict": "pass",
        },
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    events = get_report_events(audit_log, "report_1")
    assert compute_node_status(events, "gate_3") == "blocked"
    assert compute_node_status(events, "gate_4") == "blocked"


def test_compute_cascading_lock_empty_on_clean_run(audit_log):
    _log_clean_full_run(audit_log)
    events = get_report_events(audit_log, "report_1")
    locks = compute_cascading_lock(events)
    assert locks["locked_nodes"] == []
    assert locks["blocking_node"] is None


def test_compute_cascading_lock_locks_downstream_on_gate_3_block(audit_log):
    audit_log.log_event(
        report_id="report_1",
        event_type="gate_decision",
        payload={
            "gate": 3,
            "action": "reconciliation_reviewed",
            "internal_verdict": "pass",
            "external_verdict": "block",
        },
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    events = get_report_events(audit_log, "report_1")
    locks = compute_cascading_lock(events)
    assert locks["blocking_node"] == "gate_3"
    assert set(locks["locked_nodes"]) == {"optional_ai_doc", "gate_4", "pdf_export"}


def test_is_downstream_locked_returns_true_for_locked_nodes(audit_log):
    audit_log.log_event(
        report_id="report_1",
        event_type="gate_decision",
        payload={
            "gate": 3,
            "action": "reconciliation_reviewed",
            "internal_verdict": "block",
            "external_verdict": "pass",
        },
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    events = get_report_events(audit_log, "report_1")
    locks = compute_cascading_lock(events)

    assert is_downstream_locked("gate_4", locks) is True
    assert is_downstream_locked("pdf_export", locks) is True
    assert is_downstream_locked("optional_ai_doc", locks) is True


def test_is_downstream_locked_returns_false_for_upstream_nodes(audit_log):
    audit_log.log_event(
        report_id="report_1",
        event_type="gate_decision",
        payload={
            "gate": 3,
            "action": "reconciliation_reviewed",
            "internal_verdict": "block",
            "external_verdict": "pass",
        },
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    events = get_report_events(audit_log, "report_1")
    locks = compute_cascading_lock(events)

    assert is_downstream_locked("gate_1", locks) is False
    assert is_downstream_locked("gate_2", locks) is False
    assert is_downstream_locked("gate_3", locks) is False


def test_build_pipeline_state_clean_run(audit_log):
    _log_clean_full_run(audit_log)
    events = get_report_events(audit_log, "report_1")
    state = build_pipeline_state(events, chain_valid=True)

    assert state.total_events == 5
    assert state.chain_valid is True
    assert state.is_all_locked is False
    assert state.blocking_node is None

    assert state.nodes["gate_1"]["status"] == "complete"
    assert state.nodes["gate_1"]["actor"] == "Isaac Shukla"
    assert state.nodes["gate_4"]["status"] == "complete"
    assert state.nodes["gate_4"]["actor"] == "Divyank"


def test_build_pipeline_state_includes_node_definitions():
    events = []
    state = build_pipeline_state(events, chain_valid=True)

    for node in NODE_ORDER:
        assert state.nodes[node]["rules"] is not None
        assert state.nodes[node]["rules"].name == node


def test_build_pipeline_state_all_nodes_present():
    events = []
    state = build_pipeline_state(events, chain_valid=True)
    for node in NODE_ORDER:
        assert node in state.nodes


def test_build_pipeline_state_gate_3_block_sets_flags(audit_log):
    audit_log.log_event(
        report_id="report_1",
        event_type="gate_decision",
        payload={
            "gate": 3,
            "action": "reconciliation_reviewed",
            "internal_verdict": "block",
            "external_verdict": "pass",
        },
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    events = get_report_events(audit_log, "report_1")
    state = build_pipeline_state(events, chain_valid=True)

    assert state.is_all_locked is True
    assert state.blocking_node == "gate_3"
    assert state.nodes["gate_4"]["is_locked"] is True
    assert state.nodes["pdf_export"]["is_locked"] is True


def test_describe_node_shows_complete_status():
    state = build_pipeline_state([], chain_valid=True)
    state.nodes["gate_1"]["status"] = "complete"
    state.nodes["gate_1"]["actor"] = "Isaac Shukla"

    desc = describe_node("gate_1", state)
    assert "✓" in desc
    assert "complete" in desc
    assert "Isaac Shukla" in desc


def test_describe_node_shows_waiting_status():
    state = build_pipeline_state([], chain_valid=True)
    state.nodes["gate_2"]["status"] = "waiting"

    desc = describe_node("gate_2", state)
    assert "⏳" in desc
    assert "waiting" in desc


def test_describe_node_shows_blocked_status():
    state = build_pipeline_state([], chain_valid=True)
    state.nodes["gate_4"]["status"] = "blocked"
    state.blocking_node = "gate_3"

    desc = describe_node("gate_4", state)
    assert "✗" in desc
    assert "blocked" in desc
    assert "gate_3" in desc


def test_describe_pipeline_includes_all_nodes(audit_log):
    _log_clean_full_run(audit_log)
    events = get_report_events(audit_log, "report_1")
    state = build_pipeline_state(events, chain_valid=True)

    desc = describe_pipeline(state)
    for node in NODE_ORDER:
        assert node in desc


def test_describe_pipeline_shows_chain_validity():
    state = build_pipeline_state([], chain_valid=True)
    desc = describe_pipeline(state)
    assert "✓ valid" in desc

    state.chain_valid = False
    desc = describe_pipeline(state)
    assert "✗ BROKEN" in desc


def test_describe_pipeline_warns_on_locked_pipeline():
    state = build_pipeline_state([], chain_valid=True)
    state.is_all_locked = True
    state.blocking_node = "gate_3"
    state.blocking_reason = "reconciliation block"

    desc = describe_pipeline(state)
    assert "⚠" in desc
    assert "LOCKED" in desc
    assert "gate_3" in desc
