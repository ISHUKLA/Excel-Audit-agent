"""Streamlit interface for the five-stage Excel audit review workflow.

This module collects input, calls the orchestrator, and renders returned model
data. Reconciliation, gate, mapping, and evidence-integrity decisions remain in
the non-UI modules.
"""

import json
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from agents.orchestrator import Orchestrator, PipelineStateError
from core.gates import GateBlockedError
from core.models import FileContext, MappingReviewDecision, ParsedFile, ReconciliationResult
from core.state_store import ChainIntegrityError
from core.workbook_identity import WorkbookIdentityError, sha256_bytes
from core.ui_inputs import (
    ReferenceFigureInputError,
    build_reference_figures,
    validate_reference_csv_columns,
)
from report.generator import generate_report_pdf

ROLE_OPTIONS = ["actuary", "cro", "cfo", "auditor"]
SEVERITY_BADGE = {
    "blocker": "🔴 BLOCKER",
    "warning": "🟡 WARNING",
    "info": "🔵 INFO",
}
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
REFERENCE_COLUMNS = [
    "account_number",
    "label",
    "debit_credit",
    "amount",
    "ledger_source",
    "evidence_reference",
]


def _load_environment() -> None:
    load_dotenv()
    try:
        api_key = st.secrets.get("ANTHROPIC_API_KEY")
    except FileNotFoundError:
        api_key = None
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key


def _orchestrator() -> Orchestrator:
    if "orchestrator" not in st.session_state:
        st.session_state.orchestrator = Orchestrator()
    return st.session_state.orchestrator


