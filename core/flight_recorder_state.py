"""State machine for the governance flight recorder pipeline.

Models the 9 sequential nodes through which every report passes:
  1. gate_1 (human confirmation of context)
  2. anomaly_detector (rule-based agent)
  3. gate_2 (human review of findings and designation of outputs)
  4. internal_reconciliation (agent: reconstruction vs. cached values)
  5. external_reconciliation (agent: reconstruction vs. reference figures)
  6. gate_3 (human review of reconciliation with materiality thresholds)
  7. optional_ai_doc (optional AI agent for documentation)
  8. gate_4 (human approval by a registered CRO)
  9. pdf_export (agent: generate report PDF)

A node can be in one of five states:
  - "complete": final event for this node exists and is not blocked
  - "waiting": predecessor is complete, but this node hasn't started
  - "blocked": a predecessor recorded a BLOCK verdict, locking this node
  - "in_progress": event exists but no final verdict yet (rare; for gates that don't block immediately)
  - "incomplete": some evidence exists but is abandoned (e.g., gate 3 logged but never reached gate 4)

A BLOCK verdict at gate_3 cascades downstream to lock gate_4, optional_ai_doc, and pdf_export.

This module exports:
  - NodeDefinition: metadata about one node (allowed/prohibited actions, predecessors, etc.)
  - PipelineState: complete snapshot of all nodes' statuses
  - compute_node_status(): determine one node's status from event list
  - compute_cascading_lock(): which downstream nodes are locked by a gate_3 block
  - build_pipeline_state(): construct a PipelineState from events
  - node_definitions(): static metadata for all 9 nodes
  - is_downstream_locked(): check if a node is affected by a cascading lock
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.flight_recorder_queries import (
    _gate_events,
    get_cascading_locks,
    get_node_status,
    get_node_rules,
)

# The order in which nodes appear in the pipeline.
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


@dataclass
class NodeDefinition:
    """Static metadata about one pipeline node."""

    name: str
    actor_type: str  # "human", "agent", "ai"
    actor_name: str
    allowed_actions: List[str] = field(default_factory=list)
    prohibited_actions: List[str] = field(default_factory=list)
    predecessor_nodes: List[str] = field(default_factory=list)
    can_be_locked: bool = False


@dataclass
class PipelineState:
    """Complete snapshot of the pipeline at one moment in time."""

    nodes: Dict[str, dict]
    """
    Dict[node_name, {
      'status': str,  # one of: complete, waiting, blocked, in_progress, incomplete
      'actor': Optional[str],  # the human or system that performed this node
      'event': Optional[dict],  # the last event for this node (if exists)
      'evidence_summary': dict,  # parsed evidence from that event (verdicts, findings count, etc.)
      'rules': NodeDefinition,  # allowed/prohibited actions, predecessors
      'is_locked': bool,  # True if cascading_lock affects this node
    }]
    """

    blocking_node: Optional[str] = None
    """Which node's BLOCK verdict initiated the cascade. None if no block."""

    blocking_reason: str = ""
    """Explanation of why this block occurred."""

    cascading_lock: Dict = field(default_factory=lambda: {"locked_nodes": [], "blocking_node": None})
    """Dict with keys locked_nodes, blocking_node, blocking_reason."""

    is_all_locked: bool = False
    """True if gate_3 recorded a BLOCK, locking everything downstream."""

    chain_valid: bool = True
    """True if the audit chain passed tamper verification."""

    total_events: int = 0
    """Count of events in the log for this report."""


def compute_node_status(events: List[dict], node_name: str) -> str:
    """Determine one node's status from the event list.

    Returns one of: "complete", "waiting", "blocked", "in_progress", "incomplete".
    Delegates to get_node_status from flight_recorder_queries and extracts the status string.
    """
    if node_name not in NODE_ORDER:
        raise ValueError(f"unknown node: {node_name!r}")

    try:
        status_dict = get_node_status(events, node_name)
        return status_dict["status"]
    except (KeyError, ValueError):
        return "incomplete"


def compute_cascading_lock(events: List[dict]) -> dict:
    """Determine which downstream nodes are locked by a gate_3 block.

    If gate_3 recorded internal_verdict="block" or external_verdict="block",
    all downstream nodes (optional_ai_doc, gate_4, pdf_export) are locked.

    Returns {locked_nodes, blocking_node, blocking_reason}.
    Delegates to get_cascading_locks from flight_recorder_queries.
    """
    return get_cascading_locks(events)


def is_downstream_locked(node_name: str, cascading_lock: dict) -> bool:
    """Check if a node is affected by a cascading lock.

    Args:
        node_name: name of the node to check
        cascading_lock: result from compute_cascading_lock()

    Returns True if node_name is in cascading_lock["locked_nodes"].
    """
    if not cascading_lock or not cascading_lock.get("locked_nodes"):
        return False
    return node_name in cascading_lock["locked_nodes"]


