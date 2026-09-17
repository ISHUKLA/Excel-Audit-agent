"""Read-only queries over `audit.db` for the governance flight recorder.

This module has no write path and does not participate in the hash chain — it
only reads what `core/audit_log.py` already wrote. That module's real schema
and API differ from a generic "audit_log table" assumption in a few load-bearing
ways this module has to respect rather than paper over:

  - The table is `log_rows`, and the hash columns are `row_hash`/`prev_row_hash`
    (this module's public dicts rename them to `event_hash`/`previous_hash` for
    a display-friendly shape, but the underlying column names are unchanged).
  - `AuditLog.verify_chain()` takes no `report_id` — the chain is global by
    design (see that module's docstring), so a tampered row anywhere before a
    report's rows undermines confidence in everything chained after it. This
    module's `verify_audit_chain()` accepts a `report_id` to match the requested
    call shape, but it explains a global break in those terms rather than
    pretending each report has its own independent chain.
  - Gates 1, 2, and 4 check-then-raise `GateBlockedError` *before* calling
    `log_event` (see `core/gates.py`), so a blocked attempt at one of those
    gates leaves no row in the log at all. Gate 3 is the one exception: it logs
    the computed verdicts and only raises afterward, which is why "gate_3
    blocked" is the only gate-level block this module can see directly in the
    evidence. This asymmetry is real, not a bug in this module, and the
    functions below say so rather than inventing a blocked-event row that
    was never written.
  - Agents (the anomaly detector, the two reconciliation passes) never log
    their own audit events — their output is folded into the next gate's
    `gate_decision` payload as a human reviews it. So "node status" for those
    agent-named nodes is read out of the enclosing gate's payload, not from an
    independent event. Until that gate logs, the audit log genuinely has no
    visibility into whether the agent ran; this module reports that as
    "incomplete" rather than guessing.
"""

import sqlite3
from typing import Optional

from core.audit_log import AuditLog

# Pipeline order used for "is my predecessor done yet" reasoning. Agent-named
# nodes (anomaly_detector, internal_reconciliation, external_reconciliation)
# have no event of their own; their evidence rides inside the next gate node.
NODE_ORDER = [
    "gate_1",
    "anomaly_detector",
    "gate_2",
    "internal_reconciliation",
    "external_reconciliation",
    "gate_3",
    "optional_ai_doc",
    "gate_4",
    "pdf_export",
]

# Which gate number's gate_decision payload carries each node's evidence.
_NODE_TO_GATE = {
    "gate_1": 1,
    "anomaly_detector": 2,
    "gate_2": 2,
    "internal_reconciliation": 3,
    "external_reconciliation": 3,
    "gate_3": 3,
}


def get_report_events(audit_log: AuditLog, report_id: str) -> list[dict]:
    """All log rows for one report, oldest first, in a display-friendly shape.

    Delegates the actual query to `AuditLog.get_rows`, which already runs
    `SELECT * FROM log_rows WHERE report_id = ? ORDER BY row_id ASC` — this
    function does not open a second connection to `audit.db`. It renames the
    hash columns to `event_hash`/`previous_hash` and adds a parsed `evidence`
    dict (gate number, verdicts, finding counts) so callers don't each have to
    re-parse `payload_json` themselves.
    """
    rows = audit_log.get_rows(report_id)
    events = []
    for row in rows:
        try:
            payload = _json_loads(row["payload_json"])
        except ValueError:
            payload = {}
        events.append(
            {
                "row_id": row["row_id"],
                "event_type": row["event_type"],
                "actor": row["actor"],
                "timestamp": row["timestamp"],
                "payload_json": row["payload_json"],
                "event_hash": row["row_hash"],
                "previous_hash": row["prev_row_hash"],
                "evidence": _summarize_evidence(row["event_type"], payload),
            }
        )
    return events


def _json_loads(text: str) -> dict:
    import json

    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"payload_json is not valid JSON: {exc}") from exc


def _summarize_evidence(event_type: str, payload: dict) -> dict:
    """Pull the fields callers actually want out of a raw gate payload.

    Only extracts what is genuinely present — never fills in a verdict or
    count that the event didn't record.
    """
    summary: dict = {"action": payload.get("action")}

    if event_type == "gate_decision":
        summary["gate"] = payload.get("gate")
        for key in (
            "context_match_verdict",
            "internal_verdict",
            "external_verdict",
            "control_total_verdict",
        ):
            if key in payload:
                summary[key] = payload[key]
        if "dispositions" in payload:
            summary["findings_count"] = len(payload["dispositions"])
        if "threshold_deviation" in payload:
            summary["threshold_deviation"] = payload["threshold_deviation"]

    elif event_type == "report_approved":
        summary["gate"] = payload.get("gate")
        summary["approval_name"] = payload.get("approval_name")
        summary["role"] = payload.get("role")
        summary["name_in_registry"] = payload.get("name_in_registry")

    elif event_type == "llm_use_decision":
        summary["decision"] = payload.get("decision")
        summary["disclosure_version"] = payload.get("disclosure_version")

    elif event_type == "workbook_identity_mismatch":
        summary.update({k: v for k, v in payload.items() if k != "context"})

    return summary