def _init_state() -> None:
    defaults = {
        "screen": 1,
        "report_id": None,
        "parsed_file": None,
        "findings": [],
        "finding_decisions": {},
        "authoritative_outputs": [],
        "reconciliation_result": None,
        "mapping_decisions": {},
        "reference_figures": None,
        "reviewer_name": "",
        "reviewer_role": ROLE_OPTIONS[0],
        "context_match_verdict": "not_checked",
        "materiality_defaults": None,
        "internal_verdict": None,
        "external_verdict": None,
        "final_result": None,
        "report_preview": None,
        "report_preparation_error": None,
        "final_report": None,
        "pdf_bytes": None,
        "pdf_generation_error": None,
        "audit_rows": [],
        "integrity_result": None,
        "approval_name": "",
        "approval_role": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _verdict_banner(verdict: str, label: str) -> None:
    palette_key = verdict if verdict in VERDICT_BG else "not_performed"
    display = verdict.upper().replace("_", " ")
    st.markdown(
        "<div style='padding:10px 14px;border:1px solid "
        f"{VERDICT_COLOR[palette_key]};border-radius:4px;background-color:"
        f"{VERDICT_BG[palette_key]};color:{VERDICT_COLOR[palette_key]};"
        f"font-weight:700'>{label}: {display}</div>",
        unsafe_allow_html=True,
    )


def _chain_integrity_error(exc: ChainIntegrityError) -> None:
    """Render a refused recovery. Rendering only — the refusal itself is made
    in core/state_store.py, per the rule that app.py holds no business logic."""
    st.error(str(exc))
    st.caption(
        "The audit log is tamper-evident, not tamper-proof: the hash chain makes "
        "a change detectable after the fact, but it cannot prevent one, identify "
        "who made it, or establish when. Nothing has been altered or repaired in "
        "response to this check."
    )


def _workbook_identity_panel(uploaded_file) -> str | None:
    """Show which workbook is being confirmed, and reset the confirmation if it
    changes. Rendering and session bookkeeping only — the binding is enforced in
    the orchestrator, which never trusts anything decided here.

    A filename is not an identity: two different workbooks can share a name. The
    short hash is for the eye, the full 64 characters are what can actually be
    checked against an external record, so both are shown.
    """
    if uploaded_file is None:
        st.session_state.confirmed_workbook_hash = None
        return None

    workbook_bytes = uploaded_file.getvalue()
    workbook_hash = sha256_bytes(workbook_bytes)

    # Requirement 4: a new file — even one with the same name — invalidates the
    # previous confirmation, because it is a different workbook.
    if st.session_state.get("confirmed_workbook_hash") != workbook_hash:
        st.session_state.confirmed_workbook_hash = workbook_hash
        st.session_state.gate1_context_confirmed = False

    st.markdown("**Workbook identity**")
    identity_columns = st.columns([1, 1, 2])
    identity_columns[0].metric("File", uploaded_file.name)
    identity_columns[1].metric("Size", f"{len(workbook_bytes):,} bytes")
    identity_columns[2].metric("SHA-256 (short)", workbook_hash[:12])
    st.code(workbook_hash, language=None)
    st.caption(
        "The full SHA-256 of the uploaded bytes. Reproduce it with "
        "`shasum -a 256 <file>`. Confirming below binds this review to these "
        "exact bytes; uploading a different file, even under the same name, "
        "clears the confirmation."
    )
    return workbook_hash


def _empty_reference_table() -> pd.DataFrame:
    return pd.DataFrame([{column: None for column in REFERENCE_COLUMNS}])


def screen_1_upload() -> None:
    st.header("Screen 1 — Upload")

    uploaded_file = st.file_uploader("Upload Excel file", type=["xlsx"], key="workbook_upload")
    description = st.text_area("Describe what this file does", key="file_description")
    role = st.selectbox(
        "Role",
        ROLE_OPTIONS,
        format_func=lambda value: value.upper() if value in {"cro", "cfo"} else value.title(),
        key="file_role",
    )
    reviewer_name = st.text_input("Reviewer full name (for Gates 1–3)", key="reviewer_input")

    context_col1, context_col2, context_col3 = st.columns(3)
    entity = context_col1.text_input("Entity", key="file_entity")
    period = context_col2.text_input("Period", placeholder="2025-Q4", key="file_period")
    currency = context_col3.text_input("Currency", placeholder="EUR", key="file_currency")
    basis = st.text_input("Basis (optional)", placeholder="IFRS 17", key="file_basis")

    include_reference = False
    reference_rows = None
    reference_from_csv = False
    with st.expander("Add reference figures (for CFO reconciliation)"):
        include_reference = st.checkbox(
            "Include reference figures in this audit", key="include_reference"
        )
        if include_reference:
            source_label = st.text_input(
                "Reference figures source",
                placeholder="Q4 trial balance extract",
                key="ref_source_label",
            )
            ref_col1, ref_col2, ref_col3 = st.columns(3)
            ref_entity = ref_col1.text_input("Extract entity", value=entity, key="ref_entity")
            ref_period = ref_col2.text_input("Extract period", value=period, key="ref_period")
            ref_currency = ref_col3.text_input(
                "Extract currency", value=currency, key="ref_currency"
            )
            ref_basis = st.text_input("Extract basis (optional)", value=basis, key="ref_basis")

            control_col1, control_col2 = st.columns(2)
            control_total = control_col1.number_input(
                "Signed net control total",
                value=None,
                step=100.0,
                key="ref_control_total",
                help="Debit lines are positive and credit lines are negative.",
            )
            control_confirmed = control_col2.checkbox(
                "I confirm this extract ties to the control total above",
                value=False,
                key="ref_control_confirmed",
            )

            csv_file = st.file_uploader(
                "Upload CSV instead of manual entry",
                type=["csv"],
                key="ref_csv",
                help=(
                    "Required columns: account_number, label, amount, debit_credit. "
                    "Optional columns: ledger_source, evidence_reference."
                ),
            )
            reference_from_csv = csv_file is not None
            if reference_from_csv:
                try:
                    csv_frame = pd.read_csv(csv_file)
                    validate_reference_csv_columns(csv_frame.columns)
                except (ReferenceFigureInputError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
                    st.error(str(exc))
                    reference_rows = None
                else:
                    reference_rows = csv_frame.to_dict(orient="records")
                    st.dataframe(csv_frame, width="stretch")
            else:
                st.caption(
                    "Enter one line per account. Duplicate labels and account numbers are preserved."
                )
                edited = st.data_editor(
                    _empty_reference_table(),
                    num_rows="dynamic",
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "debit_credit": st.column_config.SelectboxColumn(
                            "debit_credit", options=["debit", "credit"]
                        ),
                        "amount": st.column_config.NumberColumn("amount", min_value=0.0),
                    },
                    key="ref_table",
                )
                reference_rows = edited.to_dict(orient="records")
        else:
            source_label = ref_entity = ref_period = ref_currency = ref_basis = ""
            control_total = None
            control_confirmed = False

    st.subheader("Gate 1 — Confirm context before parsing")
    context_summary = [
        ("Workbook", "Filename", uploaded_file.name if uploaded_file else "Not uploaded"),
        ("Workbook", "Description", description.strip() or "Not supplied"),
        ("Review", "Reviewer", reviewer_name.strip() or "Not supplied"),
        ("Review", "Role", role.upper() if role in {"cro", "cfo"} else role.title()),
        ("Workbook", "Entity", entity.strip() or "Not supplied"),
        ("Workbook", "Period", period.strip() or "Not supplied"),
        ("Workbook", "Currency", currency.strip().upper() or "Not supplied"),
        ("Workbook", "Basis", basis.strip() or "Not supplied"),
        ("Reference", "Included", "Yes" if include_reference else "No"),
    ]
    if include_reference:
        context_summary.extend(
            [
                ("Reference", "Source", source_label.strip() or "Not supplied"),
                ("Reference", "Entity", ref_entity.strip() or "Not supplied"),
                ("Reference", "Period", ref_period.strip() or "Not supplied"),
                ("Reference", "Currency", ref_currency.strip().upper() or "Not supplied"),
                ("Reference", "Basis", ref_basis.strip() or "Not supplied"),
                (
                    "Reference",
                    "Signed net control total",
                    "Not supplied" if control_total is None else f"{control_total:,.2f}",
                ),
            ]
        )
    st.table(pd.DataFrame(context_summary, columns=["Area", "Field", "Confirmed context"]))

    workbook_hash = _workbook_identity_panel(uploaded_file)

    gate1_confirmed = st.checkbox(
        "I confirm that the workbook and reference-figure context shown above is accurate.",
        value=False,
        key="gate1_context_confirmed",
    )

    if not st.button("Start audit", type="primary", disabled=not gate1_confirmed):
        return

    if uploaded_file is None:
        st.error("Please upload an .xlsx file.")
        return
    if not description.strip():
        st.error("Please describe what this file does.")
        return
    if not reviewer_name.strip():
        st.error("Please enter the reviewer’s full name for Gates 1–3.")
        return

    try:
        reference_figures = (
            build_reference_figures(
                source_label=source_label,
                entity=ref_entity,
                period=ref_period,
                currency=ref_currency,
                basis=ref_basis,
                control_total=control_total,
                control_total_confirmed_by_human=control_confirmed,
                rows=reference_rows or [],
                require_account_number=reference_from_csv,
            )
            if include_reference
            else None
        )
        file_context = FileContext(
            filename=uploaded_file.name,
            description=description.strip(),
            user_role=role,
            entity=entity.strip() or None,
            period=period.strip() or None,
            currency=currency.strip().upper() or None,
            basis=basis.strip() or None,
            confirmed_workbook_hash=workbook_hash,
            uploaded_at=datetime.now(timezone.utc),
        )
    except (ReferenceFigureInputError, ValueError) as exc:
        st.error(str(exc))
        return

    try:
        # No temporary file: the bytes confirmed above are the bytes parsed.
        # Writing them to disk first would reintroduce a mutable reference
        # between confirmation and parsing.
        with st.spinner("Parsing workbook and detecting findings…"):
            report_id, parsed_file, findings = _orchestrator().run(
                uploaded_file.getvalue(),
                file_context,
                reference_figures,
                expected_workbook_hash=workbook_hash,
                context_confirmed=gate1_confirmed,
                actor=reviewer_name.strip(),
            )
    except WorkbookIdentityError as exc:
        st.error(str(exc))
        st.session_state.gate1_context_confirmed = False
        st.caption(
            "Nothing was parsed and no Gate 1 decision was recorded. The blocked "
            "attempt is in the audit trail. Re-confirm the context for the "
            "workbook you intend to review."
        )
        return
    except GateBlockedError as exc:
        st.error(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - processing failures must be visible in the UI
        st.error(f"Could not process this file: {exc}")
        return

    st.session_state.report_id = report_id
    st.session_state.parsed_file = parsed_file
    st.session_state.findings = findings
    st.session_state.finding_decisions = {}
    st.session_state.authoritative_outputs = []
    st.session_state.reconciliation_result = None
    st.session_state.mapping_decisions = {}
    st.session_state.reference_figures = reference_figures
    st.session_state.reviewer_name = reviewer_name.strip()
    st.session_state.reviewer_role = role
    st.session_state.context_match_verdict = _orchestrator().get_context_match_verdict(report_id)
    st.session_state.final_report = None
    st.session_state.pdf_bytes = None
    st.session_state.pdf_generation_error = None
    st.session_state.report_preparation_error = None
    st.session_state.screen = 2
    st.rerun()


def screen_2_findings_review() -> None:
    st.header("Screen 2 — Gate 2: Findings review and output designation")
    findings = st.session_state.findings
    decisions = st.session_state.finding_decisions

    decided_count = sum(finding.finding_id in decisions for finding in findings)
    st.progress(decided_count / len(findings) if findings else 1.0)
    st.caption(f"{decided_count} of {len(findings)} findings reviewed")
    if not findings:
        st.info("No findings were raised for this file. Output designation is still required.")

    for finding in findings:
        with st.container(border=True):
            st.markdown(
                f"**{SEVERITY_BADGE[finding.severity]}** — "
                f"`{finding.tab}!{finding.cell_ref}`"
            )
            st.write(finding.description)
            current = decisions.get(finding.finding_id, {}).get("decision")
            confirm, override, dismiss = st.columns(3)
            if confirm.button(
                "Confirm",
                key=f"confirm_{finding.finding_id}",
                type="primary" if current == "confirmed" else "secondary",
            ):
                decisions[finding.finding_id] = {"decision": "confirmed", "reason": ""}
            if override.button(
                "Override",
                key=f"override_{finding.finding_id}",
                type="primary" if current == "overridden" else "secondary",
            ):
                decisions[finding.finding_id] = {
                    "decision": "overridden",
                    "reason": decisions.get(finding.finding_id, {}).get("reason", ""),
                }
            if dismiss.button(
                "Dismiss",
                key=f"dismiss_{finding.finding_id}",
                type="primary" if current == "dismissed" else "secondary",
            ):
                decisions[finding.finding_id] = {
                    "decision": "dismissed",
                    "reason": decisions.get(finding.finding_id, {}).get("reason", ""),
                }

            if decisions.get(finding.finding_id, {}).get("decision") in {
                "overridden",
                "dismissed",
            }:
                decisions[finding.finding_id]["reason"] = st.text_input(
                    "Reason (required)",
                    value=decisions[finding.finding_id].get("reason", ""),
                    key=f"reason_{finding.finding_id}",
                )

    st.subheader("Authoritative output designation")
    st.caption(
        "This is your call, not the tool’s — pick the exact cells whose figures need "
        "to be verified."
    )
    parsed_file: ParsedFile = st.session_state.parsed_file
    selectable: list[str] = []
    for tab in parsed_file.tab_names:
        rows = _numeric_cells_for_tab(parsed_file, tab)
        st.markdown(f"**{tab}**")
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            selectable.extend(row["cell_ref"] for row in rows)
        else:
            st.caption("No numeric-looking cells found on this tab.")

    selected_outputs = st.multiselect(
        "Select the authoritative output cell(s) to reconstruct and reconcile",
        options=selectable,
        default=[ref for ref in st.session_state.authoritative_outputs if ref in selectable],
        key="authoritative_output_selector",
    )
    st.session_state.authoritative_outputs = selected_outputs

    all_decided = all(finding.finding_id in decisions for finding in findings)
    reasons_complete = all(
        decisions[finding.finding_id]["decision"] == "confirmed"
        or bool(decisions[finding.finding_id].get("reason", "").strip())
        for finding in findings
        if finding.finding_id in decisions
    )
    ready = all_decided and reasons_complete and bool(selected_outputs)

    if not st.button("Submit all decisions", disabled=not ready, type="primary"):
        return

    decided_findings = [
        finding.model_copy(
            update={
                "human_decision": decisions[finding.finding_id]["decision"],
                "human_reason": decisions[finding.finding_id].get("reason") or None,
                "decided_by": st.session_state.reviewer_name,
                "decided_at": datetime.now(timezone.utc),
            }
        )
        for finding in findings
    ]
    try:
        with st.spinner("Reconstructing the selected outputs…"):
            _, result = _orchestrator().submit_gate2_decisions(
                st.session_state.report_id,
                decided_findings,
                selected_outputs,
                actor=st.session_state.reviewer_name,
            )
    except ChainIntegrityError as exc:
        _chain_integrity_error(exc)
        return
    except (GateBlockedError, PipelineStateError) as exc:
        st.error(str(exc))
        return

    defaults = _orchestrator().get_materiality_defaults(st.session_state.report_id)
    st.session_state.findings = decided_findings
    st.session_state.reconciliation_result = result
    st.session_state.materiality_defaults = defaults
    st.session_state.internal_pct_percent = defaults["default_pct_threshold"] * 100
    st.session_state.internal_absolute_threshold = defaults["default_absolute_threshold"]
    st.session_state.external_pct_percent = defaults["default_pct_threshold"] * 100
    st.session_state.external_absolute_threshold = defaults["default_absolute_threshold"]
    st.session_state.internal_threshold_reason = ""
    st.session_state.external_threshold_reason = ""
    st.session_state.acknowledge_incomplete = False
    st.session_state.mapping_decisions = {}
    st.session_state.screen = 3
    st.rerun()


def screen_3_reconciliation() -> None:
    st.header("Screen 3 — Gate 3: Reconciliation and mapping approval")
    context_verdict = st.session_state.context_match_verdict
    if context_verdict == "mismatch":
        st.error(
            "Entity, period, or currency does not match — accounts reconciliation "
            "cannot be relied upon."
        )

    if (
        not st.session_state.report_preparation_error
        and _orchestrator().get_stage(st.session_state.report_id) == "post_gate3"
    ):
        st.session_state.report_preparation_error = (
            "Gate 3 is complete, but downstream report preparation has not finished. "
            "Retry it without replaying the recorded Gate 3 decision."
        )
    if st.session_state.report_preparation_error:
        st.error(st.session_state.report_preparation_error)
        if st.button("Retry report preparation", type="primary"):
            try:
                with st.spinner("Retrying documentation and report preparation…"):
                    report = _orchestrator().prepare_report(
                        st.session_state.report_id,
                        actor=st.session_state.reviewer_name,
                    )
            except Exception as exc:  # noqa: BLE001 - provider failures stay retryable
                st.session_state.report_preparation_error = _preparation_error_message(exc)
                st.rerun()
            st.session_state.report_preview = report
            st.session_state.internal_verdict = report.internal_verdict
            st.session_state.external_verdict = report.external_verdict
            st.session_state.report_preparation_error = None
            st.session_state.approval_name = ""
            st.session_state.approval_role = ""
            st.session_state.screen = 4
            st.rerun()
        return

    result: ReconciliationResult = st.session_state.reconciliation_result
    defaults = st.session_state.materiality_defaults
    default_pct_percent = defaults["default_pct_threshold"] * 100
    default_absolute = defaults["default_absolute_threshold"]

    st.subheader("Internal consistency (Excel vs Python)")
    internal_pct_percent, internal_absolute = _threshold_inputs(
        prefix="internal",
        default_pct_percent=default_pct_percent,
        default_absolute=default_absolute,
    )
    internal_changed = (
        internal_pct_percent != default_pct_percent or internal_absolute != default_absolute
    )
    internal_reason = ""
    if internal_changed:
        internal_reason = st.text_input(
            "Reason for changing from the default threshold (required)",
            key="internal_threshold_reason",
        )

    has_reference_figures = st.session_state.reference_figures is not None
    external_pct_percent = float(
        st.session_state.get("external_pct_percent", default_pct_percent)
    )
    external_absolute = float(
        st.session_state.get("external_absolute_threshold", default_absolute)
    )
    _sync_mapping_edit_selections(result)
    mapping_decisions = _mapping_decision_models(result)
    try:
        internal_verdict, external_verdict, preview = _orchestrator().preview_gate3_decisions(
            st.session_state.report_id,
            result,
            mapping_decisions,
            internal_pct_threshold=internal_pct_percent / 100,
            internal_absolute_threshold=internal_absolute,
            external_pct_threshold=external_pct_percent / 100,
            external_absolute_threshold=external_absolute,
            actor=st.session_state.reviewer_name,
        )
    except ChainIntegrityError as exc:
        _chain_integrity_error(exc)
        return
    except (GateBlockedError, PipelineStateError, ValueError) as exc:
        st.error(str(exc))
        return

    internal_lines = [
        line for line in preview.lines if line.check_type == "excel_vs_python"
    ]
    _render_reconciliation_table(internal_lines, "Internal comparison")
    _render_incomplete_details(internal_lines)

    if has_reference_figures:
        st.subheader("Accounts reconciliation (CFO) — mapping approval")
        if context_verdict == "mismatch":
            st.caption(
                "External controls remain visible for review, but the context mismatch "
                "forces a blocking external result."
            )
        external_pct_percent, external_absolute = _threshold_inputs(
            prefix="external",
            default_pct_percent=default_pct_percent,
            default_absolute=default_absolute,
        )
        external_changed = (
            external_pct_percent != default_pct_percent
            or external_absolute != default_absolute
        )
        external_reason = ""
        if external_changed:
            external_reason = st.text_input(
                "Reason for changing the accounts default threshold (required)",
                key="external_threshold_reason",
            )
        _render_mapping_cards(result)
        mapping_decisions = _mapping_decision_models(result)
        try:
            internal_verdict, external_verdict, preview = (
                _orchestrator().preview_gate3_decisions(
                    st.session_state.report_id,
                    result,
                    mapping_decisions,
                    internal_pct_threshold=internal_pct_percent / 100,
                    internal_absolute_threshold=internal_absolute,
                    external_pct_threshold=external_pct_percent / 100,
                    external_absolute_threshold=external_absolute,
                    actor=st.session_state.reviewer_name,
                )
            )
        except ChainIntegrityError as exc:
            _chain_integrity_error(exc)
            return
        except (GateBlockedError, PipelineStateError, ValueError) as exc:
            st.error(str(exc))
            return
        external_lines = [
            line for line in preview.lines if line.check_type == "python_vs_accounts"
        ]
        _render_reconciliation_table(external_lines, "Accounts comparison preview")
        _render_incomplete_details(external_lines)
        _render_bidirectional_gaps(preview)
    else:
        st.info("No reference figures provided — accounts reconciliation skipped")
        external_pct_percent = default_pct_percent
        external_absolute = default_absolute
        external_changed = False
        external_reason = ""

    unreviewed = _unreviewed_mapping_ids(result)
    internal_reason_missing = internal_changed and not internal_reason.strip()
    external_reason_missing = external_changed and not external_reason.strip()
    has_blocker = "block" in {internal_verdict, external_verdict} or any(
        line.verdict == "block" for line in preview.lines
    )
    is_incomplete = "incomplete" in {internal_verdict, external_verdict}

    if has_blocker:
        st.error("Must be resolved before proceeding")
    if unreviewed:
        st.warning("Mapping review still required: " + ", ".join(unreviewed))
    if internal_reason_missing or external_reason_missing:
        st.warning("Every threshold change requires a reason before Gate 3 can proceed.")

    acknowledge_incomplete = False
    if is_incomplete and not has_blocker:
        acknowledge_incomplete = st.checkbox(
            "I acknowledge that the reconciliation result is incomplete",
            key="acknowledge_incomplete",
        )

    disabled = (
        has_blocker
        or bool(unreviewed)
        or internal_reason_missing
        or external_reason_missing
        or (is_incomplete and not acknowledge_incomplete)
    )
    if not st.button("Confirm reconciliation", disabled=disabled, type="primary"):
        return

    try:
        with st.spinner("Finalizing reconciliation and preparing report evidence…"):
            internal_verdict, external_verdict, final_result = (
                _orchestrator().submit_gate3_decisions(
                    st.session_state.report_id,
                    result,
                    mapping_decisions=mapping_decisions,
                    internal_pct_threshold=internal_pct_percent / 100,
                    internal_absolute_threshold=internal_absolute,
                    external_pct_threshold=external_pct_percent / 100,
                    external_absolute_threshold=external_absolute,
                    internal_threshold_deviation_reason=internal_reason or None,
                    external_threshold_deviation_reason=external_reason or None,
                    actor=st.session_state.reviewer_name,
                    acknowledge_incomplete=acknowledge_incomplete,
                )
            )
    except ChainIntegrityError as exc:
        _chain_integrity_error(exc)
        return
    except (GateBlockedError, PipelineStateError, ValueError) as exc:
        st.error(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - Gate 3 is durable; downstream work is retryable
        st.session_state.report_preparation_error = _preparation_error_message(exc)
        st.error(st.session_state.report_preparation_error)
        return

    st.session_state.internal_verdict = internal_verdict
    st.session_state.external_verdict = external_verdict
    st.session_state.final_result = final_result
    st.session_state.report_preview = _orchestrator().get_report(st.session_state.report_id)
    st.session_state.report_preparation_error = None
    st.session_state.approval_name = ""
    st.session_state.approval_role = ""
    st.session_state.screen = 4
    st.rerun()


def screen_4_approval_record() -> None:
    st.header("Screen 4 — Gate 4: Named approval record")
    if st.session_state.final_report is not None and st.session_state.pdf_bytes is None:
        st.error(
            st.session_state.pdf_generation_error
            or "The named approval record is complete, but PDF generation has not finished."
        )
        if st.button("Retry PDF generation", type="primary"):
            try:
                audit_rows = _orchestrator().get_audit_rows(st.session_state.report_id)
                pdf_bytes = generate_report_pdf(st.session_state.final_report, audit_rows)
            except Exception as exc:  # noqa: BLE001 - approval must not be replayed
                st.session_state.pdf_generation_error = _pdf_error_message(exc)
                st.rerun()
            st.session_state.audit_rows = audit_rows
            st.session_state.pdf_bytes = pdf_bytes
            st.session_state.pdf_generation_error = None
            st.session_state.screen = 5
            st.rerun()
        return

    report = st.session_state.report_preview
    findings = st.session_state.findings
    blockers = [finding for finding in findings if finding.severity == "blocker"]
    resolved = sum(
        finding.human_decision in {"overridden", "dismissed"} for finding in blockers
    )

    summary1, summary2 = st.columns(2)
    summary1.metric("Total findings", len(findings))
    summary2.metric("Blockers resolved", f"{resolved} of {len(blockers)}")
    _verdict_banner(
        report.translation_and_reconciliation_verdict,
        "Overall translation and reconciliation verdict",
    )

    st.subheader("Data sent to third-party AI service")
    if report.llm_data_manifest:
        st.dataframe(_manifest_frame(report), hide_index=True, width="stretch")
    else:
        st.info("No Anthropic API calls were recorded for this report.")

    approval_name = st.text_input("Your full name", key="approval_name")
    approval_role = st.text_input("Your role at the organisation", key="approval_role")
    if approval_name.strip():
        if not _orchestrator().is_approver_registered(approval_name):
            st.warning(
                "This name isn’t in the registered approver list — you can still proceed, "
                "but this will be flagged in the report evidence."
            )
        disclosure = _orchestrator().preview_independence_disclosure(
            st.session_state.report_id, approval_name.strip()
        )
        st.info(disclosure)
    else:
        st.caption("Enter a name to preview the report’s independence disclosure.")

    st.markdown(
        "**This confirms your typed identity and the timestamp. It is not a "
        "cryptographic or legal signature.**"
    )
    ready = bool(approval_name.strip()) and bool(approval_role.strip())
    if not st.button(
        "Record approval and generate report", disabled=not ready, type="primary"
    ):
        return

    try:
        with st.spinner("Recording the named approval record…"):
            final_report = _orchestrator().submit_approval_record(
                st.session_state.report_id, approval_name.strip(), approval_role.strip()
            )
    except ChainIntegrityError as exc:
        _chain_integrity_error(exc)
        return
    except (GateBlockedError, PipelineStateError, ValueError) as exc:
        st.error(str(exc))
        return

    st.session_state.final_report = final_report
    try:
        with st.spinner("Generating the PDF…"):
            audit_rows = _orchestrator().get_audit_rows(st.session_state.report_id)
            pdf_bytes = generate_report_pdf(final_report, audit_rows)
    except Exception as exc:  # noqa: BLE001 - Gate 4 is durable; only PDF work is retried
        st.session_state.pdf_generation_error = _pdf_error_message(exc)
        st.error(st.session_state.pdf_generation_error)
        return

    st.session_state.audit_rows = audit_rows
    st.session_state.pdf_bytes = pdf_bytes
    st.session_state.pdf_generation_error = None
    st.session_state.integrity_result = None
    st.session_state.screen = 5
    st.rerun()


def screen_5_report() -> None:
    st.header("Screen 5 — Report")
    report = st.session_state.final_report
    if (
        report is None
        or report.report_approval_name is None
        or report.report_approval_at is None
        or st.session_state.pdf_bytes is None
    ):
        st.error("The PDF remains unavailable until Gate 4 completes.")
        return

    st.download_button(
        "Download PDF",
        data=st.session_state.pdf_bytes,
        file_name=f"translation_reconciliation_{report.report_id}.pdf",
        mime="application/pdf",
    )
    _verdict_banner(
        report.translation_and_reconciliation_verdict,
        "Overall translation and reconciliation verdict",
    )
    internal_col, external_col = st.columns(2)
    with internal_col:
        _verdict_banner(report.internal_verdict, "Internal consistency")
    with external_col:
        external = (
            "not_performed" if report.reference_figures is None else report.external_verdict
        )
        _verdict_banner(external, "Accounts reconciliation (CFO)")

    st.subheader("Traceability index (preview)")
    if report.traceability_index:
        st.dataframe(_traceability_frame(report), hide_index=True, width="stretch")
        st.caption("full index in the PDF")
    else:
        st.info("No traceability entries were produced for this file.")

    st.subheader("Audit trail")
    if st.session_state.audit_rows:
        st.dataframe(
            _audit_frame(st.session_state.audit_rows), hide_index=True, width="stretch"
        )
    else:
        st.info("No audit trail entries were recorded for this report.")

    if st.button("Verify evidence integrity"):
        st.session_state.integrity_result = _orchestrator().verify_evidence_integrity()
    if st.session_state.integrity_result is not None:
        valid, errors = st.session_state.integrity_result
        if valid:
            st.success("Pass — the local tamper-evident hash chain verifies.")
        else:
            st.error("Fail — evidence integrity check failed: " + "; ".join(errors))


def _numeric_cells_for_tab(parsed_file: ParsedFile, tab: str) -> list[dict]:
    prefix = f"{tab}!"
    rows = []
    for ref, cell in parsed_file.cells.items():
        if not ref.startswith(prefix):
            continue
        numeric_value = isinstance(cell.cached_value, (int, float)) and not isinstance(
            cell.cached_value, bool
        )
        if not numeric_value and cell.data_type != "number" and cell.formula is None:
            continue
        rows.append(
            {
                "cell_ref": ref,
                "current value": cell.cached_value,
                "has_formula": cell.formula is not None,
            }
        )
    return sorted(rows, key=lambda row: row["cell_ref"])


def _threshold_inputs(
    *, prefix: str, default_pct_percent: float, default_absolute: float
) -> tuple[float, float]:
    percentage_key = f"{prefix}_pct_percent"
    absolute_key = f"{prefix}_absolute_threshold"
    st.session_state.setdefault(percentage_key, default_pct_percent)
    st.session_state.setdefault(absolute_key, default_absolute)
    left, right = st.columns(2)
    percentage = left.number_input(
        "Percentage threshold (%)",
        min_value=0.0,
        step=0.1,
        key=percentage_key,
    )
    absolute = right.number_input(
        "Absolute threshold (currency)",
        min_value=0.0,
        step=10.0,
        key=absolute_key,
    )
    return float(percentage), float(absolute)


def _record_mapping_action(mapping_id: str, action: str) -> None:
    st.session_state.mapping_decisions[mapping_id] = {"action": action}


def _render_mapping_cards(result: ReconciliationResult) -> None:
    st.markdown("#### Proposed mappings")
    references = {
        line.line_id: line for line in st.session_state.reference_figures.lines
    }
    internal_values = {
        ref: line.target_value
        for ref, line in zip(
            st.session_state.authoritative_outputs,
            [line for line in result.lines if line.check_type == "excel_vs_python"],
        )
    }
    for mapping in result.mappings:
        reference = references.get(mapping.reference_line_id)
        current = st.session_state.mapping_decisions.get(mapping.mapping_id, {})
        with st.container(border=True):
            st.markdown(f"**{mapping.mapping_id} — {mapping.mapping_type.replace('_', ' ')}**")
            st.write(
                {
                    "Python output": mapping.python_output_cell_ref,
                    "Python value": internal_values.get(mapping.python_output_cell_ref),
                    "Reference label": reference.label if reference else "Not found",
                    "Account number": reference.account_number if reference else None,
                    "Reference value": reference.amount if reference else None,
                    "Suggested confidence": mapping.suggested_confidence,
                    "Suggested by": mapping.suggested_by,
                }
            )
            if mapping.approval_note:
                st.warning(mapping.approval_note)
            if mapping.mapping_type != "one_to_one":
                st.info(
                    "This mapping requires manual reconciliation; no aggregate was computed."
                )
                st.button(
                    "Acknowledge — requires manual reconciliation",
                    key=f"manual_{mapping.mapping_id}",
                    type=(
                        "primary"
                        if current.get("action") == "acknowledge_manual"
                        else "secondary"
                    ),
                    on_click=_record_mapping_action,
                    args=(mapping.mapping_id, "acknowledge_manual"),
                )
                continue

            approve, reject, edit = st.columns(3)
            approve.button(
                "Approve",
                key=f"approve_{mapping.mapping_id}",
                type="primary" if current.get("action") == "approve" else "secondary",
                on_click=_record_mapping_action,
                args=(mapping.mapping_id, "approve"),
            )
            reject.button(
                "Reject",
                key=f"reject_{mapping.mapping_id}",
                type="primary" if current.get("action") == "reject" else "secondary",
                on_click=_record_mapping_action,
                args=(mapping.mapping_id, "reject"),
            )
            edit.button(
                "Edit mapping",
                key=f"edit_{mapping.mapping_id}",
                type="primary" if current.get("action") == "edit" else "secondary",
                on_click=_record_mapping_action,
                args=(mapping.mapping_id, "edit"),
            )
            if current.get("action") == "edit":
                options = [
                    line.line_id
                    for line in st.session_state.reference_figures.lines
                    if line.line_id != mapping.reference_line_id
                ]
                if not options:
                    st.error("No different reference line is available for this edit.")
                else:
                    replacement = st.selectbox(
                        "Choose the replacement reference line",
                        options,
                        format_func=lambda line_id: _reference_option_label(line_id, references),
                        key=f"replacement_{mapping.mapping_id}",
                    )
                    st.session_state.mapping_decisions[mapping.mapping_id][
                        "replacement_reference_line_id"
                    ] = replacement


def _reference_option_label(line_id: str, references: dict) -> str:
    line = references[line_id]
    return f"{line.label} — {line.account_number or 'no account'} — {line.amount:,.2f}"


def _mapping_decision_models(
    result: ReconciliationResult,
) -> list[MappingReviewDecision]:
    decisions = []
    known = {mapping.mapping_id for mapping in result.mappings}
    for mapping_id, raw in st.session_state.mapping_decisions.items():
        if mapping_id not in known or not raw.get("action"):
            continue
        if raw["action"] == "edit" and not raw.get("replacement_reference_line_id"):
            continue
        decisions.append(
            MappingReviewDecision(
                mapping_id=mapping_id,
                action=raw["action"],
                replacement_reference_line_id=raw.get("replacement_reference_line_id"),
            )
        )
    return decisions


def _sync_mapping_edit_selections(result: ReconciliationResult) -> None:
    for mapping in result.mappings:
        decision = st.session_state.mapping_decisions.get(mapping.mapping_id)
        widget_key = f"replacement_{mapping.mapping_id}"
        if decision and decision.get("action") == "edit" and widget_key in st.session_state:
            decision["replacement_reference_line_id"] = st.session_state[widget_key]


def _unreviewed_mapping_ids(result: ReconciliationResult) -> list[str]:
    reviewed = {decision.mapping_id for decision in _mapping_decision_models(result)}
    return [mapping.mapping_id for mapping in result.mappings if mapping.mapping_id not in reviewed]


def _render_reconciliation_table(lines: list, heading: str) -> None:
    st.markdown(f"#### {heading}")
    if not lines:
        st.info("No comparison rows are currently included in this pass.")
        return
    frame = pd.DataFrame(
        [
            {
                "Label": line.label,
                "Source value": line.source_value,
                "Target value": line.target_value,
                "Delta": line.delta,
                "Delta %": (
                    f"{line.delta_pct * 100:.2f}%" if line.delta_pct is not None else "not comparable"
                ),
                "Verdict": line.verdict.upper(),
                "Coverage": f"{line.reconstruction_coverage_pct:.1f}%",
            }
            for line in lines
        ]
    )
    st.dataframe(
        frame.style.map(_verdict_cell_style, subset=["Verdict"]),
        hide_index=True,
        width="stretch",
    )


def _verdict_cell_style(value: object) -> str:
    verdict = str(value).lower()
    if verdict not in VERDICT_BG:
        return ""
    return f"background-color: {VERDICT_BG[verdict]}; color: {VERDICT_COLOR[verdict]};"


def _render_incomplete_details(lines: list) -> None:
    for line in lines:
        if line.verdict != "incomplete":
            continue
        with st.expander(
            f"{line.label}: {line.reconstruction_coverage_pct:.1f}% reconstructed"
        ):
            if line.unsupported_elements:
                for element in line.unsupported_elements:
                    st.write(f"- {element}")
            else:
                st.write("No unsupported-element detail was recorded.")


def _render_bidirectional_gaps(result: ReconciliationResult) -> None:
    st.markdown("#### Bidirectional completeness")
    left, right = st.columns(2)
    with left:
        st.markdown("**Reference figures with no Python counterpart**")
        if result.unmatched_reference_items:
            for item in result.unmatched_reference_items:
                st.write(f"- {item}")
        else:
            st.write("None — every reference figure was mapped")
    with right:
        st.markdown("**Designated outputs with no accounting counterpart**")
        if result.unmapped_python_outputs:
            for item in result.unmapped_python_outputs:
                st.write(f"- {item}")
        else:
            st.write("None — every designated output was mapped")


def _manifest_frame(report) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Tab": entry.tab_name,
                "Cells included": ", ".join(entry.cell_refs_included) or "None",
                "Cells excluded": ", ".join(entry.cell_refs_excluded) or "None",
                "Exclusion reasons": "; ".join(
                    f"{ref}: {reason}" for ref, reason in entry.exclusion_reasons.items()
                )
                or "None",
            }
            for entry in report.llm_data_manifest
        ]
    )


