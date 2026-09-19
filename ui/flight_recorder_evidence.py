"""Presentation helpers for governance flight recorder evidence and rules.

Small, composable display functions used by ui/flight_recorder.py (and
reusable elsewhere) to render a hash, a node's allowed/prohibited actions,
evidence flow, a cascading-lock banner, and chain-verification status in a
consistent, readable shape.

None of these functions read from `audit.db` or the orchestrator directly —
callers pass in already-fetched values. This keeps the module pure rendering,
matching the app.py convention that UI modules call and render only.

The "chain broken" recovery button here is deliberately inert: clicking it
shows the same refusal message app.py already renders via
`_chain_integrity_error` ("nothing has been altered or repaired"). The audit
log is tamper-evident, not tamper-proof (CLAUDE.md Rule 14) — there is no
code path in this repo that repairs a broken hash chain, and this module does
not invent one.
"""

from typing import List, Optional

import streamlit as st

from core.flight_recorder_state import node_definitions


def display_evidence_hash(hash_value: str, label: str = "Hash") -> None:
    """Show a hash value with a shortened preview and a copy-to-clipboard control.

    Streamlit has no native clipboard API, so "Copy" is simulated with a
    disabled text_input the user can select-and-copy from — clicking the
    button reveals it rather than silently writing to the clipboard.
    """
    if not hash_value:
        st.caption(f"{label}: not available")
        return

    short = hash_value[:12]
    st.markdown(f"**{label}**: `{short}...`")

    copy_key = f"copy_{label}_{short}"
    if st.button("📋 Copy", key=copy_key):
        st.session_state[f"{copy_key}_show"] = True

    if st.session_state.get(f"{copy_key}_show"):
        st.text_input(
            f"Full {label.lower()} — select and copy",
            value=hash_value,
            key=f"{copy_key}_field",
        )


def display_node_rules(node_name: str) -> None:
    """Show a node's allowed and prohibited actions as color-coded bulleted lists."""
    definitions = node_definitions()
    if node_name not in definitions:
        st.error(f"Unknown node: {node_name!r}")
        return

    rules = definitions[node_name]

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(":green[**✓ Allowed**]")
        if rules.allowed_actions:
            for action in rules.allowed_actions:
                st.markdown(f":green[• {action}]")
        else:
            st.caption("(none recorded)")

    with col2:
        st.markdown(":red[**✗ Prohibited**]")
        if rules.prohibited_actions:
            for action in rules.prohibited_actions:
                st.markdown(f":red[• {action}]")
        else:
            st.caption("(none recorded)")


def display_evidence_in_out(
    evidence_in: List[str], evidence_out: List[str], node_name: str = ""
) -> None:
    """Show what evidence entered a node and what exited it, side by side."""
    label = f"[{node_name}]" if node_name else "[node]"
    st.markdown(f"**Evidence In** → {label} → **Evidence Out**")

    col1, col2 = st.columns(2)
    with col1:
        if evidence_in:
            for item in evidence_in:
                st.markdown(f"→ {item}")
        else:
            st.caption("(nothing recorded as input)")

    with col2:
        if evidence_out:
            for item in evidence_out:
                st.markdown(f"{item} →")
        else:
            st.caption("(nothing recorded as output)")


def display_cascade_lock_banner(
    blocking_node: str, blocking_reason: str, locked_nodes: Optional[List[str]] = None
) -> None:
    """Show a prominent warning banner for a cascading pipeline lock."""
    locked_nodes = locked_nodes or []

    st.error(
        "⚠️ **PIPELINE LOCKED**\n\n"
        f"**Blocking Node**: `{blocking_node}`\n\n"
        f"**Reason**: {blocking_reason}\n\n"
        f"**Locked Nodes**: {', '.join(locked_nodes) if locked_nodes else 'none'}"
    )


def display_chain_verification(
    is_valid: bool,
    verification_msg: str,
    broken_events: Optional[List[dict]] = None,
    event_count: Optional[int] = None,
    last_hash: Optional[str] = None,
) -> None:
    """Show chain-verification status, with an inert recovery control when broken.

    `broken_events` (if provided) is a list of event dicts to list under the
    failure message. The "Revert and Unlock?" button never mutates any state —
    the audit log is tamper-evident, not tamper-proof, and no code path here
    repairs a broken chain (CLAUDE.md Rule 14).
    """
    if is_valid:
        st.success(f"✓ {verification_msg}")
        metric_parts = []
        if event_count is not None:
            metric_parts.append(f"Events: {event_count}")
        if last_hash:
            metric_parts.append(f"Last hash: {last_hash[:8]}")
        if metric_parts:
            st.caption(" | ".join(metric_parts))
        return

    st.error(f"✗ CHAIN BROKEN\n\n{verification_msg}")

    if broken_events:
        st.markdown("**Broken events:**")
        for event in broken_events:
            row_id = event.get("row_id", "?")
            event_hash = event.get("event_hash", "")[:8]
            previous_hash = event.get("previous_hash", "")[:8]
            st.write(f"- row_id={row_id}: hash=`{event_hash}`, previous=`{previous_hash}`")

    if st.button("🔓 Revert and Unlock?", key="revert_unlock_chain"):
        st.error(
            "❌ Recovery refused: the chain is broken and cannot be repaired.\n\n"
            "The hash chain is tamper-evident, not tamper-proof — it detects "
            "protected-field changes and in-sequence removal or reordering, but "
            "needs an external checkpoint to detect tail or whole-file truncation. "
            "Nothing here can undo a change, identify who made it, or restore trust "
            "in rows after the break. Nothing has been altered in response to this click."
        )


def display_node_card(node_name: str, node_state: dict) -> None:
    """Unified card for one pipeline node: status, actor, evidence, timestamp, rules.

    `node_state` follows the shape produced by
    `core.flight_recorder_state.build_pipeline_state()`:
        {status, actor, event, evidence_summary, rules, is_locked}
    """
    status = node_state.get("status", "unknown")
    actor = node_state.get("actor")
    event = node_state.get("event")
    evidence_summary = node_state.get("evidence_summary", {})
    rules = node_state.get("rules")
    is_locked = node_state.get("is_locked", False)

    status_colors = {
        "complete": "green",
        "waiting": "orange",
        "blocked": "red",
        "in_progress": "blue",
        "incomplete": "orange",
    }
    status_emojis = {
        "complete": "✓",
        "waiting": "⏳",
        "blocked": "✗",
        "in_progress": "⚙",
        "incomplete": "❌",
    }
    color = status_colors.get(status, "gray")
    emoji = status_emojis.get(status, "?")

    actor_name = rules.actor_name if rules else node_name
    actor_type = rules.actor_type if rules else "unknown"

    header = f"{emoji} **{node_name}** — {actor_name}"
    if is_locked:
        header += " 🔒"

    container = st.expander(header, expanded=False)
    with container:
        if is_locked:
            st.markdown(":gray[**locked**]")
        else:
            st.markdown(f":{color}[**{status}**]")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Actor type**: {actor_type}")
            if actor:
                st.markdown(f"**Actor**: {actor}")
        with col2:
            if event:
                st.markdown(f"**Timestamp**: {event.get('timestamp', 'N/A')}")
                event_hash = event.get("event_hash", "")
                if event_hash:
                    display_evidence_hash(event_hash, label="Event hash")

        if evidence_summary:
            st.markdown("**Evidence**")
            for key, value in evidence_summary.items():
                if value is not None:
                    st.markdown(f"- {key}: {value}")

        if rules:
            with st.expander("Governance rules", expanded=False):
                display_node_rules(node_name)

    return container
