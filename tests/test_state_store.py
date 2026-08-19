"""Tests for core/state_store.py — that state survives a restart, and that a
snapshot's integrity is verifiable through the same chain as a decision's."""

import hashlib
import json
import sqlite3

import pytest

from core.audit_log import AuditContextError, AuditLog
from core.state_store import (
    ChainIntegrityError,
    StateIntegrityError,
    StateSerializationError,
    StateStore,
)

CONTEXT = {"workbook_hash": "a" * 64, "code_version": "0.1.0"}

STATE = {
    "findings": [
        {"finding_id": "F-001", "severity": "warning", "human_decision": "confirmed"},
        {"finding_id": "F-002", "severity": "info", "human_decision": "dismissed"},
    ],
    "authoritative_outputs": ["Provisions!C5"],
    "tab_names": ["Provisions", "Assumptions"],
}


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "audit.db")


@pytest.fixture
def store(db_path):
    return StateStore(db_path)


# --------------------------------------------------------------------------
# restart recovery
# --------------------------------------------------------------------------


def test_state_survives_a_restart(db_path):
    """Save, throw away every in-memory object, reopen from disk, and confirm
    the state that comes back is the state that went in."""
    StateStore(db_path).save_snapshot("RPT-001", "gate_2_findings_review", STATE, CONTEXT)

    # A fresh instance against the same file — nothing carried over in memory.
    reloaded = StateStore(db_path).load_latest_snapshot("RPT-001")

    assert reloaded is not None
    assert json.loads(reloaded.state_json) == STATE
    assert reloaded.gate_name == "gate_2_findings_review"
    assert reloaded.report_id == "RPT-001"


def test_load_latest_returns_the_most_recent_gate(store):
    store.save_snapshot("RPT-001", "gate_1_context", {"step": 1}, CONTEXT)
    store.save_snapshot("RPT-001", "gate_2_findings_review", {"step": 2}, CONTEXT)
    store.save_snapshot("RPT-001", "gate_3_reconciliation", {"step": 3}, CONTEXT)

    latest = store.load_latest_snapshot("RPT-001")
    assert latest.gate_name == "gate_3_reconciliation"
    assert json.loads(latest.state_json) == {"step": 3}


def test_load_snapshot_retrieves_one_specific_point_in_the_run(store):
    store.save_snapshot("RPT-001", "gate_1_context", {"step": 1}, CONTEXT)
    store.save_snapshot("RPT-001", "gate_2_findings_review", {"step": 2}, CONTEXT)
    store.save_snapshot("RPT-001", "gate_3_reconciliation", {"step": 3}, CONTEXT)

    earlier = store.load_snapshot("RPT-001", "gate_1_context")
    assert json.loads(earlier.state_json) == {"step": 1}


def test_a_rerun_gate_returns_its_latest_attempt(store):
    store.save_snapshot("RPT-001", "gate_2_findings_review", {"attempt": 1}, CONTEXT)
    store.save_snapshot("RPT-001", "gate_2_findings_review", {"attempt": 2}, CONTEXT)
    assert json.loads(store.load_snapshot("RPT-001", "gate_2_findings_review").state_json) == {
        "attempt": 2
    }


def test_snapshots_are_scoped_to_one_report(store):
    store.save_snapshot("RPT-001", "gate_1_context", {"report": 1}, CONTEXT)
    store.save_snapshot("RPT-002", "gate_1_context", {"report": 2}, CONTEXT)
    assert json.loads(store.load_latest_snapshot("RPT-002").state_json) == {"report": 2}


def test_loading_an_unknown_report_returns_none_not_an_empty_snapshot(store):
    assert store.load_latest_snapshot("RPT-NONE") is None
    assert store.load_snapshot("RPT-NONE", "gate_1_context") is None


def test_loading_a_gate_that_was_never_reached_returns_none(store):
    store.save_snapshot("RPT-001", "gate_1_context", {"step": 1}, CONTEXT)
    assert store.load_snapshot("RPT-001", "gate_4_approval") is None


# --------------------------------------------------------------------------
# the snapshot is chained into the audit log
# --------------------------------------------------------------------------


def test_saving_a_snapshot_writes_a_matching_state_snapshot_event(store):
    snapshot = store.save_snapshot("RPT-001", "gate_2_findings_review", STATE, CONTEXT)

    events = [r for r in store.audit_log.get_rows("RPT-001") if r["event_type"] == "state_snapshot"]
    assert len(events) == 1

    payload = json.loads(events[0]["payload_json"])
    assert payload["gate_name"] == "gate_2_findings_review"
    assert payload["state_hash"] == snapshot.state_hash


def test_the_snapshot_hash_is_the_hash_of_the_stored_state(store):
    snapshot = store.save_snapshot("RPT-001", "gate_2_findings_review", STATE, CONTEXT)
    assert snapshot.state_hash == hashlib.sha256(snapshot.state_json.encode()).hexdigest()


