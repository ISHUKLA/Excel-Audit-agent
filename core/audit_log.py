"""Hash-chained, tamper-evident SQLite log of every event in a pipeline run.

Tamper-EVIDENT, not tamper-proof. Two independent controls sit here, and the
distinction between them matters:

  1. Database-level triggers reject UPDATE and DELETE on `log_rows`, so
     append-only holds even for writes that bypass this module.
  2. A global hash chain across every row, so that if someone with file access
     removes those triggers — which they can — and then edits, deletes, or
     reorders rows, `verify_chain()` will detect it afterwards.

Neither control makes `audit.db` physically unmodifiable by anyone who can write
to the file. The first raises the effort required; the second makes the attempt
visible after the fact. Describe it that way in code, in the UI, and in the
report — never as "tamper-proof".
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from core.models import AuditLogRow

# The prev_row_hash of the very first row in the chain.
GENESIS_HASH = "0" * 64

# Every event must record which workbook and which code produced it. A decision
# with no idea what it was made about is not evidence of anything.
REQUIRED_CONTEXT_KEYS = ("workbook_hash", "code_version")

# For events logged before the workbook has been parsed (Gate 1 confirms the
# file description first), callers pass this in place of a real hash. It is
# deliberately not a valid sha256 so it can never be mistaken for one.
NOT_YET_PARSED = "not_yet_parsed"


class AuditContextError(ValueError):
    """Raised when log_event is called without the context the chain requires."""


def _canonical_json(payload: dict) -> str:
    """Deterministic JSON, so the same payload always hashes to the same digest."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, db_path: str = "audit.db"):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None so transactions are managed explicitly — log_event
        # needs the read-previous-hash and the insert to be one atomic step, or
        # two concurrent writers could chain onto the same predecessor.
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS log_rows (
                    row_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id     TEXT NOT NULL,
                    event_type    TEXT NOT NULL,
                    payload_json  TEXT NOT NULL,
                    payload_hash  TEXT NOT NULL,
                    prev_row_hash TEXT NOT NULL,
                    row_hash      TEXT NOT NULL,
                    timestamp     TEXT NOT NULL,
                    actor         TEXT
                )
                """
            )
            # Append-only at the database level, so it holds for writes that
            # bypass this class. Someone with file access can drop these; that
            # is what the hash chain is for.
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS log_rows_no_update
                BEFORE UPDATE ON log_rows
                BEGIN
                    SELECT RAISE(ABORT, 'audit log is append-only: UPDATE is not permitted');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS log_rows_no_delete
                BEFORE DELETE ON log_rows
                BEGIN
                    SELECT RAISE(ABORT, 'audit log is append-only: DELETE is not permitted');
                END
                """
            )
        finally:
            conn.close()

    def log_event(
        self,
        report_id: str,
        event_type: str,
        payload: dict,
        actor: Optional[str],
        context: dict,
    ) -> AuditLogRow:
        """Append one row to the chain.

        `context` is folded into the payload before hashing — the workbook hash,
        code version, and whatever thresholds or configuration were in effect are
        part of what gets chained, not metadata sitting beside it. A decision is
        only evidence if what it was decided against is chained with it.
        """
        self._require_context(context)
        if "context" in payload:
            raise AuditContextError(
                "payload may not contain a 'context' key — it collides with the "
                "folded-in context and would silently overwrite it"
            )

        folded = {**payload, "context": context}
        payload_json = _canonical_json(folded)
        payload_hash = _sha256(payload_json)
        timestamp = datetime.now(timezone.utc).isoformat()

        conn = self._connect()
        try:
            # IMMEDIATE takes the write lock up front, so no other writer can
            # read the same previous row_hash and fork the chain.
            conn.execute("BEGIN IMMEDIATE")
            prev_row_hash = self._last_row_hash(conn)
            row_hash = _sha256(prev_row_hash + payload_hash + timestamp)
            cursor = conn.execute(
                """
                INSERT INTO log_rows (
                    report_id, event_type, payload_json, payload_hash,
                    prev_row_hash, row_hash, timestamp, actor
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    event_type,
                    payload_json,
                    payload_hash,
                    prev_row_hash,
                    row_hash,
                    timestamp,
                    actor,
                ),
            )
            row_id = cursor.lastrowid
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

        return AuditLogRow(
            row_id=row_id,
            report_id=report_id,
            event_type=event_type,
            payload_hash=payload_hash,
            prev_row_hash=prev_row_hash,
            row_hash=row_hash,
            timestamp=datetime.fromisoformat(timestamp),
            actor=actor,
        )

    def get_rows(self, report_id: str) -> list[dict]:
        """Every row for one report, in insertion order."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM log_rows WHERE report_id = ? ORDER BY row_id ASC",
                (report_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def count_rows(self) -> int:
        """How many rows are in the chain. Read-only; adds nothing and changes nothing.

        Used to record how much of the log a verification actually covered, so
        "the chain verified" is accompanied by the size of what was walked.
        """
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) AS n FROM log_rows").fetchone()["n"]
        finally:
            conn.close()

    def verify_chain(self) -> tuple[bool, list[str]]:
        """Walk the whole log and report any row whose hashes no longer agree.

        This DETECTS tampering after the fact. It does not PREVENT anyone with
        write access to `audit.db` from attempting it — they can edit a row, drop
        a row, reorder rows, or remove the append-only triggers entirely. What
        they cannot do is make those changes agree with the chain, because each
        row's hash commits to the previous row's hash.

        Three ways a row is reported:
          - its payload_json no longer hashes to its stored payload_hash (edited)
          - its row_hash is not sha256(prev_row_hash + payload_hash + timestamp)
          - its prev_row_hash is not the previous surviving row's row_hash
            (a row was deleted, or rows were reordered)

        Returns (True, []) on an intact chain, or (False, [row_ids as strings]).
        """
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM log_rows ORDER BY row_id ASC").fetchall()
        finally:
            conn.close()

        broken: list[str] = []
        expected_prev = GENESIS_HASH
        for row in rows:
            row_id = str(row["row_id"])

            if _sha256(row["payload_json"]) != row["payload_hash"]:
                broken.append(row_id)
            elif row["prev_row_hash"] != expected_prev:
                broken.append(row_id)
            elif (
                _sha256(row["prev_row_hash"] + row["payload_hash"] + row["timestamp"])
                != row["row_hash"]
            ):
                broken.append(row_id)

            # Follow the stored hash, not the recomputed one: a single altered
            # row should be reported once, not cascade into every row after it.
            expected_prev = row["row_hash"]

        return (not broken, broken)

    def get_legacy_decisions(self, report_id: str) -> list[dict]:
        """Read the pre-Step-3 `decisions` table, if this database has one.

        That table is unchained and no longer written to. It is kept only so
        that audit history recorded before the hash chain existed remains
        readable; deleting audit evidence to tidy a schema is not something this
        module does. Returns [] if the table was never created.
        """
        conn = self._connect()
        try:
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
            ).fetchone()
            if not exists:
                return []
            rows = conn.execute(
                "SELECT * FROM decisions WHERE report_id = ? ORDER BY rowid ASC",
                (report_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    @staticmethod
    def _require_context(context: dict) -> None:
        missing = [k for k in REQUIRED_CONTEXT_KEYS if not context.get(k)]
        if missing:
            raise AuditContextError(
                f"log_event requires non-empty context keys {list(REQUIRED_CONTEXT_KEYS)}; "
                f"missing or empty: {missing}. For events logged before the workbook is "
                f"parsed, pass workbook_hash={NOT_YET_PARSED!r} explicitly rather than "
                f"omitting it."
            )

    @staticmethod
    def _last_row_hash(conn: sqlite3.Connection) -> str:
        """The most recent row_hash across ALL reports.

        The chain is global, not per-report, so removing or reordering one
        report's rows breaks the chain for every row after them.
        """
        row = conn.execute("SELECT row_hash FROM log_rows ORDER BY row_id DESC LIMIT 1").fetchone()
        return row["row_hash"] if row else GENESIS_HASH
