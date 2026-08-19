"""Persists full pipeline state at each gate transition so a run's evidence
survives a restart, rather than living only in Streamlit session memory.

Every snapshot's hash is also chained into the tamper-evident audit log. Without
that, this table would just be a bigger, more bypassable version of the problem
the log exists to solve — a place where state could be quietly rewritten with
nothing to detect it.

Loading a snapshot verifies the COMPLETE global chain first, not merely the
snapshot and the single log row committing to it. Those two checks pass happily
while an earlier decision — a finding disposition, a threshold — has been
rewritten: the edited row still hashes correctly on its own, and the snapshot
was never touched. Only a walk from genesis sees the disagreement. Because the
chain is global rather than per-report, a corrupt row belonging to a different
report refuses recovery here too; that is a real availability cost, and the
remedy is a restored backup, never a repair.

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


class ChainIntegrityError(StateIntegrityError):
    """Raised when the complete global audit chain does not verify.

    Distinct from its parent on purpose. `StateIntegrityError` means *this one
    snapshot* disagrees with its own committed hashes; this means *the history
    behind it* no longer holds together. Different findings, different remedies,
    so they must not collapse into one signal — but it subclasses, so anything
    already catching StateIntegrityError keeps catching this too.

    Refusal is unconditional on ownership: the chain is global (see
    AuditLog._last_row_hash), so a break anywhere destroys the ordering
    guarantee for every row after it, across report boundaries. A corrupt row
    belonging to another report therefore refuses recovery of this one.

    Tamper-EVIDENT, not tamper-proof. This detects disagreement; it does not
    prevent anyone with write access to the file from causing it, does not
    identify who did, and does not establish when.
    """


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

    def record_chain_verification(
        self,
        report_id: str,
        snapshot: StateSnapshot,
        context: dict,
        actor: Optional[str] = None,
    ) -> None:
        """Record that the chain verified immediately before a recovery.

        Called AFTER the snapshot loads, not from inside _load(), because
        AuditLog._require_context() needs a workbook_hash and the only copy of
        it lives inside the snapshot that has not been read yet. So the ordering
        is forced: verify chain -> load snapshot -> restore -> record. The claim
        this row makes is "the chain verified immediately before this state was
        recovered", not that the two happened at the same instant.

        There is deliberately no failure counterpart. A broken chain is reported
        by raising, never by appending — see ChainIntegrityError.
        """
        self.audit_log.log_event(
            report_id=report_id,
            event_type="chain_verification",
            payload={
                "gate_name": snapshot.gate_name,
                "state_hash": snapshot.state_hash,
                "outcome": "verified",
                "verified_rows": self.audit_log.count_rows(),
            },
            actor=actor,
            context=context,
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
        # The complete chain is verified BEFORE the snapshots table is read, so
        # there is no window in which corrupt history has already produced state.
        # Verifying only the snapshot — its own hash, and the one log row that
        # commits to it — cannot see an edit made to an EARLIER decision: that
        # row still hashes correctly on its own, and the snapshot is untouched.
        # Only a walk from genesis catches it.
        chain_ok, broken_row_ids = self.audit_log.verify_chain()
        if not chain_ok:
            raise ChainIntegrityError(
                "refusing to recover pipeline state: the tamper-evident audit "
                f"chain does not verify at row(s) {', '.join(broken_row_ids)}. "
                "The recorded history behind this state no longer agrees with "
                "itself, so it cannot be relied on. This does not identify who "
                "changed it, when, or whether the change was deliberate. "
                "Restore audit.db from a backup and preserve the current file "
                "as it stands — it is evidence. Nothing has been repaired."
            )

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
