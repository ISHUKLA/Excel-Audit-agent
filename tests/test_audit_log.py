"""Tests for core/audit_log.py — chain integrity, append-only enforcement, and
that tampering done outside this module is actually detected.

The raw-SQL tampering tests are the point of this file. Everything else confirms
the code compiles and behaves; only those confirm the control works.
"""

import sqlite3

import pytest

from core.audit_log import GENESIS_HASH, AuditContextError, AuditLog, _sha256

CONTEXT = {"workbook_hash": "a" * 64, "code_version": "0.1.0"}


@pytest.fixture
def audit_log(tmp_path):
    return AuditLog(str(tmp_path / "audit.db"))


def _log_three(log):
    log.log_event("RPT-001", "gate_decision", {"gate": 1, "action": "context_confirmed"}, "Isaac Shukla", CONTEXT)
    log.log_event("RPT-001", "gate_decision", {"gate": 2, "finding_id": "F-001", "action": "confirmed"}, "Isaac Shukla", CONTEXT)
    log.log_event("RPT-001", "report_approved", {"gate": 4, "role": "Senior Actuary"}, "Isaac Shukla", CONTEXT)


def _drop_triggers(db_path):
    """Simulate an attacker with file access removing the append-only controls.

    This is not a workaround for the test's convenience — it is the threat the
    hash chain exists to cover, since DB triggers protect nothing against
    someone who can write to the file.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER IF EXISTS log_rows_no_update")
        conn.execute("DROP TRIGGER IF EXISTS log_rows_no_delete")


# --------------------------------------------------------------------------
# clean case
# --------------------------------------------------------------------------


def test_creates_db_file(tmp_path):
    db_path = tmp_path / "audit.db"
    assert not db_path.exists()
    AuditLog(str(db_path))
    assert db_path.exists()


def test_three_events_returned_in_insertion_order_and_correctly_chained(audit_log):
    _log_three(audit_log)
    rows = audit_log.get_rows("RPT-001")

    assert len(rows) == 3
    assert [r["event_type"] for r in rows] == ["gate_decision", "gate_decision", "report_approved"]
    assert [r["row_id"] for r in rows] == sorted(r["row_id"] for r in rows)

    assert rows[0]["prev_row_hash"] == GENESIS_HASH
    assert rows[1]["prev_row_hash"] == rows[0]["row_hash"]
    assert rows[2]["prev_row_hash"] == rows[1]["row_hash"]

    for row in rows:
        recomputed = _sha256(row["prev_row_hash"] + row["payload_hash"] + row["timestamp"])
        assert recomputed == row["row_hash"]


def test_log_event_returns_a_populated_audit_log_row(audit_log):
    row = audit_log.log_event("RPT-001", "llm_call", {"tab": "Provisions"}, "Isaac Shukla", CONTEXT)
    assert row.row_id == 1
    assert row.prev_row_hash == GENESIS_HASH
    assert row.event_type == "llm_call"
    assert row.actor == "Isaac Shukla"


def test_verify_chain_passes_on_an_untampered_log(audit_log):
    _log_three(audit_log)
    assert audit_log.verify_chain() == (True, [])


def test_verify_chain_passes_on_an_empty_log(audit_log):
    assert audit_log.verify_chain() == (True, [])


def test_get_rows_is_scoped_to_one_report(audit_log):
    audit_log.log_event("RPT-001", "gate_decision", {"gate": 1}, "Isaac Shukla", CONTEXT)
    audit_log.log_event("RPT-002", "gate_decision", {"gate": 1}, "Isaac Shukla", CONTEXT)
    assert len(audit_log.get_rows("RPT-001")) == 1
    assert len(audit_log.get_rows("RPT-002")) == 1


def test_chain_is_global_not_per_report(audit_log):
    """A second report's first row chains onto the first report's last row, so
    removing an entire report's rows is detectable too."""
    first = audit_log.log_event("RPT-001", "gate_decision", {"gate": 1}, None, CONTEXT)
    second = audit_log.log_event("RPT-002", "gate_decision", {"gate": 1}, None, CONTEXT)
    assert second.prev_row_hash == first.row_hash


# --------------------------------------------------------------------------
# context is chained, not merely recorded
# --------------------------------------------------------------------------


def test_context_is_folded_into_the_hashed_payload(audit_log):
    """The same event under a different code_version must hash differently, or
    the context isn't really chained."""
    row_a = audit_log.log_event("RPT-001", "gate_decision", {"gate": 1}, None, CONTEXT)
    row_b = audit_log.log_event(
        "RPT-001", "gate_decision", {"gate": 1}, None, {**CONTEXT, "code_version": "0.2.0"}
    )
    assert row_a.payload_hash != row_b.payload_hash

    stored = audit_log.get_rows("RPT-001")[0]
    assert '"code_version":"0.1.0"' in stored["payload_json"]


def test_thresholds_in_context_are_part_of_the_hash(audit_log):
    row_a = audit_log.log_event(
        "RPT-001", "gate_decision", {"gate": 3}, None, {**CONTEXT, "pct_threshold": 0.01}
    )
    row_b = audit_log.log_event(
        "RPT-001", "gate_decision", {"gate": 3}, None, {**CONTEXT, "pct_threshold": 0.05}
    )
    assert row_a.payload_hash != row_b.payload_hash


