"""Streamlit visualization for the governance flight recorder pipeline.

Displays a live execution map showing the status of each node in the 9-node
governance pipeline, cascading locks, evidence flow, and audit chain integrity.
"""

import streamlit as st
from typing import Optional

from core.audit_log import AuditLog
from core.flight_recorder_queries import (
    get_report_events,
    verify_audit_chain,
    extract_evidence_hash,
)
from core.flight_recorder_state import (
    build_pipeline_state,
    describe_node,
    describe_pipeline,
    NODE_ORDER,
    PipelineState,
    NodeDefinition,
)


def display_flight_recorder(audit_log: AuditLog, report_id: str) -> None:
    """Render the governance flight recorder as a live execution map.

    Args:
        audit_log: AuditLog instance connected to audit.db
        report_id: Report identifier to display
    """
    try:
        events = get_report_events(audit_log, report_id)
        chain_valid, chain_message = verify_audit_chain(audit_log, report_id)
        state = build_pipeline_state(events, chain_valid=chain_valid)
    except Exception as e:
        st.error(f"Error loading flight recorder: {e}")
        return

    _render_header(report_id, state)
    _render_cascading_lock_warning(state)
    _render_pipeline_visualization(state)
    _render_chain_verification(chain_valid, chain_message, audit_log, report_id)


def _render_header(report_id: str, state: PipelineState) -> None:
    """Render title, subtitle, and metric boxes."""
    st.markdown(f"## Governance Flight Recorder — Report `{report_id}`")
    st.markdown("### Live Execution Map")

    col1, col2, col3 = st.columns(3)

    with col1:
        chain_status = "✓ Valid" if state.chain_valid else "✗ Broken"
        st.metric("Chain Status", chain_status)

    with col2:
        st.metric("Total Events", state.total_events)

    with col3:
        locked_count = len(state.cascading_lock.get("locked_nodes", []))
        st.metric("Locked Nodes", locked_count)

    st.divider()


def _render_cascading_lock_warning(state: PipelineState) -> None:
    """Render a red banner if pipeline is locked due to gate_3 block."""
    if state.is_all_locked and state.blocking_node:
        st.warning(
            f"⚠️ **PIPELINE LOCKED**: {state.blocking_node} produced a BLOCK verdict\n\n"
            f"{state.blocking_reason}"
        )
        st.divider()


def _render_pipeline_visualization(state: PipelineState) -> None:
    """Render the 9-node pipeline as expandable cards."""
    st.markdown("### Pipeline Nodes")

    for node_name in NODE_ORDER:
        node_state = state.nodes[node_name]
        rules = node_state["rules"]
        status = node_state["status"]
        actor = node_state["actor"]
        is_locked = node_state["is_locked"]
        event = node_state["event"]

        _render_node_card(
            node_name=node_name,
            status=status,
            is_locked=is_locked,
            actor=actor,
            actor_name=rules.actor_name,
            actor_type=rules.actor_type,
            event=event,
            evidence_summary=node_state["evidence_summary"],
            rules=rules,
        )


def _render_node_card(
    node_name: str,
    status: str,
    is_locked: bool,
    actor: Optional[str],
    actor_name: str,
    actor_type: str,
    event: Optional[dict],
    evidence_summary: dict,
    rules: NodeDefinition,
) -> None:
    """Render one node as an expandable card with status, evidence, and rules."""
    status_emoji = _status_emoji(status)
    status_color = _status_color(status, is_locked)

    header_text = f"{status_emoji} **{node_name}** — {actor_name}"
    if is_locked:
        header_text += " 🔒"

    with st.expander(header_text, expanded=False):
        col1, col2 = st.columns(2)

        with col1:
            st.markdown(f"**Status**: {status_color}")
            st.markdown(f"**Actor Type**: {actor_type}")
            if actor:
                st.markdown(f"**Actor**: {actor}")

        with col2:
            if event:
                st.markdown(f"**Timestamp**: {event.get('timestamp', 'N/A')}")
                event_hash = extract_evidence_hash(event, short=True)
                st.markdown(f"**Hash**: `{event_hash}`")

        st.divider()

        if evidence_summary:
            st.markdown("**Evidence Summary**")
            for key, value in evidence_summary.items():
                if value is not None:
                    st.markdown(f"- {key}: {value}")

        st.divider()

        st.markdown("**Governance Rules**")
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("*Allowed Actions*")
            for action in rules.allowed_actions:
                st.markdown(f"✓ {action}")

        with col2:
            st.markdown("*Prohibited Actions*")
            for action in rules.prohibited_actions:
                st.markdown(f"✗ {action}")

        st.divider()

        st.markdown("**Predecessors**")
        if rules.predecessor_nodes:
            for pred in rules.predecessor_nodes:
                st.markdown(f"← {pred}")
        else:
            st.markdown("(none — this is the first node)")

        if event and event.get("payload_json"):
            with st.expander("View Raw Event JSON", expanded=False):
                st.code(event["payload_json"], language="json")


def _status_emoji(status: str) -> str:
    """Return emoji indicator for node status."""
    return {
        "complete": "✓",
        "waiting": "⏳",
        "blocked": "✗",
        "in_progress": "⚙",
        "incomplete": "❌",
    }.get(status, "?")


def _status_color(status: str, is_locked: bool = False) -> str:
    """Return colored status text."""
    if is_locked:
        return f":gray[{status} — locked]"

    colors = {
        "complete": "green",
        "waiting": "orange",
        "blocked": "red",
        "in_progress": "blue",
        "incomplete": "orange",
    }
    color = colors.get(status, "gray")
    return f":{color}[{status}]"


def _render_chain_verification(
    chain_valid: bool, chain_message: str, audit_log: AuditLog, report_id: str
) -> None:
    """Render audit chain verification section with refresh button."""
    st.divider()
    st.markdown("### Audit Chain Verification")

    if chain_valid:
        st.success(f"✓ {chain_message}")
    else:
        st.error(f"✗ {chain_message}")

    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("🔄 Verify Chain Now", key="verify_chain"):
            chain_valid, chain_message = verify_audit_chain(audit_log, report_id)
            if chain_valid:
                st.success(f"✓ {chain_message}")
            else:
                st.error(f"✗ {chain_message}")