def test_snapshot_events_join_the_same_chain_as_decisions(store):
    """A snapshot is not a second-class event — it sits in the same chain, so
    verify_chain covers it."""
    store.audit_log.log_event("RPT-001", "gate_decision", {"gate": 1}, "Isaac Shukla", CONTEXT)
    store.save_snapshot("RPT-001", "gate_1_context", STATE, CONTEXT)
    store.audit_log.log_event("RPT-001", "gate_decision", {"gate": 2}, "Isaac Shukla", CONTEXT)

    rows = store.audit_log.get_rows("RPT-001")
    assert [r["event_type"] for r in rows] == ["gate_decision", "state_snapshot", "gate_decision"]
    assert rows[1]["prev_row_hash"] == rows[0]["row_hash"]
    assert rows[2]["prev_row_hash"] == rows[1]["row_hash"]
    assert store.audit_log.verify_chain() == (True, [])


def test_rewriting_a_snapshot_contradicts_the_hash_chained_in_the_log(store):
    """The reason chaining matters: the snapshots table has no triggers, so
    state_json can be rewritten freely — but the hash committed to the log
    still describes the original, and the two no longer agree."""
    snapshot = store.save_snapshot("RPT-001", "gate_2_findings_review", STATE, CONTEXT)

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE snapshots SET state_json = ? WHERE report_id = 'RPT-001'",
            ('{"findings":[]}',),
        )

    with pytest.raises(StateIntegrityError, match="snapshot content hash does not match"):
        store.load_latest_snapshot("RPT-001")

    event = [r for r in store.audit_log.get_rows("RPT-001") if r["event_type"] == "state_snapshot"][0]
    assert json.loads(event["payload_json"])["state_hash"] == snapshot.state_hash
    # The log itself is untouched — the tampering shows up as a mismatch
    # against it, not as a broken chain.
    assert store.audit_log.verify_chain() == (True, [])


def test_snapshot_with_no_matching_log_commitment_is_refused(store):
    store.save_snapshot("RPT-001", "gate_1_context", STATE, CONTEXT)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE snapshots SET gate_name = 'altered_gate' WHERE report_id = 'RPT-001'"
        )

    with pytest.raises(StateIntegrityError, match="no intact audit-log commitment"):
        store.load_latest_snapshot("RPT-001")


def test_context_is_required_for_a_snapshot_too(store):
    with pytest.raises(AuditContextError):
        store.save_snapshot("RPT-001", "gate_1_context", STATE, {"code_version": "0.1.0"})


# --------------------------------------------------------------------------
# messy input
# --------------------------------------------------------------------------


def test_unserializable_state_fails_loudly_rather_than_saving_a_lie(store):
    """Coercing an unknown object to a string would write something that looks
    like a snapshot and restores to something else."""

    class NotJSON:
        pass

    with pytest.raises(StateSerializationError):
        store.save_snapshot("RPT-001", "gate_1_context", {"parsed": NotJSON()}, CONTEXT)


def test_a_failed_snapshot_leaves_no_log_event(store):
    class NotJSON:
        pass

    with pytest.raises(StateSerializationError):
        store.save_snapshot("RPT-001", "gate_1_context", {"parsed": NotJSON()}, CONTEXT)

    assert store.audit_log.get_rows("RPT-001") == []


def test_empty_state_is_snapshottable(store):
    """An empty pipeline state is a legitimate thing to record — it means the
    run reached a gate with nothing in it, which is different from no snapshot."""
    snapshot = store.save_snapshot("RPT-001", "gate_1_context", {}, CONTEXT)
    assert snapshot.state_json == "{}"
    assert store.load_latest_snapshot("RPT-001").state_json == "{}"


def test_store_shares_one_database_file_with_the_audit_log(db_path):
    store = StateStore(db_path)
    store.save_snapshot("RPT-001", "gate_1_context", STATE, CONTEXT)

    with sqlite3.connect(db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"log_rows", "snapshots"} <= tables


def test_an_injected_audit_log_is_used_rather_than_a_second_one(db_path):
    log = AuditLog(db_path)
    store = StateStore(db_path, audit_log=log)
    store.save_snapshot("RPT-001", "gate_1_context", STATE, CONTEXT)
    assert store.audit_log is log
    assert len(log.get_rows("RPT-001")) == 1


# --------------------------------------------------------------------------
# the complete global chain is verified before a snapshot is loaded
# --------------------------------------------------------------------------


