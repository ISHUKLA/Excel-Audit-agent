"""Regression coverage for direct-SQL tampering with audit-log metadata."""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

import demo_cases
from agents.orchestrator import Orchestrator
from core.audit_log import AuditLog, _event_hash, _sha256
from core.models import FileContext
from core.workbook_identity import sha256_bytes


CONTEXT = {"workbook_hash": "a" * 64, "code_version": "0.1.0"}


def _log_two_events(audit_log: AuditLog) -> None:
    audit_log.log_event(
        "RPT-001",
        "gate_decision",
        {"gate": 1, "action": "context_confirmed"},
        "Isaac Shukla",
        CONTEXT,
    )
    audit_log.log_event(
        "RPT-001",
        "gate_decision",
        {"gate": 2, "finding_id": "F-001", "action": "confirmed"},
        "Isaac Shukla",
        CONTEXT,
    )


def _log_three_events(audit_log: AuditLog) -> None:
    _log_two_events(audit_log)
    audit_log.log_event(
        "RPT-001",
        "report_approved",
        {"gate": 4, "role": "Senior Actuary"},
        "Isaac Shukla",
        CONTEXT,
    )


def _new_log(tmp_path, name: str, *, rows: int = 3) -> AuditLog:
    case_dir = tmp_path / name
    case_dir.mkdir()
    audit_log = AuditLog(str(case_dir / "audit.db"))
    if rows == 2:
        _log_two_events(audit_log)
    else:
        _log_three_events(audit_log)
    return audit_log


def _drop_trigger(audit_log: AuditLog, trigger_name: str) -> None:
    with sqlite3.connect(audit_log.db_path) as conn:
        conn.execute(f"DROP TRIGGER {trigger_name}")


def _assert_and_show(label: str, actual, expected) -> None:
    print(f"{label}: verify_chain() -> {actual!r}")
    assert actual == expected


def test_actor_tampering_is_detected(tmp_path) -> None:
    audit_log = _new_log(tmp_path, "actor", rows=2)

    with sqlite3.connect(audit_log.db_path) as conn:
        conn.execute("DROP TRIGGER log_rows_no_update")
        conn.execute(
            "UPDATE log_rows SET actor = 'Chief Risk Officer' WHERE row_id = 1"
        )

    actual = audit_log.verify_chain()
    _assert_and_show("actor changed", actual, (False, ["1"]))


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("event_type", "llm_call"),
        ("report_id", "RPT-MOVED"),
    ],
)
def test_other_identifying_metadata_tampering_is_detected(
    tmp_path, field: str, new_value: str
) -> None:
    audit_log = _new_log(tmp_path, field)
    _drop_trigger(audit_log, "log_rows_no_update")

    with sqlite3.connect(audit_log.db_path) as conn:
        conn.execute(f"UPDATE log_rows SET {field} = ? WHERE row_id = 1", (new_value,))

    _assert_and_show(f"{field} changed", audit_log.verify_chain(), (False, ["1"]))


def test_timestamp_tampering_is_already_detected(tmp_path) -> None:
    audit_log = _new_log(tmp_path, "timestamp")
    _drop_trigger(audit_log, "log_rows_no_update")

    with sqlite3.connect(audit_log.db_path) as conn:
        conn.execute(
            "UPDATE log_rows SET timestamp = '2030-01-01T00:00:00+00:00' "
            "WHERE row_id = 1"
        )

    _assert_and_show("timestamp changed", audit_log.verify_chain(), (False, ["1"]))


def test_single_character_payload_tampering_is_already_detected(tmp_path) -> None:
    audit_log = _new_log(tmp_path, "payload")
    _drop_trigger(audit_log, "log_rows_no_update")

    with sqlite3.connect(audit_log.db_path) as conn:
        original = conn.execute(
            "SELECT payload_json FROM log_rows WHERE row_id = 2"
        ).fetchone()[0]
        changed = original.replace('"confirmed"', '"xonfirmed"')
        assert changed != original
        conn.execute(
            "UPDATE log_rows SET payload_json = ? WHERE row_id = 2", (changed,)
        )

    _assert_and_show("payload_json changed", audit_log.verify_chain(), (False, ["2"]))


def test_reordering_adjacent_row_ids_is_already_detected(tmp_path) -> None:
    audit_log = _new_log(tmp_path, "reordering")
    _drop_trigger(audit_log, "log_rows_no_update")

    with sqlite3.connect(audit_log.db_path) as conn:
        conn.execute("UPDATE log_rows SET row_id = -1 WHERE row_id = 1")
        conn.execute("UPDATE log_rows SET row_id = 1 WHERE row_id = 2")
        conn.execute("UPDATE log_rows SET row_id = 2 WHERE row_id = -1")

    _assert_and_show("adjacent row_ids reordered", audit_log.verify_chain(), (False, ["1", "2", "3"]))


def test_deleting_a_middle_row_is_already_detected(tmp_path) -> None:
    audit_log = _new_log(tmp_path, "middle-deletion")
    _drop_trigger(audit_log, "log_rows_no_delete")

    with sqlite3.connect(audit_log.db_path) as conn:
        conn.execute("DELETE FROM log_rows WHERE row_id = 2")

    _assert_and_show("middle row deleted", audit_log.verify_chain(), (False, ["3"]))


