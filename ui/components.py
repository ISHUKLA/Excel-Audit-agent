"""Reusable Streamlit UI components for the audit interface."""

from typing import Optional

import streamlit as st


VERDICT_COLOR = {
    "pass": "#185c2c",
    "warn": "#754b00",
    "block": "#7c1c20",
    "incomplete": "#304f63",
    "not_performed": "#44515b",
}
VERDICT_BG = {
    "pass": "#dff3e4",
    "warn": "#fff2c9",
    "block": "#f9d9da",
    "incomplete": "#dfe8ee",
    "not_performed": "#eceff1",
}

RESPONSIBILITY_ICONS = {
    "deterministic": "⚙️",
    "ai_generated": "🤖",
    "human_required": "👤",
}


def responsibility_badge(responsibility_type: str) -> None:
    """Render a small badge indicating who/what produced this output.

    Args:
        responsibility_type: one of 'deterministic', 'ai_generated', 'human_required'
    """
    labels = {
        "deterministic": "Deterministic Python calculation",
        "ai_generated": "AI-generated explanation",
        "human_required": "Human decision required",
    }
    label = labels.get(responsibility_type, "Unknown")
    icon = RESPONSIBILITY_ICONS.get(responsibility_type, "❓")

    st.caption(f"{icon} {label}")


def verdict_card(verdict: str, label: str, include_explanation: bool = True) -> None:
    """Render a colour-coded verdict box.

    Args:
        verdict: one of 'pass', 'warn', 'block', 'incomplete', 'not_performed'
        label: display label (e.g., "Internal reconciliation")
        include_explanation: if True, show brief explanation of verdict
    """
    palette_key = verdict if verdict in VERDICT_BG else "not_performed"
    display = verdict.upper().replace("_", " ")

    st.markdown(
        f"""<div style='padding:12px 16px;border:2px solid {VERDICT_COLOR[palette_key]};
        border-radius:6px;background-color:{VERDICT_BG[palette_key]};
        color:{VERDICT_COLOR[palette_key]};font-weight:700;font-size:0.95rem;'>
        {label}: {display}</div>""",
        unsafe_allow_html=True,
    )

    if include_explanation:
        explanations = {
            "pass": "All checks passed. No issues detected.",
            "warn": "Some findings present but reconciliation possible.",
            "block": "Reconciliation blocked. Review findings and reference data.",
            "incomplete": "Reconstruction incomplete. Some formulas unsupported.",
            "not_performed": "Not checked or not applicable.",
        }
        st.caption(f"ℹ️ {explanations.get(palette_key, '')}")


def summary_metric(col, label: str, value, unit: str = "", is_alert: bool = False) -> None:
    """Render a consistent metric card.

    Args:
        col: Streamlit column to render in
        label: metric name
        value: metric value (int, float, str, or "Not available")
        unit: optional unit suffix
        is_alert: if True, use warning styling
    """
    if value == "Not available" or value is None:
        col.metric(label, "—", delta=None)
    else:
        val_str = f"{value:,}" if isinstance(value, int) else str(value)
        if unit:
            val_str = f"{val_str} {unit}"
        col.metric(label, val_str, delta=None)


def finding_card(finding, decision: Optional[dict] = None) -> None:
    """Render a single finding with context and reviewer action.

    Args:
        finding: AnomalyFinding object
        decision: dict with 'decision' and 'reason' keys, or None
    """
    col1, col2 = st.columns([2, 1])

    with col1:
        severity_icon = {
            "blocker": "🔴",
            "warning": "🟡",
            "info": "🔵",
        }.get(finding.severity, "⚪")

        st.markdown(f"**{severity_icon} {finding.severity.upper()}** — {finding.tab}!{finding.cell_ref}")
        st.caption(f"📋 {finding.description}")

        if finding.formula:
            st.code(finding.formula, language="excel")

    with col2:
        if decision:
            decision_text = decision.get("decision", "—").upper()
            st.markdown(f"**Disposition:** {decision_text}")
            if decision.get("reason"):
                st.caption(f"Reason: {decision['reason']}")
        else:
            st.markdown("**Disposition:** Pending")


def reconciliation_line_card(line, is_internal: bool = True) -> None:
    """Render a single reconciliation line with source and verdict.

    Args:
        line: ReconciliationLine object
        is_internal: if True, show internal verdict; else external
    """
    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        st.markdown(f"**{line.cell_ref}**")
        st.caption(f"Cached: {line.cached_value} | Reconstructed: {line.reconstructed_value}")

    with col2:
        verdict = line.internal_verdict if is_internal else line.external_verdict
        verdict_key = verdict if verdict in VERDICT_COLOR else "not_performed"
        st.markdown(
            f"""<span style='color:{VERDICT_COLOR[verdict_key]};font-weight:700;'>
            {verdict.upper().replace("_", " ")}</span>""",
            unsafe_allow_html=True,
        )

    with col3:
        delta_val = line.delta if line.delta is not None else "—"
        st.metric("Delta", delta_val, delta=None)
