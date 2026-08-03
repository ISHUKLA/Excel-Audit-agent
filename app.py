"""Streamlit entry point: wires the human gates and agent pipeline into a single-page UI."""

import os
import tempfile
from datetime import datetime

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from agents.orchestrator import Orchestrator
from agents.reconciliation import apply_thresholds
from core.gates import GateBlockedError
from core.models import FileContext, ReferenceFigures
from report.generator import generate_report_pdf

# Local dev reads ANTHROPIC_API_KEY from .env; Streamlit Cloud reads it from
# st.secrets, which isn't automatically exported as an OS env var, so it's
# bridged here -- agents/documentation.py's Anthropic client only checks os.environ.
load_dotenv()
try:
    if "ANTHROPIC_API_KEY" in st.secrets:
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except FileNotFoundError:
    pass  # no .streamlit/secrets.toml -- fine locally, where .env is used instead

st.set_page_config(page_title="Excel Audit Agent", layout="wide")

ROLE_OPTIONS = ["actuary", "cro", "cfo", "auditor"]
SEVERITY_BADGE = {"blocker": "🔴 BLOCKER", "warning": "🟡 WARNING", "info": "🔵 INFO"}
VERDICT_COLOR = {"pass": "#155724", "warn": "#856404", "block": "#721c24", "not_performed": "#383d41"}
VERDICT_BG = {"pass": "#d4edda", "warn": "#fff3cd", "block": "#f8d7da", "not_performed": "#e2e3e5"}


def _orchestrator() -> Orchestrator:
    if "orchestrator" not in st.session_state:
        st.session_state.orchestrator = Orchestrator()
    return st.session_state.orchestrator


def _init_state() -> None:
    st.session_state.setdefault("screen", 1)
    st.session_state.setdefault("report_id", None)
    st.session_state.setdefault("findings", [])
    st.session_state.setdefault("finding_decisions", {})
    st.session_state.setdefault("reconciliation_lines", [])
    st.session_state.setdefault("unmatched_reference_items", [])
    st.session_state.setdefault("reference_figures", None)
    st.session_state.setdefault("reviewer_name", "")
    st.session_state.setdefault("reviewer_role", ROLE_OPTIONS[0])
    st.session_state.setdefault("final_report", None)
    st.session_state.setdefault("pdf_bytes", None)
    st.session_state.setdefault("audit_decisions", [])


def _verdict_banner(verdict: str, label: str) -> None:
    st.markdown(
        f"<div style='padding:10px 14px;border-radius:4px;background-color:{VERDICT_BG[verdict]};"
        f"color:{VERDICT_COLOR[verdict]};font-weight:bold;'>{label}: {verdict.upper().replace('_', ' ')}</div>",
        unsafe_allow_html=True,
    )


def screen_1_upload() -> None:
    st.header("Screen 1 — Upload")

    uploaded_file = st.file_uploader("Upload Excel file", type=["xlsx"])
    description = st.text_area("Describe what this file does")
    role = st.selectbox("Your role", ROLE_OPTIONS, format_func=str.upper)
    reviewer_name = st.text_input("Your name", value=st.session_state.reviewer_name)

    reference_figures = None
    with st.expander("Add reference figures (for CFO reconciliation)"):
        source_label = st.text_input(
            "Reference figures source (e.g. 'Q4 trial balance extract')", key="ref_source_label"
        )
        st.caption("Fill in the table below, or upload a CSV with 'label' and 'value' columns.")
        csv_file = st.file_uploader("Upload CSV instead", type=["csv"], key="ref_csv")
        editable = st.data_editor(
            pd.DataFrame({"label": [""], "value": [0.0]}),
            num_rows="dynamic",
            key="ref_table",
        )

        line_items: dict[str, float] = {}
        if csv_file is not None:
            csv_df = pd.read_csv(csv_file)
            for _, row in csv_df.iterrows():
                label = str(row.get("label", "")).strip()
                if label:
                    line_items[label] = float(row["value"])
        else:
            for _, row in editable.iterrows():
                label = str(row.get("label", "")).strip()
                if label:
                    line_items[label] = float(row.get("value", 0.0) or 0.0)

        if line_items and source_label.strip():
            reference_figures = ReferenceFigures(
                source_label=source_label.strip(), line_items=line_items, uploaded_at=datetime.now()
            )
        elif line_items and not source_label.strip():
            st.warning("Enter a source label to include these reference figures.")

    if st.button("Start audit", type="primary"):
        if uploaded_file is None:
            st.error("Please upload an .xlsx file.")
            return
        if not description.strip():
            st.error("Please describe what this file does.")
            return
        if not reviewer_name.strip():
            st.error("Please enter your name.")
            return

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp_path = tmp.name

        file_context = FileContext(
            filename=uploaded_file.name,
            description=description.strip(),
            user_role=role,
            uploaded_at=datetime.now(),
        )

        try:
            report_id, findings = _orchestrator().run(
                tmp_path,
                file_context,
                user_name=reviewer_name.strip(),
                context_confirmed=True,
                reference_figures=reference_figures,
                reference_figures_confirmed=True,
            )
        except GateBlockedError as exc:
            st.error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 -- surface any parse/processing failure to the user
            st.error(f"Could not process this file: {exc}")
            return

        st.session_state.report_id = report_id
        st.session_state.findings = findings
        st.session_state.reference_figures = reference_figures
        st.session_state.reviewer_name = reviewer_name.strip()
        st.session_state.reviewer_role = role
        st.session_state.finding_decisions = {}
        st.session_state.screen = 2
        st.rerun()


