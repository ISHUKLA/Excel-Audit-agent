"""Append-only SQLite log of every gate decision, override, and sign-off."""

import sqlite3


class AuditLog:
    def __init__(self, db_path: str = "audit.db"):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    report_id TEXT NOT NULL,
                    gate_number INTEGER NOT NULL,
                    finding_id TEXT,
                    action TEXT NOT NULL,
                    reason TEXT,
                    user_name TEXT NOT NULL,
                    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
                )
                """
            )
            # Enforced at the database level so append-only holds even for
            # writes that bypass this class entirely.
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS decisions_no_update
                BEFORE UPDATE ON decisions
                BEGIN
                    SELECT RAISE(ABORT, 'audit log is append-only: UPDATE is not permitted');
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS decisions_no_delete
                BEFORE DELETE ON decisions
                BEGIN
                    SELECT RAISE(ABORT, 'audit log is append-only: DELETE is not permitted');
                END
                """
            )

    def log_decision(
        self,
        report_id: str,
        gate: int,
        finding_id: str | None,
        action: str,
        reason: str | None,
        user_name: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO decisions (report_id, gate_number, finding_id, action, reason, user_name)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (report_id, gate, finding_id, action, reason, user_name),
            )

    def get_decisions(self, report_id: str) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT report_id, gate_number, finding_id, action, reason, user_name, timestamp
                FROM decisions
                WHERE report_id = ?
                ORDER BY rowid ASC
                """,
                (report_id,),
            ).fetchall()
            return [dict(row) for row in rows]
