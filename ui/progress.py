"""Persistent progress indicator for the five-gate pipeline."""

import streamlit as st


def progress_indicator() -> None:
    """Render a persistent progress indicator in the sidebar.

    Shows real-time progress through the five gates, driven by actual backend state.
    States:
    1. ⏳ Context confirmation — st.session_state.gate1_context_confirmed
    2. 📋 Findings reviewed — all findings have human_decision
    3. 📊 Reconciliation completed — internal_verdict is not None
    4. ✍️ Named approval recorded — approval_name is not empty
    5. 📄 Report available — pdf_bytes is not None
    """
    st.sidebar.markdown("## Pipeline Progress")

    findings = st.session_state.get("findings", [])
    all_decided = (
        len(findings) == 0
        or all(f.finding_id in st.session_state.get("finding_decisions", {}) for f in findings)
    )

    steps = [
        ("Context confirmation", st.session_state.get("gate1_context_confirmed", False)),
        ("Findings reviewed", all_decided and len(findings) > 0 or len(findings) == 0),
        ("Reconciliation completed", st.session_state.get("internal_verdict") is not None),
        ("Named approval recorded", bool(st.session_state.get("approval_name", "").strip())),
        ("Report available", st.session_state.get("pdf_bytes") is not None),
    ]

    for i, (label, is_complete) in enumerate(steps, 1):
        icon = "✅" if is_complete else "⏳"
        st.sidebar.markdown(f"{i}. {icon} {label}")

    # Overall completion percentage
    completed = sum(1 for _, complete in steps if complete)
    st.sidebar.progress(completed / len(steps), f"{completed}/{len(steps)} gates")