def screen_2_findings_review() -> None:
    st.header("Screen 2 — Gate 2: Findings review")

    findings = st.session_state.findings
    decisions = st.session_state.finding_decisions

    decided_count = sum(1 for f in findings if f.finding_id in decisions)
    st.progress(decided_count / len(findings) if findings else 1.0)
    st.caption(f"{decided_count} of {len(findings)} findings reviewed")

    if not findings:
        st.info("No findings were raised for this file.")

    for finding in findings:
        with st.container(border=True):
            st.markdown(f"**{SEVERITY_BADGE[finding.severity]}** — `{finding.tab}!{finding.cell_ref}`")
            st.write(finding.description)

            current = decisions.get(finding.finding_id, {}).get("decision")
            col1, col2, col3 = st.columns(3)
            if col1.button(
                "Confirm", key=f"confirm_{finding.finding_id}",
                type="primary" if current == "confirmed" else "secondary",
            ):
                decisions[finding.finding_id] = {"decision": "confirmed", "reason": ""}
            if col2.button(
                "Override", key=f"override_{finding.finding_id}",
                type="primary" if current == "overridden" else "secondary",
            ):
                decisions[finding.finding_id] = {
                    "decision": "overridden",
                    "reason": decisions.get(finding.finding_id, {}).get("reason", ""),
                }
            if col3.button(
                "Dismiss", key=f"dismiss_{finding.finding_id}",
                type="primary" if current == "dismissed" else "secondary",
            ):
                decisions[finding.finding_id] = {
                    "decision": "dismissed",
                    "reason": decisions.get(finding.finding_id, {}).get("reason", ""),
                }

            if decisions.get(finding.finding_id, {}).get("decision") in ("overridden", "dismissed"):
                decisions[finding.finding_id]["reason"] = st.text_input(
                    "Reason (required)",
                    value=decisions[finding.finding_id].get("reason", ""),
                    key=f"reason_{finding.finding_id}",
                )

    all_decided = all(f.finding_id in decisions for f in findings)
    all_reasons_ok = all(
        decisions[f.finding_id]["decision"] == "confirmed" or decisions[f.finding_id].get("reason", "").strip()
        for f in findings
        if f.finding_id in decisions
    )
    ready = bool(findings) and all_decided and all_reasons_ok

    if st.button("Submit all decisions", disabled=not ready, type="primary"):
        decided_findings = [
            finding.model_copy(
                update={
                    "human_decision": decisions[finding.finding_id]["decision"],
                    "human_reason": decisions[finding.finding_id]["reason"] or None,
                    "decided_by": st.session_state.reviewer_name,
                    "decided_at": datetime.now(),
                }
            )
            for finding in findings
        ]
        try:
            lines, unmatched = _orchestrator().submit_findings_decisions(
                st.session_state.report_id, decided_findings
            )
        except GateBlockedError as exc:
            st.error(str(exc))
            return

        st.session_state.findings = decided_findings
        st.session_state.reconciliation_lines = lines
        st.session_state.unmatched_reference_items = unmatched
        st.session_state.screen = 3
        st.rerun()


