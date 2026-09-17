#!/usr/bin/env python3
"""Pipeline-level checks for scripts/test_flight_recorder_e2e.sh.

This repo has no Selenium/Playwright (see scripts/audit_trail_multiuser.sh,
which documents the same gap), so these checks drive agents.orchestrator.Orchestrator
directly against the real demo case workbooks instead of the Streamlit UI. Per
CLAUDE.md Rule 2, app.py contains no business logic — it only calls this same
orchestrator and renders the result — so exercising the orchestrator exercises
exactly what the UI exercises. Each check builds the flight recorder's own
PipelineState from the resulting audit.db, the same way ui/flight_recorder.py
does, and asserts on that.

Each function prints PASS/FAIL lines and returns an exit code; main() dispatches
by case name so the bash script can run them as separate subprocesses (an
isolated audit.db per case, no shared Streamlit session state).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Union, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import demo_cases
from agents.orchestrator import Orchestrator
from core.audit_log import AuditLog
from core.gates import GateBlockedError
from core.flight_recorder_queries import get_report_events, verify_audit_chain
from core.flight_recorder_state import build_pipeline_state, NODE_ORDER
from core.models import FileContext, MappingReviewDecision, ReferenceFigures
from core.state_store import StateStore
from core.ui_inputs import build_reference_figures
from core.workbook_identity import sha256_bytes

ACTOR = "Isaac Shukla"  # must match config/authorized_reviewers.json (Gate 1) and be a "cro" entry in config/authorized_approvers.json (Gate 4)
DEFAULT_PCT = 0.01
DEFAULT_ABSOLUTE = 100.0


def _make_orchestrator(db_path: str) -> Orchestrator:
    audit_log = AuditLog(db_path)
    state_store = StateStore(db_path, audit_log=audit_log)
    return Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        documentation_client=None,
        code_version="flight-recorder-e2e-check",
    )


def _file_context(case: dict, workbook_hash: str) -> FileContext:
    return FileContext(
        filename="case.xlsx",
        description=case["description"],
        user_role="actuary",
        entity=case["entity"],
        period=case["period"],
        currency=case["currency"],
        basis=case["basis"],
        confirmed_workbook_hash=workbook_hash,
        uploaded_at=datetime.now(timezone.utc),
    )


def _reference_figures(case: dict, *, currency_override: Optional[str] = None) -> Optional[ReferenceFigures]:
    if not case["reference_csv_path"]:
        return None
    import pandas as pd

    rows = pd.read_csv(case["reference_csv_path"]).to_dict(orient="records")
    return build_reference_figures(
        source_label=case["description"],
        entity=case["reference_entity"],
        period=case["reference_period"],
        currency=currency_override or case["reference_currency"],
        basis=case["reference_basis"],
        control_total=None,
        control_total_confirmed_by_human=False,
        rows=rows,
        require_account_number=True,
    )


def _pick_authoritative_outputs(parsed_file) -> list[str]:
    """Pick a couple of numeric, formula-derived cells as designated outputs.

    Gate 2 requires a human to name real output cells (core/gates.py
    findings_review_gate); an arbitrary text cell breaks reconciliation
    downstream. This mirrors what a reviewer would pick — calculated,
    numeric results — without hardcoding tab/cell names per demo case.
    """
    candidates = [
        ref
        for ref, cell in parsed_file.cells.items()
        if cell.formula is not None and cell.data_type == "number"
    ]
    if not candidates:
        raise AssertionError("no numeric formula cells found to designate as authoritative outputs")
    return candidates[:2]


def _confirm_all_findings(findings):
    return [
        f.model_copy(
            update={
                "human_decision": "confirmed",
                "human_reason": "Reviewed by scripts/test_flight_recorder_e2e.sh.",
                "decided_by": ACTOR,
                "decided_at": None,
            }
        )
        for f in findings
    ]


def _node_statuses(audit_log: AuditLog, report_id: str) -> dict:
    events = get_report_events(audit_log, report_id)
    chain_valid, _ = verify_audit_chain(audit_log, report_id)
    state = build_pipeline_state(events, chain_valid=chain_valid)
    return {name: state.nodes[name]["status"] for name in NODE_ORDER}, state


def check_case_1(db_path: str) -> bool:
    """Case 1 (clean run): drive all four gates, expect every node complete."""
    print("\n--- Test 1: Case 1 (clean run) — all nodes should reach 'complete' ---")
    case = demo_cases.load_case(1)
    orchestrator = _make_orchestrator(db_path)
    workbook_hash = sha256_bytes(case["workbook_bytes"])
    file_context = _file_context(case, workbook_hash)
    reference_figures = _reference_figures(case)

    report_id, parsed_file, findings = orchestrator.run(
        case["workbook_bytes"],
        file_context,
        reference_figures,
        expected_workbook_hash=workbook_hash,
        context_confirmed=True,
        actor=ACTOR,
    )
    outputs = _pick_authoritative_outputs(parsed_file)
    _, preview = orchestrator.submit_gate2_decisions(
        report_id, _confirm_all_findings(findings), outputs, actor=ACTOR
    )
    mapping_decisions = [
        MappingReviewDecision(mapping_id=m.mapping_id, action="approve")
        for m in preview.mappings
    ]
    orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=mapping_decisions,
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABSOLUTE,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABSOLUTE,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=False,
        acknowledge_incomplete=True,
    )
    orchestrator.submit_approval_record(report_id, "Isaac Shukla")

    statuses, state = _node_statuses(orchestrator._audit_log, report_id)
    for node, status in statuses.items():
        print(f"  {node}: {status}")

    incomplete = [n for n, s in statuses.items() if s != "complete"]
    if incomplete:
        print(f"FAIL: nodes not complete: {incomplete}")
        return False
    if not state.chain_valid:
        print("FAIL: chain_valid is False on a clean run")
        return False
    print(f"PASS: all {len(NODE_ORDER)} nodes complete, chain valid, report_id={report_id}")
    return True


def check_case_2(db_path: str) -> bool:
    """Case 2 (control failures): expect anomaly findings to surface at Gate 2."""
    print("\n--- Test 2: Case 2 (control failures) — anomaly findings should be shown ---")
    case = demo_cases.load_case(2)
    orchestrator = _make_orchestrator(db_path)
    workbook_hash = sha256_bytes(case["workbook_bytes"])
    file_context = _file_context(case, workbook_hash)

    report_id, parsed_file, findings = orchestrator.run(
        case["workbook_bytes"],
        file_context,
        None,
        expected_workbook_hash=workbook_hash,
        context_confirmed=True,
        actor=ACTOR,
    )
    print(f"  Anomaly detector surfaced {len(findings)} finding(s):")
    for f in findings:
        print(f"    - [{f.severity}] {f.finding_id}: {f.description[:80]}")

    if not findings:
        print("FAIL: Case 2 is expected to contain control-failure findings, but none were found")
        return False

    outputs = _pick_authoritative_outputs(parsed_file)
    orchestrator.submit_gate2_decisions(
        report_id, _confirm_all_findings(findings), outputs, actor=ACTOR
    )

    statuses, _ = _node_statuses(orchestrator._audit_log, report_id)
    print(f"  anomaly_detector: {statuses['anomaly_detector']}")
    print(f"  gate_2: {statuses['gate_2']}")

    if statuses["anomaly_detector"] != "complete" or statuses["gate_2"] != "complete":
        print("FAIL: anomaly_detector/gate_2 did not reach 'complete' after dispositioning findings")
        return False
    print(f"PASS: {len(findings)} finding(s) shown and dispositioned, report_id={report_id}")
    return True


def check_case_3(db_path: str) -> bool:
    """Case 3 (reconciliation failure): expect Gate 3 to block and lock downstream."""
    print("\n--- Test 3: Case 3 (reconciliation block) — Gate 3 should block and lock downstream ---")
    case = demo_cases.load_case(3)
    orchestrator = _make_orchestrator(db_path)
    workbook_hash = sha256_bytes(case["workbook_bytes"])
    file_context = _file_context(case, workbook_hash)
    # demo/expected_results/case_3_expected.json documents that this case's
    # reference figures are intentionally in GBP against a EUR workbook — a
    # deliberate currency-mismatch demonstration. demo_cases.py's case 3 entry
    # defaults reference_currency to match the workbook (EUR) rather than
    # encoding that mismatch, so it is overridden here to reproduce the
    # documented "block" scenario.
    reference_figures = _reference_figures(case, currency_override="GBP")

    report_id, parsed_file, findings = orchestrator.run(
        case["workbook_bytes"],
        file_context,
        reference_figures,
        expected_workbook_hash=workbook_hash,
        context_confirmed=True,
        actor=ACTOR,
    )
    outputs = _pick_authoritative_outputs(parsed_file)
    _, preview = orchestrator.submit_gate2_decisions(
        report_id, _confirm_all_findings(findings), outputs, actor=ACTOR
    )
    mapping_decisions = [
        MappingReviewDecision(mapping_id=m.mapping_id, action="approve")
        for m in preview.mappings
    ]

    blocked = False
    try:
        orchestrator.submit_gate3_decisions(
            report_id,
            preview,
            mapping_decisions=mapping_decisions,
            internal_pct_threshold=DEFAULT_PCT,
            internal_absolute_threshold=DEFAULT_ABSOLUTE,
            external_pct_threshold=DEFAULT_PCT,
            external_absolute_threshold=DEFAULT_ABSOLUTE,
            internal_threshold_deviation_reason=None,
            external_threshold_deviation_reason=None,
            actor=ACTOR,
            use_ai_documentation=False,
            acknowledge_incomplete=False,
        )
    except GateBlockedError as exc:
        blocked = True
        print(f"  Gate 3 raised GateBlockedError as expected: {exc}")

    if not blocked:
        print("FAIL: Case 3 is expected to block at Gate 3, but submit_gate3_decisions succeeded")
        return False

    statuses, state = _node_statuses(orchestrator._audit_log, report_id)
    for node, status in statuses.items():
        locked = state.nodes[node]["is_locked"]
        print(f"  {node}: {status}{' (locked)' if locked else ''}")

    locked_nodes = state.cascading_lock.get("locked_nodes", [])
    expected_locked = {"optional_ai_doc", "gate_4", "pdf_export"}
    if statuses["gate_3"] not in ("blocked",):
        print(f"FAIL: gate_3 status is {statuses['gate_3']!r}, expected 'blocked'")
        return False
    if not expected_locked.issubset(set(locked_nodes)):
        print(f"FAIL: expected {expected_locked} locked, got {locked_nodes}")
        return False
    print(f"PASS: gate_3 blocked, downstream nodes locked ({locked_nodes}), report_id={report_id}")
    return True


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: flight_recorder_e2e_checks.py <case_1|case_2|case_3> <db_path>", file=sys.stderr)
        return 2

    case_name, db_path = sys.argv[1], sys.argv[2]
    checks = {"case_1": check_case_1, "case_2": check_case_2, "case_3": check_case_3}
    if case_name not in checks:
        print(f"Unknown case: {case_name}", file=sys.stderr)
        return 2

    try:
        ok = checks[case_name](db_path)
    except Exception as exc:  # surfaced to the bash script as a FAIL, not a crash
        print(f"FAIL: unhandled exception: {exc!r}")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
