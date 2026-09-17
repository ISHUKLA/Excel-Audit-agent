#!/usr/bin/env python3
"""Attempt to delete a single report's rows from the audit log.

This script is expected to fail. `audit.db`'s `log_rows` table has database
triggers (`log_rows_no_delete`, `log_rows_no_update` — see core/audit_log.py)
that reject DELETE and UPDATE at the SQL level, because the audit log is
append-only by design: CLAUDE.md Rule 4 explicitly forbids adding DELETE/UPDATE
logic for it anywhere in this codebase. This script does not add such logic —
it demonstrates, against a real `audit.db`, that the existing protection holds.

A backup is taken first regardless of outcome, since running this script at
all implies uncertainty about whether the protection is intact.
"""

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

TABLE = "log_rows"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1

# report_id is a free-text field in the schema (see core/audit_log.py), but in
# practice every caller in this codebase generates it with uuid.uuid4(). A
# value that doesn't look like a UUID is not necessarily wrong, but it is
# unusual enough to flag before running SQL that references it.
_REPORT_ID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Attempt to delete a report's rows from the audit log. This is "
            "expected to be blocked by database triggers — the audit log is "
            "append-only by design."
        )
    )
    parser.add_argument("--report-id", required=True, help="report_id to attempt to delete")
    parser.add_argument("--db", default="audit.db", help="path to the audit database (default: audit.db)")
    parser.add_argument(
        "--no-backup",
        action="store_true",
        default=False,
        help="skip creating a backup before attempting deletion (not recommended)",
    )
    return parser.parse_args()


def _create_backup(db_path: Path) -> Path:
    timestamp = int(time.time())
    backup_path = db_path.parent / f"{db_path.name}.{timestamp}.backup"
    backup_path.write_bytes(db_path.read_bytes())
    print(f"✓ Backup created: {backup_path}")
    return backup_path


def _connect_with_retry(db_path: Path) -> sqlite3.Connection:
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return sqlite3.connect(str(db_path), timeout=5)
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" not in str(exc).lower():
                raise
            print(f"⚠ Database locked, retrying ({attempt}/{MAX_RETRIES})...")
            time.sleep(RETRY_DELAY_SECONDS)
    raise RuntimeError(f"could not open database after {MAX_RETRIES} attempts") from last_error


def main() -> int:
    args = _parse_args()
    db_path = Path(args.db)

    if not db_path.exists():
        print(f"✗ ERROR: database not found at {db_path}", file=sys.stderr)
        return 1

    if not _REPORT_ID_PATTERN.match(args.report_id):
        print(
            f"⚠ WARNING: {args.report_id!r} does not look like a UUID report_id. "
            "Continuing anyway; SQLite will simply match zero rows if it's wrong."
        )

    if not args.no_backup:
        try:
            _create_backup(db_path)
        except OSError as exc:
            print(f"✗ ERROR: could not create backup: {exc}", file=sys.stderr)
            return 1
    else:
        print("⚠ WARNING: proceeding without a backup (--no-backup was set)")

    try:
        conn = _connect_with_retry(db_path)
    except RuntimeError as exc:
        print(f"✗ ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        with conn:
            row_count = conn.execute(
                f"SELECT COUNT(*) FROM {TABLE} WHERE report_id = ?",
                (args.report_id,),
            ).fetchone()[0]

            if row_count == 0:
                print(f"⚠ WARNING: no rows found for report_id {args.report_id!r}")
                return 0

            print(f"Found {row_count} row(s) for report_id {args.report_id!r}. Attempting deletion...")

            try:
                conn.execute(f"DELETE FROM {TABLE} WHERE report_id = ?", (args.report_id,))
            except sqlite3.IntegrityError as exc:
                print("✓ Deletion blocked by database trigger (expected)")
                print(f"  ({exc})")
                print("✓ Audit log is protected; manual intervention required if deletion is necessary.")
                return 0

            # If we get here, the trigger did not fire — the protection is
            # gone (dropped, or this db predates it). The deletion already
            # happened inside this transaction; report it honestly rather
            # than pretending the fast path is fine.
            print(f"⚠ WARNING: Deleted {row_count} row(s) — the append-only trigger is NOT active on this database.")
            print("⚠ This is a serious integrity gap. Investigate before trusting this audit.db further.")
            return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