def screen_3_reconciliation() -> None:
    st.header("Screen 3 — Gate 3: Reconciliation")

    lines = st.session_state.reconciliation_lines

    st.subheader("Internal consistency (Excel vs Python)")
    internal_threshold_pct = st.number_input(
        "Internal materiality threshold (%)", min_value=0.0, value=1.0, step=0.1
    )
    internal_threshold = internal_threshold_pct / 100

    st.subheader("Accounts reconciliation (CFO)")
    if st.session_state.reference_figures is None:
        st.info("No reference figures provided — accounts reconciliation skipped")
        external_threshold = 0.01
    else:
        external_threshold_pct = st.number_input(
            "External materiality threshold (%)", min_value=0.0, value=1.0, step=0.1
        )
        external_threshold = external_threshold_pct / 100

    # Pure, side-effect-free reclassification for live preview -- Gate 3 itself
    # (with its audit logging) only fires when "Confirm reconciliation" is clicked.
    preview_lines = apply_thresholds(lines, internal_threshold, external_threshold)
    internal_lines = [line for line in preview_lines if line.check_type == "excel_vs_python"]
    external_lines = [line for line in preview_lines if line.check_type == "python_vs_accounts"]

    st.markdown("**Internal consistency table**")
    if internal_lines:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Label": line.label,
                        "Excel value": line.source_value,
                        "Python value": line.target_value,
                        "Delta": line.delta,
                        "Delta %": f"{line.delta_pct * 100:.2f}%",
                        "Verdict": line.verdict.upper(),
                    }
                    for line in internal_lines
                ]
            ),
            width="stretch",
        )
    else:
        st.info("No internal comparisons were produced for this file.")

    if st.session_state.reference_figures is not None:
        st.markdown("**Accounts reconciliation table**")
        if external_lines:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Label": line.label + (" ⚠️ match confidence: low" if line.match_note else ""),
                            "Python value": line.source_value,
                            "Accounts value": line.target_value,
                            "Delta": line.delta,
                            "Delta %": f"{line.delta_pct * 100:.2f}%",
                            "Verdict": line.verdict.upper(),
                        }
                        for line in external_lines
                    ]
                ),
                width="stretch",
            )
        else:
            st.info("No accounts figures could be matched to a computed value.")

        if st.session_state.unmatched_reference_items:
            st.markdown("**Reference figures that could not be matched:**")
            for item in st.session_state.unmatched_reference_items:
                st.write(f"- {item}")

    has_blocker = any(line.verdict == "block" for line in preview_lines)
    if has_blocker:
        st.error("Must be resolved before proceeding")

    if st.button("Confirm reconciliation", disabled=has_blocker, type="primary"):
        try:
            internal_verdict, external_verdict = _orchestrator().submit_reconciliation_decisions(
                st.session_state.report_id,
                lines,
                internal_threshold,
                external_threshold,
                user_name=st.session_state.reviewer_name,
            )
        except GateBlockedError as exc:
            st.error(str(exc))
            return

        st.session_state.screen = 4
        st.rerun()


def screen_4_signoff() -> None:
    st.header("Screen 4 — Gate 4: Sign-off")

    report_preview = _orchestrator().get_report(st.session_state.report_id)
    findings = st.session_state.findings
    blockers = [f for f in findings if f.severity == "blocker"]
    blockers_resolved = sum(1 for f in blockers if f.human_decision in ("overridden", "dismissed"))

    st.write(f"**Total findings:** {len(findings)}")
    st.write(f"**Blockers resolved:** {blockers_resolved} of {len(blockers)}")
    _verdict_banner(report_preview.overall_verdict, "Overall verdict")

    signed_by = st.text_input("Your full name", value=st.session_state.reviewer_name)
    role = st.text_input("Your role at the organisation", value=st.session_state.reviewer_role)

    ready = bool(signed_by.strip()) and bool(role.strip())
    if st.button("Sign and generate report", disabled=not ready, type="primary"):
        try:
            report = _orchestrator().submit_signoff(st.session_state.report_id, signed_by.strip(), role.strip())
        except GateBlockedError as exc:
            st.error(str(exc))
            return

        decisions = _orchestrator().get_decisions(st.session_state.report_id)
        pdf_bytes = generate_report_pdf(report, decisions)

        st.session_state.final_report = report
        st.session_state.pdf_bytes = pdf_bytes
        st.session_state.audit_decisions = decisions
        st.session_state.screen = 5
        st.rerun()


def screen_5_report() -> None:
    st.header("Screen 5 — Report")

    report = st.session_state.final_report

    st.download_button(
        "Download PDF",
        data=st.session_state.pdf_bytes,
        file_name=f"audit_report_{report.report_id}.pdf",
        mime="application/pdf",
    )

    _verdict_banner(report.overall_verdict, "Overall verdict")
    col1, col2 = st.columns(2)
    with col1:
        _verdict_banner(report.internal_verdict, "Internal consistency")
    with col2:
        if report.reference_figures is None:
            st.info("Accounts reconciliation (CFO): Not performed")
        else:
            _verdict_banner(report.external_verdict, "Accounts reconciliation (CFO)")

    st.subheader("Traceability index (preview)")
    if report.traceability_index:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Figure": entry.report_figure_label,
                        "Value": entry.report_value,
                        "Source tab": entry.source_tab or "Not traceable",
                        "Source cell": entry.source_cell or "Not traceable",
                        "Derivation": entry.derivation_note,
                    }
                    for entry in report.traceability_index[:10]
                ]
            ),
            width="stretch",
        )
        st.caption("full index in the PDF")
    else:
        st.info("No traceable figures were produced for this file.")

    st.subheader("Audit trail")
    if st.session_state.audit_decisions:
        st.dataframe(pd.DataFrame(st.session_state.audit_decisions), width="stretch")
    else:
        st.info("No audit trail entries were recorded for this report.")


def main() -> None:
    _init_state()
    screens = {
        1: screen_1_upload,
        2: screen_2_findings_review,
        3: screen_3_reconciliation,
        4: screen_4_signoff,
        5: screen_5_report,
    }
    screens[st.session_state.screen]()


if __name__ == "__main__":
    main()