def build_pipeline_state(events: List[dict], chain_valid: bool = True) -> PipelineState:
    """Construct a complete PipelineState from an event list.

    Args:
        events: output from core.flight_recorder_queries.get_report_events()
        chain_valid: result from core.flight_recorder_queries.verify_audit_chain()

    Returns a PipelineState with all nodes' statuses, rules, locks, and evidence.
    """
    nodes_dict = {}
    cascading_lock = compute_cascading_lock(events)
    definitions = node_definitions()

    for node_name in NODE_ORDER:
        status = compute_node_status(events, node_name)
        status_dict = get_node_status(events, node_name) or {
            "last_event": None,
            "evidence_summary": {},
        }
        rules = definitions[node_name]
        is_locked = is_downstream_locked(node_name, cascading_lock)

        last_event = status_dict.get("last_event")
        nodes_dict[node_name] = {
            "status": status,
            "actor": last_event.get("actor") if last_event else None,
            "event": last_event,
            "evidence_summary": status_dict.get("evidence_summary", {}),
            "rules": rules,
            "is_locked": is_locked,
        }

    is_all_locked = bool(cascading_lock.get("locked_nodes"))
    blocking_node = cascading_lock.get("blocking_node")

    return PipelineState(
        nodes=nodes_dict,
        blocking_node=blocking_node,
        blocking_reason=cascading_lock.get("blocking_reason", ""),
        cascading_lock=cascading_lock,
        is_all_locked=is_all_locked,
        chain_valid=chain_valid,
        total_events=len(events),
    )


def node_definitions() -> Dict[str, NodeDefinition]:
    """Return static governance definitions for all 9 pipeline nodes.

    These definitions describe what each node is permitted to do, independent
    of what it actually did on any particular run. They come from CLAUDE.md,
    core/gates.py, and the agent implementations.
    """
    return {
        "gate_1": NodeDefinition(
            name="gate_1",
            actor_type="human",
            actor_name="Qualified Actuary (Preparer)",
            allowed_actions=[
                "read and confirm file description",
                "review supplied reference figures for accuracy",
                "log context agreement (entity, period, currency, basis)",
            ],
            prohibited_actions=[
                "parse the workbook before context is confirmed",
                "infer context from the file instead of asking a human",
                "proceed with an unconfirmed context",
            ],
            predecessor_nodes=[],
            can_be_locked=False,
        ),
        "anomaly_detector": NodeDefinition(
            name="anomaly_detector",
            actor_type="agent",
            actor_name="Deterministic Anomaly Detection Engine",
            allowed_actions=[
                "scan for hardcoded values (not formulas)",
                "detect circular references",
                "flag unsupported formula patterns",
                "report independence-of-findings anomalies",
            ],
            prohibited_actions=[
                "modify the workbook",
                "skip checks to hide issues",
                "resolve or dismiss its own findings",
                "call an LLM",
            ],
            predecessor_nodes=["gate_1"],
            can_be_locked=False,
        ),
        "gate_2": NodeDefinition(
            name="gate_2",
            actor_type="human",
            actor_name="Qualified Actuary (Reviewer)",
            allowed_actions=[
                "confirm, override, or dismiss each finding with a reason",
                "designate authoritative outputs (cells to reconstruct and reconcile)",
                "request additional analysis or investigation",
            ],
            prohibited_actions=[
                "proceed with any finding lacking a human disposition",
                "infer authoritative outputs by keyword match — they must be named",
                "approve without reviewing every finding",
            ],
            predecessor_nodes=["anomaly_detector"],
            can_be_locked=False,
        ),
        "internal_reconciliation": NodeDefinition(
            name="internal_reconciliation",
            actor_type="agent",
            actor_name="Formula Reconstruction Engine (Internal Consistency)",
            allowed_actions=[
                "independently reconstruct formulas from the workbook structure",
                "compare reconstruction against the workbook's cached values",
                "report completeness and discrepancies",
            ],
            prohibited_actions=[
                "merge with the external reconciliation verdict",
                "use reference figures in the comparison (that is external's job)",
                "suppress or override a completeness issue",
                "call an LLM",
            ],
            predecessor_nodes=["gate_2"],
            can_be_locked=False,
        ),
        "external_reconciliation": NodeDefinition(
            name="external_reconciliation",
            actor_type="agent",
            actor_name="Accounting Reconciliation Engine (Accounts Comparison)",
            allowed_actions=[
                "compare reconstructed outputs to supplied reference figures",
                "propose account mappings based on fuzzy string matching",
                "report unmatched reference items and unmapped outputs",
            ],
            prohibited_actions=[
                "auto-approve a fuzzy-matched mapping without human review",
                "merge with the internal reconciliation verdict",
                "proceed without complete reference figures",
                "call an LLM",
            ],
            predecessor_nodes=["gate_2"],
            can_be_locked=False,
        ),
        "gate_3": NodeDefinition(
            name="gate_3",
            actor_type="human",
            actor_name="Qualified Actuary (Chief Risk Officer or Delegated Reviewer)",
            allowed_actions=[
                "review both reconciliation verdicts side-by-side",
                "set materiality thresholds (percentage and absolute)",
                "acknowledge an incomplete reconstruction with a documented reason",
                "approve or reject the reconciliation and recommend next steps",
            ],
            prohibited_actions=[
                "bypass a BLOCK verdict — blockers cannot be overridden",
                "collapse internal_verdict and external_verdict into one verdict",
                "approve without addressing unmatched reference items",
                "set materiality thresholds to hidden defaults",
            ],
            predecessor_nodes=["internal_reconciliation", "external_reconciliation"],
            can_be_locked=False,
        ),
        "optional_ai_doc": NodeDefinition(
            name="optional_ai_doc",
            actor_type="ai",
            actor_name="Optional AI Documentation Engine",
            allowed_actions=[
                "generate natural-language documentation of formulas and mappings (if approved per-report)",
                "decline to generate documentation without blocking the pipeline",
            ],
            prohibited_actions=[
                "call the Anthropic API without a logged per-report decision",
                "send unminimized data (see core/llm_data_policy.py)",
                "block the pipeline if documentation generation fails",
            ],
            predecessor_nodes=["gate_3"],
            can_be_locked=True,
        ),
        "gate_4": NodeDefinition(
            name="gate_4",
            actor_type="human",
            actor_name="Chief Risk Officer (Approval)",
            allowed_actions=[
                "record a named approval from a registered CRO",
                "confirm the approval with a timestamp",
                "generate the independence-disclosure disclosure statement",
            ],
            prohibited_actions=[
                "accept a name not in config/authorized_approvers.json with role='cro'",
                "call the approval a 'signature' or an 'attestation'",
                "approve without a name or with an unregistered name",
            ],
            predecessor_nodes=["optional_ai_doc"],
            can_be_locked=True,
        ),
        "pdf_export": NodeDefinition(
            name="pdf_export",
            actor_type="agent",
            actor_name="PDF Report Generator",
            allowed_actions=[
                "generate the final report PDF once gate_4 completes",
                "include disclaimers, named approval, and audit chain summary",
            ],
            prohibited_actions=[
                "enable the export button before gate_4 completes",
                "generate a report without a named approval",
                "omit the tamper-evidence verification results",
            ],
            predecessor_nodes=["gate_4"],
            can_be_locked=True,
        ),
    }


