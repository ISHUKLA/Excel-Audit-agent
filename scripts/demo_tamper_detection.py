#!/usr/bin/env python3
"""Demonstrate tamper-evidence detection in the audit log.

Shows how the hash chain detects tampering and how the pipeline refuses recovery.
The audit log is tamper-evident (detectable), not tamper-proof (unmodifiable).

Usage:
    python scripts/demo_tamper_detection.py

Expected flow:
    [1/6] Setup: create audit.db.tamper_test from audit.db
    [2/6] Baseline: verify chain is intact
    [3/6] Tamper: modify one event row
    [4/6] Verify: detect hash mismatch
    [5/6] Pipeline: show all downstream nodes locked
    [6/6] Cleanup: remove test copy
"""

import os
import shutil
import sqlite3
import sys
from pathlib import Path

# Add repo root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.audit_log import AuditLog
from core.flight_recorder_queries import get_report_events, verify_audit_chain
from core.flight_recorder_state import build_pipeline_state


def print_step(step_num: int, title: str) -> None:
    """Print a colored step header."""
    print(f"\n{'='*70}")
    print(f"[{step_num}/6] {title}")
    print(f"{'='*70}")


def print_success(msg: str) -> None:
    """Print a success message."""
    print(f"✓ {msg}")


def print_warning(msg: str) -> None:
    """Print a warning message."""
    print(f"⚠️  {msg}")


def print_error(msg: str) -> None:
    """Print an error message."""
    print(f"✗ {msg}")


