"""Tests for core/audit_log.py: insertion order, and append-only enforced at the DB level."""

import sqlite3

import pytest

from core.audit_log import AuditLog


@pytest.fixture
def audit_log(tmp_path):
    return AuditLog(str(tmp_path / "audit.db"))


def test_creates_db_file(tmp_path):
    db_path = tmp_path / "audit.db"
    assert not db_path.exists()
    AuditLog(str(db_path))
    assert db_path.exists()


def test_log_decision_returns_in_insertion_order(audit_log):
    audit_log.log_decision("RPT-001", 1, "F-001", "confirmed", "reviewed by actuary", "apoorva")
    audit_log.log_decision("RPT-001", 2, "F-002", "overridden", "known false positive", "apoorva")
    audit_log.log_decision("RPT-001", 3, None, "signed_off", None, "apoorva")

    decisions = audit_log.get_decisions("RPT-001")

    assert len(decisions) == 3
    assert [d["action"] for d in decisions] == ["confirmed", "overridden", "signed_off"]
    assert [d["gate_number"] for d in decisions] == [1, 2, 3]


def test_get_decisions_scoped_to_report_id(audit_log):
    audit_log.log_decision("RPT-001", 1, "F-001", "confirmed", "reason", "apoorva")
    audit_log.log_decision("RPT-002", 1, "F-001", "confirmed", "reason", "apoorva")

    assert len(audit_log.get_decisions("RPT-001")) == 1
    assert len(audit_log.get_decisions("RPT-002")) == 1


def test_update_is_rejected_at_the_database_level(audit_log):
    audit_log.log_decision("RPT-001", 1, "F-001", "confirmed", "reason", "apoorva")

    with sqlite3.connect(audit_log.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE decisions SET action = 'overridden' WHERE report_id = 'RPT-001'"
            )

    assert audit_log.get_decisions("RPT-001")[0]["action"] == "confirmed"


def test_delete_is_rejected_at_the_database_level(audit_log):
    audit_log.log_decision("RPT-001", 1, "F-001", "confirmed", "reason", "apoorva")

    with sqlite3.connect(audit_log.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM decisions WHERE report_id = 'RPT-001'")

    assert len(audit_log.get_decisions("RPT-001")) == 1
