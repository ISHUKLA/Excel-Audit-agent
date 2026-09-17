"""Tests for core/flight_recorder_queries.py.

Covers the clean path (a full run through all four gates) and the messy path
(a gate_3 block, and a tampered chain) — a query module over the audit log is
only trustworthy if it's exercised against a log that actually contains a
block and actually contains damage, not just a happy-path run.

Does not cover: concurrent readers, or every possible malformed payload_json
(only the "not valid JSON" case is exercised) — those are audit_log.py's own
test responsibilities, not this module's.
"""

import sqlite3

import pytest

from core.audit_log import AuditLog
from core.flight_recorder_queries import (
    extract_evidence_hash,
    get_cascading_locks,
    get_node_rules,
    get_node_status,
    get_report_events,
    verify_audit_chain,
)

CONTEXT = {"workbook_hash": "abc123", "code_version": "test"}


@pytest.fixture
def audit_log(tmp_path):
    return AuditLog(str(tmp_path / "audit.db"))


def _log_full_clean_run(audit_log: AuditLog, report_id: str) -> None:
    audit_log.log_event(
        report_id=report_id,
        event_type="gate_decision",
        payload={"gate": 1, "action": "context_confirmed", "context_match_verdict": "match"},
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    audit_log.log_event(
        report_id=report_id,
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
        report_id=report_id,
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
        report_id=report_id,
        event_type="llm_use_decision",
        payload={"decision": "decline", "disclosure_version": "v1"},
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    audit_log.log_event(
        report_id=report_id,
        event_type="report_approved",
        payload={"gate": 4, "action": "approval_record_created", "approval_name": "Divyank", "role": "cro"},
        actor="Divyank",
        context=CONTEXT,
    )


def test_get_report_events_clean_run_in_order(audit_log):
    _log_full_clean_run(audit_log, "report_1")
    events = get_report_events(audit_log, "report_1")

    assert len(events) == 5
    assert [e["event_type"] for e in events] == [
        "gate_decision",
        "gate_decision",
        "gate_decision",
        "llm_use_decision",
        "report_approved",
    ]
    # payload parsed into evidence, not left as an opaque blob
    assert events[1]["evidence"]["findings_count"] == 1
    assert events[2]["evidence"]["internal_verdict"] == "pass"
    assert events[4]["evidence"]["approval_name"] == "Divyank"


def test_get_report_events_only_returns_matching_report(audit_log):
    _log_full_clean_run(audit_log, "report_1")
    audit_log.log_event(
        report_id="report_2",
        event_type="gate_decision",
        payload={"gate": 1, "action": "context_confirmed"},
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    events = get_report_events(audit_log, "report_2")
    assert len(events) == 1


def test_get_node_status_complete_when_gate_event_present(audit_log):
    _log_full_clean_run(audit_log, "report_1")
    events = get_report_events(audit_log, "report_1")

    status = get_node_status(events, "gate_1")
    assert status["status"] == "complete"

    status = get_node_status(events, "gate_4")
    assert status["status"] == "complete"
    assert status["evidence_summary"]["approval_name"] == "Divyank"


def test_get_node_status_waiting_when_no_event_and_upstream_clean(audit_log):
    # Only gate_1 logged; nothing about gate_2 exists yet, and gate_1 didn't block.
    audit_log.log_event(
        report_id="report_1",
        event_type="gate_decision",
        payload={"gate": 1, "action": "context_confirmed", "context_match_verdict": "match"},
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    events = get_report_events(audit_log, "report_1")
    status = get_node_status(events, "gate_2")
    assert status["status"] == "waiting"


def test_get_node_status_agent_node_incomplete_when_gate_not_yet_logged(audit_log):
    events = []
    status = get_node_status(events, "anomaly_detector")
    assert status["status"] == "incomplete"
    assert "not independently visible" in status["evidence_summary"]["note"] or "isn't independently visible" in status["evidence_summary"]["note"]


def test_get_node_status_blocked_when_gate_3_records_block(audit_log):
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

    gate3_status = get_node_status(events, "gate_3")
    assert gate3_status["status"] == "blocked"

    gate4_status = get_node_status(events, "gate_4")
    assert gate4_status["status"] == "blocked"


def test_get_cascading_locks_locks_downstream_nodes_on_gate_3_block(audit_log):
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
    locks = get_cascading_locks(events)

    assert locks["blocking_node"] == "gate_3"
    assert locks["locked_nodes"] == ["optional_ai_doc", "gate_4", "pdf_export"]
    assert "external_verdict" in locks["blocking_reason"]


def test_get_cascading_locks_no_locks_on_clean_run(audit_log):
    _log_full_clean_run(audit_log, "report_1")
    events = get_report_events(audit_log, "report_1")
    locks = get_cascading_locks(events)
    assert locks == {"locked_nodes": [], "blocking_node": None, "blocking_reason": None}


def test_extract_evidence_hash_short_and_full(audit_log):
    _log_full_clean_run(audit_log, "report_1")
    events = get_report_events(audit_log, "report_1")

    short = extract_evidence_hash(events[0])
    full = extract_evidence_hash(events[0], short=False)

    assert len(short) == 8
    assert full.startswith(short)
    assert len(full) == 64  # sha256 hex digest


def test_get_node_rules_returns_allowed_prohibited_and_actor_type():
    rules = get_node_rules("gate_4")
    assert "allowed" in rules and "prohibited" in rules
    assert rules["actor_type"] == "human"
    assert any("cro" in item.lower() for item in rules["prohibited"])


def test_get_node_rules_unknown_node_raises():
    with pytest.raises(ValueError):
        get_node_rules("not_a_real_node")


def test_verify_audit_chain_valid_on_untampered_log(audit_log):
    _log_full_clean_run(audit_log, "report_1")
    is_valid, message = verify_audit_chain(audit_log, "report_1")
    assert is_valid is True
    assert "5 events" in message


def test_verify_audit_chain_detects_tamper_within_report(audit_log):
    _log_full_clean_run(audit_log, "report_1")

    # The append-only triggers reject UPDATE, same as they would for a real
    # tamper attempt — drop them first to simulate someone with file access
    # bypassing that control, which is exactly the scenario verify_chain()
    # exists to catch after the fact.
    conn = sqlite3.connect(audit_log.db_path)
    conn.execute("DROP TRIGGER IF EXISTS log_rows_no_update")
    conn.execute(
        "UPDATE log_rows SET payload_json = '{\"gate\": 1, \"action\": \"tampered\"}' WHERE row_id = 1"
    )
    conn.commit()
    conn.close()

    is_valid, message = verify_audit_chain(audit_log, "report_1")
    assert is_valid is False
    assert "row_id=1" in message
    assert "within report" in message