def get_first_report_id() -> str:
    """Get the first report_id from audit.db, if it exists."""
    if not os.path.exists("audit.db"):
        return None
    conn = sqlite3.connect("audit.db")
    try:
        row = conn.execute(
            "SELECT DISTINCT report_id FROM log_rows LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def step_1_setup() -> tuple[str, str]:
    """[1/6] Create a test copy of audit.db."""
    print_step(1, "Setup: create test database")

    if not os.path.exists("audit.db"):
        print_error("audit.db not found. Run the app first to create it.")
        return None, None

    test_db = "audit.db.tamper_test"
    if os.path.exists(test_db):
        os.remove(test_db)
        print(f"  Removed existing {test_db}")

    shutil.copy("audit.db", test_db)
    print_success(f"Created {test_db}")

    report_id = get_first_report_id()
    if not report_id:
        print_error("No reports found in audit.db")
        return None, None

    print(f"  Using report_id: {report_id}")
    return test_db, report_id


def step_2_baseline(test_db: str, report_id: str) -> tuple[bool, int]:
    """[2/6] Verify the baseline chain is intact."""
    print_step(2, "Baseline: verify chain integrity")

    audit_log = AuditLog(test_db)
    total_rows = audit_log.count_rows()
    is_valid, broken_rows = audit_log.verify_chain()

    if is_valid:
        print_success(f"Original chain is intact ({total_rows} total events)")
        return True, total_rows
    else:
        print_error(f"Chain already broken at rows: {broken_rows}")
        return False, total_rows


def step_3_tamper(test_db: str, report_id: str) -> int:
    """[3/6] Tamper with one event."""
    print_step(3, "Tamper: modify an event payload")

    conn = sqlite3.connect(test_db)
    try:
        # Someone with file access could drop the append-only triggers.
        # This is what we simulate here — it's not something this module would do
        # in normal operation, but it's what the hash chain is designed to detect.
        print("  Removing append-only triggers (simulating file-access bypass)...")
        conn.execute("DROP TRIGGER IF EXISTS log_rows_no_update")
        conn.execute("DROP TRIGGER IF EXISTS log_rows_no_delete")
        conn.commit()

        # Find a gate_3 event (a good candidate for tampering)
        row = conn.execute(
            "SELECT row_id, event_type, payload_json FROM log_rows "
            "WHERE report_id = ? AND event_type IN ('gate_decision', 'audit_event') "
            "ORDER BY row_id DESC LIMIT 1",
            (report_id,),
        ).fetchone()

        if not row:
            # Fall back to any row for this report
            row = conn.execute(
                "SELECT row_id, event_type, payload_json FROM log_rows "
                "WHERE report_id = ? "
                "ORDER BY row_id DESC LIMIT 1",
                (report_id,),
            ).fetchone()

        if not row:
            print_error(f"No events found for report {report_id}")
            return None

        tampered_row_id = row[0]
        event_type = row[1]

        # Directly modify the payload to something nonsensical
        # This bypasses the hash-chain recalculation, so the hash becomes invalid
        tampering_payload = '{"tamper":"DETECTED_BY_HASH_CHAIN"}'

        # Now modify via raw SQL. The hash chain will detect this.
        conn.execute(
            "UPDATE log_rows SET payload_json = ? WHERE row_id = ?",
            (tampering_payload, tampered_row_id),
        )
        conn.commit()

        print_warning(f"Tampered with event row_id={tampered_row_id} ({event_type})")
        print(f"  Replaced payload with: {tampering_payload}")

        return tampered_row_id

    finally:
        conn.close()


def step_4_verify(test_db: str) -> list[str]:
    """[4/6] Verify chain again and detect tampering."""
    print_step(4, "Verify: detect hash mismatch")

    audit_log = AuditLog(test_db)
    is_valid, broken_rows = audit_log.verify_chain()

    if is_valid:
        print_error("Chain still intact (tampering not detected — unexpected)")
        return []
    else:
        print_error(f"Chain broken — hash mismatch detected at: {broken_rows}")

        conn = sqlite3.connect(test_db)
        try:
            for broken_id in broken_rows:
                row = conn.execute(
                    "SELECT row_id, event_type, payload_hash, prev_row_hash "
                    "FROM log_rows WHERE row_id = ?",
                    (int(broken_id),),
                ).fetchone()
                if row:
                    print(f"    Row {broken_id}: event_type={row[1]}")
                    print(f"      payload_hash={row[2][:16]}...")
                    print(f"      prev_row_hash={row[3][:16]}...")
        finally:
            conn.close()

        return broken_rows


def step_5_pipeline(test_db: str, report_id: str) -> None:
    """[5/6] Load pipeline state and show cascading locks."""
    print_step(5, "Pipeline: show cascading effect")

    try:
        # Load audit log and events from the tampered database
        audit_log = AuditLog(test_db)
        events = get_report_events(audit_log, report_id)

        # Verify the chain (this will fail)
        chain_valid, broken = verify_audit_chain(audit_log, report_id)

        if not chain_valid:
            print_error(f"Chain integrity check failed at rows: {broken}")

        # Build pipeline state with chain_valid=False to reflect the tamper
        pipeline = build_pipeline_state(events, chain_valid=chain_valid)

        print(f"  Pipeline chain_valid: {pipeline.chain_valid}")
        print(f"  Total events recorded: {pipeline.total_events}")

        if pipeline.cascading_lock.get("locked_nodes"):
            print_warning(
                f"Cascading lock active: {len(pipeline.cascading_lock['locked_nodes'])} nodes locked"
            )
            for node in pipeline.cascading_lock["locked_nodes"]:
                print(f"    - {node}")

        # Show node statuses
        print("\n  Node statuses:")
        for node_name in [
            "gate_1", "anomaly_detector", "gate_2",
            "internal_reconciliation", "external_reconciliation",
            "gate_3", "optional_ai_doc", "gate_4", "pdf_export"
        ]:
            node = pipeline.nodes.get(node_name, {})
            status = node.get("status", "unknown")
            is_locked = node.get("is_locked", False)
            lock_indicator = " 🔒" if is_locked else ""
            print(f"    {node_name}: {status}{lock_indicator}")

    except Exception as e:
        print_error(f"Could not load pipeline state: {e}")


def step_6_cleanup(test_db: str) -> None:
    """[6/6] Remove the test database."""
    print_step(6, "Cleanup")

    if os.path.exists(test_db):
        os.remove(test_db)
        print_success(f"Removed {test_db}")
    else:
        print(f"(test database already removed)")


def main() -> None:
    """Run the tamper detection demo."""
    print("\n" + "="*70)
    print("AUDIT LOG TAMPER-EVIDENCE DEMONSTRATION")
    print("="*70)
    print("\nThis script shows how the hash chain detects tampering.")
    print("The audit log is tamper-EVIDENT (detectable after the fact),")
    print("not tamper-PROOF (unmodifiable by someone with file access).")

    # Step 1: Setup
    test_db, report_id = step_1_setup()
    if not test_db:
        print("\nAborting: could not set up test database.")
        return

    # Step 2: Baseline
    chain_intact, total_rows = step_2_baseline(test_db, report_id)
    if not chain_intact:
        print("\nAborting: baseline chain is already broken.")
        os.remove(test_db)
        return

    # Step 3: Tamper
    tampered_row_id = step_3_tamper(test_db, report_id)
    if tampered_row_id is None:
        print("\nAborting: could not find events to tamper with.")
        os.remove(test_db)
        return

    # Step 4: Verify
    broken_rows = step_4_verify(test_db)
    if not broken_rows:
        print("\nWarning: tampering was not detected (unexpected).")

    # Step 5: Pipeline
    step_5_pipeline(test_db, report_id)

    # Step 6: Cleanup
    step_6_cleanup(test_db)

    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print("""
The hash chain detected tampering at the payload level. Because each row
commits to the previous row's hash, modifying any payload invalidates every
row after it in the chain.

When the chain is broken, the pipeline marks all downstream nodes as
"locked" or "invalid" — you cannot approve or export a report whose audit
trail is compromised.

This is the intended behavior. The audit log is not unmodifiable; someone
with file access can edit audit.db. But they cannot hide their changes —
the hash chain makes them detectable, and the pipeline refuses to proceed
on a broken chain.

""")


if __name__ == "__main__":
    main()