def _gate_events(events: list[dict], gate_number: int) -> list[dict]:
    return [
        e
        for e in events
        if e["event_type"] == "gate_decision" and e["evidence"].get("gate") == gate_number
    ]


def get_node_status(events: list[dict], node_name: str) -> dict:
    """Determine one pipeline node's status from the events already fetched.

    Returns {status, last_event, evidence_summary}. `status` is one of:
      - "complete": the node's evidence is present and not blocked
      - "blocked": the node's evidence shows a block verdict (gate_3 only —
        see module docstring for why gates 1/2/4 can't be detected this way)
      - "incomplete": partial evidence exists (an agent ran under a gate that
        hasn't logged yet) but there is nothing final to point to
      - "waiting": no evidence yet, and nothing upstream is blocked
    """
    if node_name not in NODE_ORDER:
        raise ValueError(f"unknown node: {node_name!r}")

    if node_name == "gate_4":
        matches = [e for e in events if e["event_type"] == "report_approved"]
        if matches:
            last = matches[-1]
            return {
                "status": "complete",
                "last_event": last,
                "evidence_summary": last["evidence"],
            }
        return _waiting_or_blocked(events, node_name, "gate_3")

    if node_name == "pdf_export":
        matches = [e for e in events if e["event_type"] == "report_approved"]
        if matches:
            return {
                "status": "complete",
                "last_event": matches[-1],
                "evidence_summary": {"note": "PDF export follows gate_4 directly; no separate event is logged for it"},
            }
        return _waiting_or_blocked(events, node_name, "gate_3")

    if node_name == "optional_ai_doc":
        matches = [e for e in events if e["event_type"] == "llm_use_decision"]
        if matches:
            last = matches[-1]
            return {
                "status": "complete",
                "last_event": last,
                "evidence_summary": last["evidence"],
            }
        return _waiting_or_blocked(events, node_name, "gate_3")

    gate_number = _NODE_TO_GATE[node_name]
    matches = _gate_events(events, gate_number)

    if not matches:
        predecessor = NODE_ORDER[NODE_ORDER.index(node_name) - 1] if NODE_ORDER.index(node_name) > 0 else None
        if node_name in ("anomaly_detector", "internal_reconciliation", "external_reconciliation"):
            # The agent may already have run; the log simply can't say, because
            # agents don't log their own events. Report that honestly.
            return {
                "status": "incomplete",
                "last_event": None,
                "evidence_summary": {
                    "note": f"no gate_{gate_number} event yet — this node's evidence "
                    f"is only recorded once gate_{gate_number} logs, so its own "
                    "execution isn't independently visible in the audit log"
                },
            }
        return _waiting_or_blocked(events, node_name, predecessor)

    last = matches[-1]
    if gate_number == 3:
        if last["evidence"].get("internal_verdict") == "block" or last["evidence"].get("external_verdict") == "block":
            return {"status": "blocked", "last_event": last, "evidence_summary": last["evidence"]}

    return {"status": "complete", "last_event": last, "evidence_summary": last["evidence"]}


def _waiting_or_blocked(events: list[dict], node_name: str, predecessor: Optional[str]) -> dict:
    if predecessor is None:
        return {"status": "waiting", "last_event": None, "evidence_summary": {}}
    pred_status = get_node_status(events, predecessor)["status"]
    status = "blocked" if pred_status == "blocked" else "waiting"
    return {
        "status": status,
        "last_event": None,
        "evidence_summary": {"waiting_on": predecessor} if status == "waiting" else {"blocked_by": predecessor},
    }


def get_cascading_locks(events: list[dict]) -> dict:
    """Which downstream nodes a gate_3 block locks out.

    Gate 3 is the only gate whose block is directly visible in the audit log
    (see module docstring) — reconciliation_gate logs the computed verdicts
    before raising GateBlockedError, so a "block" verdict survives even though
    the gate then blocks. Per Rule 23 in this repo, declining the optional AI
    documentation step does not itself block gate_4; a locked gate_4 here is
    always attributed to the reconciliation block, not to the AI-doc decision.

    Returns {locked_nodes, blocking_node, blocking_reason}. If no gate_3 block
    is present, returns {locked_nodes: [], blocking_node: None, blocking_reason: None}.
    """
    gate3_events = _gate_events(events, 3)
    if not gate3_events:
        return {"locked_nodes": [], "blocking_node": None, "blocking_reason": None}

    last = gate3_events[-1]
    internal_verdict = last["evidence"].get("internal_verdict")
    external_verdict = last["evidence"].get("external_verdict")

    if internal_verdict != "block" and external_verdict != "block":
        return {"locked_nodes": [], "blocking_node": None, "blocking_reason": None}

    return {
        "locked_nodes": ["optional_ai_doc", "gate_4", "pdf_export"],
        "blocking_node": "gate_3",
        "blocking_reason": (
            f"gate_3 recorded internal_verdict={internal_verdict!r}, "
            f"external_verdict={external_verdict!r} — a block on either verdict "
            "stops the pipeline before gate_4, so the optional AI documentation "
            "step, the named approval record, and PDF export are all locked"
        ),
    }