def describe_node(node_name: str, state: PipelineState) -> str:
    """Human-readable summary of one node's status in the pipeline.

    Example output:
      "gate_1 (Qualified Actuary - Preparer): complete (Isaac Shukla)"
      "gate_3: blocked (by gate_3 BLOCK verdict on reconciliation)"
      "pdf_export: waiting for gate_4"
    """
    if node_name not in NODE_ORDER:
        raise ValueError(f"unknown node: {node_name!r}")

    node_state = state.nodes[node_name]
    rules = node_state["rules"]
    status = node_state["status"]
    actor = node_state["actor"]

    name_and_type = f"{node_name} ({rules.actor_name})"

    if status == "complete":
        return f"{name_and_type}: ✓ complete ({actor})"
    elif status == "blocked":
        return f"{name_and_type}: ✗ blocked (by {state.blocking_node})"
    elif status == "waiting":
        predecessor = rules.predecessor_nodes[0] if rules.predecessor_nodes else "start"
        return f"{name_and_type}: ⏳ waiting for {predecessor}"
    elif status == "in_progress":
        return f"{name_and_type}: ⚙ in progress ({actor})"
    else:
        return f"{name_and_type}: ❌ incomplete"


def describe_pipeline(state: PipelineState) -> str:
    """Human-readable summary of the entire pipeline state.

    Returns a multi-line string showing all nodes' statuses in order.
    """
    lines = [
        f"Pipeline: {state.total_events} events | Chain: {'✓ valid' if state.chain_valid else '✗ BROKEN'}",
    ]
    if state.is_all_locked:
        lines.append(f"⚠ LOCKED downstream of {state.blocking_node}: {state.blocking_reason[:60]}...")

    for node in NODE_ORDER:
        lines.append(describe_node(node, state))

    return "\n".join(lines)