def test_inserting_a_fabricated_row_inside_the_chain_is_already_detected(tmp_path) -> None:
    audit_log = _new_log(tmp_path, "fabricated-row")
    _drop_trigger(audit_log, "log_rows_no_update")

    with sqlite3.connect(audit_log.db_path) as conn:
        row_two = conn.execute(
            "SELECT row_hash FROM log_rows WHERE row_id = 2"
        ).fetchone()
        conn.execute("UPDATE log_rows SET row_id = 4 WHERE row_id = 3")

        payload_json = json.dumps(
            {"action": "fabricated", "context": CONTEXT, "gate": 3},
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_hash = _sha256(payload_json)
        timestamp = "2026-09-19T12:00:00+00:00"
        fake_event_hash = _event_hash(
            report_id="RPT-001",
            event_type="gate_decision",
            actor="Fabricated Actor",
            timestamp=timestamp,
            payload_hash=payload_hash,
        )
        fake_row_hash = _sha256(row_two[0] + fake_event_hash)
        conn.execute(
            """
            INSERT INTO log_rows (
                row_id, report_id, event_type, payload_json, payload_hash,
                prev_row_hash, row_hash, timestamp, actor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                3,
                "RPT-001",
                "gate_decision",
                payload_json,
                payload_hash,
                row_two[0],
                fake_row_hash,
                timestamp,
                "Fabricated Actor",
            ),
        )

    _assert_and_show("fabricated row inserted", audit_log.verify_chain(), (False, ["4"]))


def test_deleting_the_last_row_remains_internally_consistent(tmp_path) -> None:
    audit_log = _new_log(tmp_path, "tail-deletion")
    checkpoint_path = tmp_path / "external-checkpoints.jsonl"
    checkpoint = audit_log.record_checkpoint(str(checkpoint_path))
    assert set(checkpoint) == {"last_row_id", "last_row_hash", "recorded_at"}
    assert checkpoint["last_row_id"] == 3
    assert audit_log.verify_against_checkpoint(str(checkpoint_path)) == (True, [])

    _drop_trigger(audit_log, "log_rows_no_delete")

    with sqlite3.connect(audit_log.db_path) as conn:
        conn.execute("DELETE FROM log_rows WHERE row_id = 3")

    _assert_and_show("last row deleted", audit_log.verify_chain(), (True, []))
    checkpoint_verdict = audit_log.verify_against_checkpoint(str(checkpoint_path))
    print(f"last row deleted: verify_against_checkpoint() -> {checkpoint_verdict!r}")
    assert checkpoint_verdict[0] is False
    assert "rows may have been removed after the checkpoint" in checkpoint_verdict[1][0]


def test_real_case_2_gate_event_actor_tampering_is_detected(tmp_path) -> None:
    audit_log = AuditLog(str(tmp_path / "case-2-audit.db"))
    orchestrator = Orchestrator(
        audit_log=audit_log,
        code_version="metadata-tampering-regression",
    )
    case = demo_cases.load_case(2)
    workbook_hash = sha256_bytes(case["workbook_bytes"])
    file_context = FileContext(
        filename="case_2_spreadsheet_control_failures.xlsx",
        description=case["description"],
        user_role="actuary",
        entity=case["entity"],
        period=case["period"],
        currency=case["currency"],
        basis=case["basis"],
        confirmed_workbook_hash=workbook_hash,
        uploaded_at=datetime.now(timezone.utc),
    )

    report_id, _, findings = orchestrator.run(
        case["workbook_bytes"],
        file_context,
        expected_workbook_hash=workbook_hash,
        context_confirmed=True,
        actor="Isaac Shukla",
    )
    decided_at = datetime.now(timezone.utc)
    reviewed_findings = [
        finding.model_copy(
            update={
                "human_decision": "confirmed",
                "human_reason": "Reviewed for metadata-tampering regression coverage.",
                "decided_by": "Isaac Shukla",
                "decided_at": decided_at,
            }
        )
        for finding in findings
    ]
    orchestrator.submit_gate2_decisions(
        report_id,
        reviewed_findings,
        ["Reserve Calculation!B14"],
        actor="Isaac Shukla",
    )
    gate_rows = [
        row for row in audit_log.get_rows(report_id) if row["event_type"] == "gate_decision"
    ]
    assert len(gate_rows) == 2
    gate_row_id = gate_rows[-1]["row_id"]

    _drop_trigger(audit_log, "log_rows_no_update")
    with sqlite3.connect(audit_log.db_path) as conn:
        conn.execute(
            "UPDATE log_rows SET actor = 'Chief Risk Officer' WHERE row_id = ?",
            (gate_row_id,),
        )

    actual = audit_log.verify_chain()
    print(
        "Case 2 real orchestrator gate_decision actor changed: "
        f"verify_chain() -> {actual!r}"
    )
    assert actual == (False, [str(gate_row_id)])