def extract_evidence_hash(event: dict, short: bool = True) -> str:
    """The hash commitment for one event, from the dict `get_report_events` returns.

    `short=True` (default) returns the first 8 characters, for display in a
    UI; `short=False` returns the full sha256 hex digest for anything that
    needs to compare or re-verify it.
    """
    full_hash = event.get("event_hash", "")
    return full_hash[:8] if short else full_hash


# Static governance rules per node. These describe what CLAUDE.md and
# core/gates.py already commit each node to — they are not derived from the
# database, and they don't change per report.
_NODE_RULES = {
    "gate_1": {
        "allowed": ["confirm file description and reference figures are accurate", "record entity/period/currency/basis comparison"],
        "prohibited": ["parse the workbook before context is confirmed", "infer context from the file instead of asking a human"],
        "actor_type": "human",
    },
    "anomaly_detector": {
        "allowed": ["flag rule-based anomalies", "compute independently"],
        "prohibited": ["call an LLM", "resolve or dismiss its own findings"],
        "actor_type": "agent",
    },
    "gate_2": {
        "allowed": ["confirm, override, or dismiss each finding with a reason", "designate authoritative outputs"],
        "prohibited": ["proceed with any finding lacking a human disposition", "infer authoritative outputs by keyword match"],
        "actor_type": "human",
    },
    "internal_reconciliation": {
        "allowed": ["independently reconstruct formulas", "compare reconstruction to the workbook's own cached values"],
        "prohibited": ["merge with the external reconciliation verdict", "call an LLM"],
        "actor_type": "agent",
    },
    "external_reconciliation": {
        "allowed": ["compare reconstructed outputs to supplied reference figures", "propose account mappings"],
        "prohibited": ["auto-approve a fuzzy-matched mapping", "call an LLM"],
        "actor_type": "agent",
    },
    "gate_3": {
        "allowed": ["set materiality thresholds", "acknowledge an incomplete reconstruction with a reason"],
        "prohibited": ["bypass a block verdict", "collapse internal_verdict and external_verdict into one verdict"],
        "actor_type": "human",
    },
    "optional_ai_doc": {
        "allowed": ["decide, per report, whether to use AI-generated documentation", "decline without blocking the rest of the pipeline"],
        "prohibited": ["call the Anthropic API without a logged per-report decision", "send unminimized data (see core/llm_data_policy.py)"],
        "actor_type": "ai",
    },
    "gate_4": {
        "allowed": ["record a named approval from a registered CRO"],
        "prohibited": ["accept a name not in config/authorized_approvers.json with role='cro'", "call this a signature or an attestation"],
        "actor_type": "human",
    },
    "pdf_export": {
        "allowed": ["generate the report once gate_4 has completed"],
        "prohibited": ["enable the export button before gate_4 completes"],
        "actor_type": "agent",
    },
}


def get_node_rules(node_name: str) -> dict:
    """Governance rules for one pipeline node: allowed/prohibited actions and actor_type.

    These come from CLAUDE.md and the gate implementations, not from the
    database — they describe what a node is permitted to do, not what it did
    on any particular run.
    """
    if node_name not in _NODE_RULES:
        raise ValueError(f"unknown node: {node_name!r}")
    return dict(_NODE_RULES[node_name])


def verify_audit_chain(audit_log: AuditLog, report_id: str) -> tuple[bool, str]:
    """Explain chain integrity in terms of one report, honestly scoped to a global chain.

    `AuditLog.verify_chain()` has no `report_id` parameter — the chain links
    every report's rows together in insertion order, so a tampered row before
    this report's rows undermines confidence in this report's evidence too,
    even if none of this report's own rows were touched. This function calls
    the global verification once, then reports whether the break (if any) falls
    inside or outside this report's own rows, rather than pretending each
    report can be verified in isolation.
    """
    is_valid, broken_row_ids = audit_log.verify_chain()
    report_rows = audit_log.get_rows(report_id)
    report_row_ids = {str(row["row_id"]) for row in report_rows}

    if is_valid:
        return True, f"Chain valid: {len(report_rows)} events for report {report_id!r}, all hashes match"

    broken_in_report = [rid for rid in broken_row_ids if rid in report_row_ids]
    if broken_in_report:
        return (
            False,
            f"Chain broken at row_id={broken_in_report[0]} within report {report_id!r}'s own events: hash mismatch",
        )

    return (
        False,
        f"Chain broken at row_id={broken_row_ids[0]} elsewhere in the global log — "
        f"report {report_id!r}'s own rows are unaltered, but the global chain they "
        "are linked into is not intact, so it cannot vouch for anything logged "
        "after the break",
    )