def test_missing_workbook_hash_raises_rather_than_logging_an_incomplete_row(audit_log):
    with pytest.raises(AuditContextError, match="workbook_hash"):
        audit_log.log_event("RPT-001", "gate_decision", {"gate": 1}, None, {"code_version": "0.1.0"})
    assert audit_log.get_rows("RPT-001") == []


def test_missing_code_version_raises_rather_than_logging_an_incomplete_row(audit_log):
    with pytest.raises(AuditContextError, match="code_version"):
        audit_log.log_event("RPT-001", "gate_decision", {"gate": 1}, None, {"workbook_hash": "a" * 64})
    assert audit_log.get_rows("RPT-001") == []


def test_empty_string_context_value_is_treated_as_missing(audit_log):
    with pytest.raises(AuditContextError):
        audit_log.log_event(
            "RPT-001", "gate_decision", {"gate": 1}, None, {"workbook_hash": "", "code_version": "0.1.0"}
        )


def test_pre_parse_events_use_an_explicit_sentinel_not_an_omission(audit_log):
    """Gate 1 runs before parsing, so there is no workbook hash yet. The caller
    says so explicitly; it cannot be mistaken for a real digest."""
    row = audit_log.log_event(
        "RPT-001",
        "gate_decision",
        {"gate": 1},
        "Isaac Shukla",
        {"workbook_hash": "not_yet_parsed", "code_version": "0.1.0"},
    )
    assert row.row_id == 1


def test_payload_may_not_shadow_the_context_key(audit_log):
    with pytest.raises(AuditContextError, match="collides"):
        audit_log.log_event("RPT-001", "gate_decision", {"context": "spoofed"}, None, CONTEXT)


# --------------------------------------------------------------------------
# append-only, enforced at the database level
# --------------------------------------------------------------------------


def test_update_is_rejected_at_the_database_level(audit_log):
    audit_log.log_event("RPT-001", "gate_decision", {"gate": 1}, None, CONTEXT)
    with sqlite3.connect(audit_log.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE log_rows SET event_type = 'llm_call'")


def test_delete_is_rejected_at_the_database_level(audit_log):
    audit_log.log_event("RPT-001", "gate_decision", {"gate": 1}, None, CONTEXT)
    with sqlite3.connect(audit_log.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM log_rows")
    assert len(audit_log.get_rows("RPT-001")) == 1


# --------------------------------------------------------------------------
# the tamper-evidence tests — the actual proof the control works
# --------------------------------------------------------------------------


def test_editing_a_payload_with_raw_sql_is_detected(audit_log):
    """The CRO's threat, end to end: someone with file access drops the
    append-only triggers, rewrites a decision, and closes the file. The row
    looks fine. The chain does not."""
    _log_three(audit_log)
    assert audit_log.verify_chain() == (True, [])

    _drop_triggers(audit_log.db_path)
    with sqlite3.connect(audit_log.db_path) as conn:
        conn.execute(
            "UPDATE log_rows SET payload_json = ? WHERE row_id = 2",
            ('{"action":"dismissed","gate":2,"context":{"code_version":"0.1.0"}}',),
        )

    ok, broken = audit_log.verify_chain()
    assert ok is False
    assert broken == ["2"]


def test_deleting_a_row_with_raw_sql_is_detected(audit_log):
    """A deleted row leaves no trace of itself — but the row after it still
    points at a hash that is no longer there."""
    _log_three(audit_log)
    _drop_triggers(audit_log.db_path)
    with sqlite3.connect(audit_log.db_path) as conn:
        conn.execute("DELETE FROM log_rows WHERE row_id = 2")

    ok, broken = audit_log.verify_chain()
    assert ok is False
    assert broken == ["3"]


def test_altering_a_stored_row_hash_is_detected(audit_log):
    """Recomputing the hash to match an edited payload doesn't help either — the
    next row committed to the old value."""
    _log_three(audit_log)
    _drop_triggers(audit_log.db_path)
    with sqlite3.connect(audit_log.db_path) as conn:
        conn.execute("UPDATE log_rows SET row_hash = ? WHERE row_id = 1", ("f" * 64,))

    ok, broken = audit_log.verify_chain()
    assert ok is False
    assert "1" in broken


def test_a_single_edit_is_reported_once_not_cascaded(audit_log):
    """An auditor reading the output needs to know which row was touched, not a
    list of every row that happens to come after it."""
    _log_three(audit_log)
    _drop_triggers(audit_log.db_path)
    with sqlite3.connect(audit_log.db_path) as conn:
        conn.execute("UPDATE log_rows SET payload_json = '{}' WHERE row_id = 2")

    _, broken = audit_log.verify_chain()
    assert broken == ["2"]


# --------------------------------------------------------------------------
# pre-Step-3 history remains readable
# --------------------------------------------------------------------------


def test_legacy_decisions_table_is_readable_when_present(tmp_path):
    db_path = str(tmp_path / "audit.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE decisions (report_id TEXT, gate_number INTEGER, finding_id TEXT, "
            "action TEXT, reason TEXT, user_name TEXT, timestamp TEXT)"
        )
        conn.execute(
            "INSERT INTO decisions VALUES ('RPT-OLD', 1, NULL, 'context_confirmed', 'r', 'Apoorva Ranjan', '2026-08-03T13:23:45')"
        )

    log = AuditLog(db_path)
    legacy = log.get_legacy_decisions("RPT-OLD")
    assert len(legacy) == 1
    assert legacy[0]["action"] == "context_confirmed"


def test_legacy_reader_returns_empty_on_a_fresh_database(audit_log):
    assert audit_log.get_legacy_decisions("RPT-001") == []
