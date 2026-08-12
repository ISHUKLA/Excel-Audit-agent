"""Persists full pipeline state at each gate transition so a run's evidence
survives a restart, rather than living only in Streamlit session memory.

Every snapshot's hash is also chained into the tamper-evident audit log. Without
that, this table would just be a bigger, more bypassable version of the problem
the log exists to solve — a place where state could be quietly rewritten with
nothing to detect it.

DISCLOSURE: once a workbook has been through the pipeline, its contents live
durably in `audit.db`, not only in the original .xlsx. That is deliberate — it is
what evidence retention means — but it makes `audit.db` as sensitive as the
source workbook, and it needs the same handling. No code in this module can
solve that; it is stated in the README as a property of the design.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from core.audit_log import AuditLog
from core.models import StateSnapshot


class StateSerializationError(TypeError):
    """Raised when pipeline state cannot be serialized.

    A snapshot that cannot be reloaded is not a snapshot. Coercing unknown
    objects to strings would produce a file that looks like evidence and
    restores to something different, so this fails loudly instead.
    """


def _canonical_json(state: dict) -> str:
    try:
        return json.dumps(state, sort_keys=True, separators=(",", ":"))
    except TypeError as exc:
        raise StateSerializationError(
            f"pipeline state is not JSON-serializable, so it cannot be snapshotted: {exc}"
        ) from exc


class StateIntegrityError(RuntimeError):
    """Raised when recovered state no longer matches its committed hashes."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class StateStore:
    """Snapshots of pipeline state, sharing one database file with the audit log.

    One file to protect, not several.
    """

    def __init__(self, db_path: str = "audit.db", audit_log: Optional[AuditLog] = None):
        self.db_path = db_path
        self.audit_log = audit_log or AuditLog(db_path)
        self._init_db()

    def _connect(self):
        return self.audit_log._connect()

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id   TEXT NOT NULL,
                    gate_name   TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    state_json  TEXT NOT NULL,
                    state_hash  TEXT NOT NULL
                )
                """
            )
        finally:
            conn.close()

    def save_snapshot(
        self,
        report_id: str,
        gate_name: str,
        state: dict,
        context: dict,
        actor: Optional[str] = None,
    ) -> StateSnapshot:
        """Persist state at one gate transition, and chain its hash into the log.

        `context` is required because the audit log requires it — a snapshot
        recorded without knowing which workbook and code version produced it is
        not evidence of anything. It is passed straight through to log_event.
        """
        state_json = _canonical_json(state)
        state_hash = _sha256(state_json)
        captured_at = datetime.now(timezone.utc).isoformat()

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO snapshots (report_id, gate_name, captured_at, state_json, state_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (report_id, gate_name, captured_at, state_json, state_hash),
            )
        finally:
            conn.close()

        # The snapshot's existence and hash go into the chain. Editing the
        # snapshots table afterwards leaves state_json disagreeing with the
        # state_hash committed here, and that disagreement is detectable.
        self.audit_log.log_event(
            report_id=report_id,
            event_type="state_snapshot",
            payload={"gate_name": gate_name, "state_hash": state_hash},
            actor=actor,
            context=context,
        )

        return StateSnapshot(
            report_id=report_id,
            gate_name=gate_name,
            captured_at=datetime.fromisoformat(captured_at),
            state_json=state_json,
            state_hash=state_hash,
        )

    def load_latest_snapshot(self, report_id: str) -> Optional[StateSnapshot]:
        """The most recent snapshot for a report — the resume point after a restart."""
        return self._load(
            "SELECT * FROM snapshots WHERE report_id = ? ORDER BY snapshot_id DESC LIMIT 1",
            (report_id,),
        )

    def load_snapshot(self, report_id: str, gate_name: str) -> Optional[StateSnapshot]:
        """The most recent snapshot for one specific gate.

        For looking back at exactly what the state was at a point in the run —
        what the findings looked like before Gate 2, for instance. If a gate was
        re-run, this returns the latest attempt.
        """
        return self._load(
            "SELECT * FROM snapshots WHERE report_id = ? AND gate_name = ? "
            "ORDER BY snapshot_id DESC LIMIT 1",
            (report_id, gate_name),
        )

    def _load(self, sql: str, params: tuple) -> Optional[StateSnapshot]:
        conn = self._connect()
        try:
            row = conn.execute(sql, params).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        recomputed_hash = _sha256(row["state_json"])
        if recomputed_hash != row["state_hash"]:
            raise StateIntegrityError(
                f"refusing recovered state for report {row['report_id']!r} at "
                f"{row['gate_name']!r}: snapshot content hash does not match"
            )

        matching_commitment = False
        for event in self.audit_log.get_rows(row["report_id"]):
            if event["event_type"] != "state_snapshot":
                continue
            if _sha256(event["payload_json"]) != event["payload_hash"]:
                continue
            try:
                payload = json.loads(event["payload_json"])
            except json.JSONDecodeError:
                continue
            if (
                payload.get("gate_name") == row["gate_name"]
                and payload.get("state_hash") == row["state_hash"]
            ):
                matching_commitment = True
                break
        if not matching_commitment:
            raise StateIntegrityError(
                f"refusing recovered state for report {row['report_id']!r} at "
                f"{row['gate_name']!r}: no intact audit-log commitment matches the snapshot"
            )

        return StateSnapshot(
            report_id=row["report_id"],
            gate_name=row["gate_name"],
            captured_at=datetime.fromisoformat(row["captured_at"]),
            state_json=row["state_json"],
            state_hash=row["state_hash"],
        )