def _drop_update_trigger(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER log_rows_no_update")


def test_an_edited_earlier_log_row_refuses_the_load(store):
    """The gap this closes. The snapshot is pristine and its own commitment
    still verifies; the tampering is one row further back, where neither
    Check A nor Check B is looking."""
    store.audit_log.log_event("RPT-001", "gate_decision", {"gate": 2}, "Isaac Shukla", CONTEXT)
    store.save_snapshot("RPT-001", "gate_2_findings_review", STATE, CONTEXT)

    _drop_update_trigger(store.db_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE log_rows SET payload_json = '{}' WHERE row_id = 1")

    with pytest.raises(ChainIntegrityError, match="does not verify at row"):
        store.load_latest_snapshot("RPT-001")


def test_a_deleted_row_refuses_the_load(store):
    """Deletion edits no payload at all — it breaks the prev_row_hash linkage,
    which only a walk of the chain can see."""
    store.audit_log.log_event("RPT-001", "gate_decision", {"gate": 1}, None, CONTEXT)
    store.audit_log.log_event("RPT-001", "gate_decision", {"gate": 2}, None, CONTEXT)
    store.save_snapshot("RPT-001", "gate_2_findings_review", STATE, CONTEXT)

    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TRIGGER log_rows_no_delete")
        conn.execute("DELETE FROM log_rows WHERE row_id = 2")

    with pytest.raises(ChainIntegrityError):
        store.load_latest_snapshot("RPT-001")


def test_corruption_in_another_report_refuses_this_one(store):
    """The chain is global, so a break anywhere destroys the ordering guarantee
    for every row after it. Refusal is unconditional on ownership."""
    store.save_snapshot("RPT-001", "gate_1_context", STATE, CONTEXT)
    store.audit_log.log_event("RPT-002", "gate_decision", {"gate": 1}, None, CONTEXT)
    store.save_snapshot("RPT-002", "gate_1_context", STATE, CONTEXT)

    _drop_update_trigger(store.db_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE log_rows SET payload_json = '{}' WHERE report_id = 'RPT-002'")

    with pytest.raises(ChainIntegrityError):
        store.load_latest_snapshot("RPT-001")


def test_load_snapshot_by_gate_is_guarded_too(store):
    """Both public loaders go through _load(), so neither is a way around it."""
    store.save_snapshot("RPT-001", "gate_1_context", STATE, CONTEXT)
    _drop_update_trigger(store.db_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE log_rows SET payload_json = '{}' WHERE row_id = 1")

    with pytest.raises(ChainIntegrityError):
        store.load_snapshot("RPT-001", "gate_1_context")


def test_a_refused_load_changes_nothing_on_disk(store):
    """No repair, no quarantine, no truncation — the corrupt row stays exactly
    as found, because its current state is the evidence."""
    store.save_snapshot("RPT-001", "gate_1_context", STATE, CONTEXT)
    _drop_update_trigger(store.db_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE log_rows SET payload_json = '{}' WHERE row_id = 1")
        before = conn.execute("SELECT * FROM log_rows ORDER BY row_id").fetchall()

    with pytest.raises(ChainIntegrityError):
        store.load_latest_snapshot("RPT-001")

    with sqlite3.connect(store.db_path) as conn:
        after = conn.execute("SELECT * FROM log_rows ORDER BY row_id").fetchall()
    assert after == before


def test_chain_integrity_error_is_a_state_integrity_error(store):
    """Subclassing keeps existing callers working while keeping the two
    findings distinguishable — a broken history is not a broken snapshot."""
    assert issubclass(ChainIntegrityError, StateIntegrityError)


def test_an_intact_chain_still_loads_normally(store):
    """The guard must not refuse anything it has no reason to."""
    store.audit_log.log_event("RPT-001", "gate_decision", {"gate": 1}, None, CONTEXT)
    store.save_snapshot("RPT-001", "gate_1_context", STATE, CONTEXT)
    assert json.loads(store.load_latest_snapshot("RPT-001").state_json) == STATE


def test_an_unknown_report_on_an_intact_chain_still_returns_none(store):
    """Criterion 12 at this level: no snapshot is not the same as bad snapshot."""
    store.save_snapshot("RPT-001", "gate_1_context", STATE, CONTEXT)
    assert store.load_latest_snapshot("RPT-NONE") is None


def test_an_altered_stored_row_hash_refuses_the_load(store):
    """Recomputing a row's hash to cover an edit does not help either: the row
    AFTER it committed to the old value, so the linkage still disagrees. Proved
    for verify_chain() in test_audit_log.py; this proves recovery refuses on it."""
    store.audit_log.log_event("RPT-001", "gate_decision", {"gate": 1}, None, CONTEXT)
    store.audit_log.log_event("RPT-001", "gate_decision", {"gate": 2}, None, CONTEXT)
    store.save_snapshot("RPT-001", "gate_2_findings_review", STATE, CONTEXT)

    _drop_update_trigger(store.db_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE log_rows SET row_hash = ? WHERE row_id = 1", ("f" * 64,))

    with pytest.raises(ChainIntegrityError, match="does not verify at row"):
        store.load_latest_snapshot("RPT-001")