def _traceability_frame(report) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Figure": entry.report_figure_label,
                "Value": entry.report_value,
                "Trace status": entry.trace_status,
                "First source cell": entry.derivation[0].cell_ref if entry.derivation else "Not traceable",
                "Account": (
                    entry.accounting_provenance.account_number
                    if entry.accounting_provenance
                    else "Not mapped"
                ),
            }
            for entry in report.traceability_index[:10]
        ]
    )


def _audit_frame(rows: list[dict]) -> pd.DataFrame:
    prepared = []
    for row in rows:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
        prepared.append(
            {
                "Row": row.get("id") or row.get("row_id"),
                "Event": row.get("event_type"),
                "Gate": payload.get("gate", "—"),
                "Action": payload.get("action", "—"),
                "Actor": row.get("actor") or "—",
                "Timestamp": row.get("timestamp"),
                "Acknowledge incomplete": payload.get("acknowledge_incomplete", "—"),
                "Row hash": (row.get("row_hash") or "")[:12],
            }
        )
    return pd.DataFrame(prepared)


def _preparation_error_message(exc: Exception) -> str:
    return (
        "Gate 3 was recorded, but downstream report preparation paused because "
        f"{type(exc).__name__} occurred. No PDF was generated. Resolve the service "
        "issue, then use ‘Retry report preparation’; Gate 3 will not be replayed."
    )


def _pdf_error_message(exc: Exception) -> str:
    return (
        "The named approval record was completed, but PDF generation paused because "
        f"{type(exc).__name__} occurred. Retry PDF generation; the approval record "
        "will not be replayed."
    )


def main() -> None:
    st.set_page_config(page_title="Excel Audit Agent", layout="wide")
    _load_environment()
    _init_state()
    screens = {
        1: screen_1_upload,
        2: screen_2_findings_review,
        3: screen_3_reconciliation,
        4: screen_4_approval_record,
        5: screen_5_report,
    }
    screens.get(st.session_state.screen, screen_1_upload)()


if __name__ == "__main__":
    main()
