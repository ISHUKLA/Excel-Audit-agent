"""Coordinate agents and human gates without containing audit business logic.

The pipeline is deliberately staged. ``run`` stops before Gate 2,
``submit_gate2_decisions`` stops before Gate 3, and
``submit_gate3_decisions`` stops before the named approval record. Durable
snapshots, not this process-local dictionary, are the recovery source of truth.
"""

import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

import anthropic
from pydantic import BaseModel

from agents.anomaly_detector import detect_anomalies
from agents.documentation import FALLBACK_METHOD_SUMMARY, document_tabs
from agents.parser import parse_workbook
from agents.reconciliation import calculate_delta, run_reconciliation
from core.accounting import evaluate_control_total, signed_reference_amount
from core.audit_log import AuditLog, NOT_YET_PARSED
from core.workbook_identity import (
    WorkbookIdentityError,
    sha256_bytes,
    validate_hash_format,
    verify_bytes_match,
)
from core.gates import (
    GateBlockedError,
    approval_record_gate,
    context_gate,
    findings_review_gate,
    independence_disclosure_preview,
    preview_reconciliation,
    reconciliation_gate,
)
from core.models import (
    AccountMapping,
    AnomalyFinding,
    AuditReport,
    ControlTotalCheck,
    FileContext,
    MappingReviewDecision,
    ParsedFile,
    ReconciliationLine,
    ReconciliationResult,
    ReferenceFigureLine,
    ReferenceFigures,
)
from core.state_store import StateStore
from core.traceability import build_traceability_index

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MATERIALITY_PATH = _PROJECT_ROOT / "config" / "materiality_defaults.json"
_DEFAULT_APPROVERS_PATH = _PROJECT_ROOT / "config" / "authorized_approvers.json"
_DEFAULT_AUDIT_DB_PATH = "audit.db"

RECONCILIATION_PREVIEW_LABEL = "Preview, pending Gate 3 approval"

_DISCLAIMER = (
    "This Translation & Reconciliation Report describes a Python reconstruction "
    "of supported workbook calculations and any reconciliation performed. It does "
    "not constitute actuarial validation of the underlying model, methodology, "
    "assumptions, or professional judgments."
)
_PENDING_APPROVAL_DISCLOSURE = (
    "The named approval record has not yet been completed. No independent review "
    "is implied."
)

# Identifies which version of the AI transmission disclosure a reviewer was
# shown when they made the use/decline choice. Bump this whenever the
# disclosure text changes so historic audit rows stay tied to what a reviewer
# actually saw rather than the current wording.
AI_DISCLOSURE_VERSION = "ai-disclosure-2026-08-27-v1"


class PipelineStateError(RuntimeError):
    """Raised when a staged method is called out of sequence or cannot recover."""


class Orchestrator:
    """Sequence the four agents and four human gates for one or more reports."""

    def __init__(
        self,
        audit_log: Optional[AuditLog] = None,
        state_store: Optional[StateStore] = None,
        documentation_client: Optional[anthropic.Anthropic] = None,
        materiality_config_path: Union[str, Path] = _DEFAULT_MATERIALITY_PATH,
        authorized_approvers_path: Union[str, Path] = _DEFAULT_APPROVERS_PATH,
        code_version: Optional[str] = None,
    ):
        if state_store is not None and audit_log is not None:
            if Path(state_store.db_path).resolve() != Path(audit_log.db_path).resolve():
                raise ValueError("audit_log and state_store must use the same database file")

        self._audit_log = audit_log or (
            state_store.audit_log
            if state_store
            else AuditLog(_audit_db_path_from_environment())
        )
        self._state_store = state_store or StateStore(
            self._audit_log.db_path,
            audit_log=self._audit_log,
        )
        self._documentation_client = documentation_client
        self._materiality_config_path = Path(materiality_config_path)
        self._authorized_approvers_path = Path(authorized_approvers_path)
        self._code_version_override = code_version
        self._state: dict[str, dict] = {}
        # Reports whose chain verification has already been recorded by THIS
        # process. Recovery is reached implicitly through _state_for(), so
        # without this a single restarted session would append a verification
        # row on every staged call and fill the log with evidence about itself.
        self._chain_verification_recorded: set[str] = set()

    def run(
        self,
        workbook_bytes: bytes,
        file_context: FileContext,
        reference_figures: Optional[ReferenceFigures] = None,
        *,
        expected_workbook_hash: str,
        context_confirmed: bool,
        actor: str,
    ) -> tuple[str, ParsedFile, list[AnomalyFinding]]:
        """Start a run and pause with the evidence needed for Gate 2.

        Takes the workbook's BYTES and the hash a human confirmed at Gate 1, not
        a path. A path is a mutable reference: it cannot carry the guarantee that
        the workbook confirmed is the workbook parsed.

        ``expected_workbook_hash`` is keyword-only with NO default, so a caller
        who omits it gets a TypeError rather than an unverified run. There is no
        flag to skip verification; adding one would make the control advisory.

        Returning an AuditReport here would bypass three mandatory human pauses.
        The report exists only after ``submit_gate3_decisions`` and is returned
        as final only by ``submit_approval_record``.
        """
        _require_actor(actor)
        report_id = str(uuid.uuid4())
        code_version = self._code_version_override or _detect_code_version()
        defaults = _load_materiality_defaults(self._materiality_config_path)

        # Identity is established BEFORE Gate 1 runs. If this raises, context_gate
        # is never called, so no context_confirmed decision is ever recorded for a
        # workbook whose identity was never established. Ordering is the control.
        workbook_hash = self._verify_workbook_identity(
            workbook_bytes=workbook_bytes,
            expected_workbook_hash=expected_workbook_hash,
            file_context=file_context,
            report_id=report_id,
            actor=actor,
            code_version=code_version,
        )

        # Gate 1 now records the real workbook hash. NOT_YET_PARSED was honest
        # when nothing had been hashed yet; it is no longer true, because the
        # bytes are hashed before the gate rather than during parsing.
        gate_context = {
            "workbook_hash": workbook_hash,
            "code_version": code_version,
        }
        _, context_match_verdict = context_gate(
            file_context=file_context,
            reference_figures=reference_figures,
            confirmed=context_confirmed,
            report_id=report_id,
            actor=actor,
            audit_log=self._audit_log,
            context=gate_context,
        )
        control_total_check = evaluate_control_total(reference_figures)

        parsed_file = parse_workbook(workbook_bytes)

        # Defence in depth. The parser hashes the same bytes with the same helper,
        # so this cannot fail without a defect somewhere between them. If it ever
        # does, the run stops rather than silently re-basing every later event on
        # an identity nobody confirmed.
        if parsed_file.workbook_meta.workbook_hash != workbook_hash:
            raise WorkbookIdentityError(
                "internal consistency failure: the parsed workbook hash "
                f"({parsed_file.workbook_meta.workbook_hash}) does not match the "
                f"confirmed hash ({workbook_hash}). Nothing further has been recorded."
            )

        context = {
            "workbook_hash": workbook_hash,
            "code_version": code_version,
        }
        state = {
            "report_id": report_id,
            "validation_run_id": str(uuid.uuid4()),
            "file_context": file_context,
            "reference_figures": reference_figures,
            "parsed_file": parsed_file,
            "findings": [],
            "authoritative_outputs": [],
            "context_match_verdict": context_match_verdict,
            "control_total_check": control_total_check,
            "context": context,
            "materiality_defaults": defaults,
            "stage": "post_parse",
        }
        self._state[report_id] = state
        self._snapshot(report_id, "post_parse", actor)

        findings = detect_anomalies(parsed_file)
        state["findings"] = findings
        state["stage"] = "post_anomaly_detection"
        self._snapshot(report_id, "post_anomaly_detection", actor)

        return report_id, parsed_file, findings


    def _verify_workbook_identity(
        self,
        *,
        workbook_bytes: bytes,
        expected_workbook_hash: str,
        file_context: FileContext,
        report_id: str,
        actor: str,
        code_version: str,
    ) -> str:
        """Confirm these bytes are the workbook the human confirmed at Gate 1.

        Returns the verified hash. On mismatch, records the attempt and raises —
        no gate decision, no parse, no snapshot, no state.

        The mismatch row's context carries the CONFIRMED hash, not the observed
        one. Writing the observed hash there would assert an identity for a
        workbook nobody approved; the observed value belongs in the payload as
        evidence of what was rejected.
        """
        try:
            return verify_bytes_match(workbook_bytes, expected_workbook_hash)
        except WorkbookIdentityError as mismatch:
            # The expected hash may itself be malformed (that is one way to get
            # here). Only a well-formed digest is safe to record as context; a
            # junk value falls back to the sentinel rather than being written in
            # as though it were an identity.
            try:
                confirmed = validate_hash_format(expected_workbook_hash)
            except WorkbookIdentityError:
                confirmed = NOT_YET_PARSED
            try:
                observed = sha256_bytes(workbook_bytes)
            except WorkbookIdentityError:
                observed = "unreadable"
            self._audit_log.log_event(
                report_id=report_id,
                event_type="workbook_identity_mismatch",
                payload={
                    "confirmed_workbook_hash": confirmed,
                    "observed_workbook_hash": observed,
                    "filename": file_context.filename,
                    "outcome": "blocked",
                },
                actor=actor,
                context={"workbook_hash": confirmed, "code_version": code_version},
            )
            raise mismatch

    def submit_gate2_decisions(
        self,
        report_id: str,
        findings: list[AnomalyFinding],
        authoritative_outputs: list[str],
        *,
        actor: str,
    ) -> tuple[str, ReconciliationResult]:
        """Record Gate 2 decisions, run Agent 3, and return a labelled preview."""
        _require_actor(actor)
        state = self._state_for(report_id)
        _require_stage(state, {"post_anomaly_detection", "post_gate2"})

        reviewed_findings, outputs = findings_review_gate(
            findings=findings,
            parsed_file=state["parsed_file"],
            authoritative_outputs=authoritative_outputs,
            report_id=report_id,
            actor=actor,
            audit_log=self._audit_log,
            context=state["context"],
        )
        state["findings"] = reviewed_findings
        state["authoritative_outputs"] = outputs
        state["stage"] = "post_gate2"
        self._snapshot(report_id, "post_gate2", actor)

        defaults = state["materiality_defaults"]
        result = run_reconciliation(
            parsed_file=state["parsed_file"],
            authoritative_outputs=outputs,
            reference_figures=state["reference_figures"],
            pct_threshold=defaults["default_pct_threshold"],
            absolute_threshold=defaults["default_absolute_threshold"],
            default_pct_threshold=defaults["default_pct_threshold"],
            default_absolute_threshold=defaults["default_absolute_threshold"],
            warnings=state["parsed_file"].warnings,
        )
        if result.verdicts_are_final:
            raise PipelineStateError(
                "Agent 3 returned verdicts_are_final=True; only Gate 3 may finalize verdicts"
            )

        state["reconciliation_result"] = result
        state["reconciliation_label"] = RECONCILIATION_PREVIEW_LABEL
        state["stage"] = "post_reconciliation"
        self._snapshot(report_id, "post_reconciliation", actor)
        return RECONCILIATION_PREVIEW_LABEL, result

    def preview_gate3_decisions(
        self,
        report_id: str,
        result: ReconciliationResult,
        mapping_decisions: list[MappingReviewDecision],
        internal_pct_threshold: float,
        internal_absolute_threshold: float,
        external_pct_threshold: float,
        external_absolute_threshold: float,
        *,
        actor: str,
    ) -> tuple[str, str, ReconciliationResult]:
        """Return a non-evidentiary live preview for the Gate 3 screen."""
        _require_actor(actor)
        state = self._state_for(report_id)
        _require_stage(state, {"post_reconciliation"})
        if result.verdicts_are_final:
            raise PipelineStateError("Gate 3 requires an Agent 3 preview, not an already-final result")

        working_result = self._apply_mapping_review(
            report_id=report_id,
            result=result,
            decisions=mapping_decisions,
            actor=actor,
            context=state["context"],
            require_complete=False,
            log_events=False,
        )
        defaults = state["materiality_defaults"]
        return preview_reconciliation(
            working_result,
            internal_pct_threshold=internal_pct_threshold,
            internal_absolute_threshold=internal_absolute_threshold,
            external_pct_threshold=external_pct_threshold,
            external_absolute_threshold=external_absolute_threshold,
            default_pct_threshold=defaults["default_pct_threshold"],
            default_absolute_threshold=defaults["default_absolute_threshold"],
            context_match_verdict=state["context_match_verdict"],
            control_total_verdict=state["control_total_check"].status,
        )

    def submit_gate3_decisions(
        self,
        report_id: str,
        result: ReconciliationResult,
        mapping_decisions: list[MappingReviewDecision],
        internal_pct_threshold: float,
        internal_absolute_threshold: float,
        external_pct_threshold: float,
        external_absolute_threshold: float,
        internal_threshold_deviation_reason: Optional[str],
        external_threshold_deviation_reason: Optional[str],
        *,
        actor: str,
        use_ai_documentation: bool,
        ai_transmission_acknowledged: bool = False,
        acknowledge_incomplete: bool = False,
    ) -> tuple[str, str, ReconciliationResult]:
        """Apply mapping dispositions, finalize both passes, and assemble a report.

        ``use_ai_documentation`` is required (no default) so this cannot be
        called without the reviewer having made an explicit per-report choice
        about whether Claude documentation runs. It is only actioned inside
        ``prepare_report``, after Gate 3 itself is already durable — so a
        failed or declined AI step never replays the Gate 3 decision.
        """
        _require_actor(actor)
        state = self._state_for(report_id)
        _require_stage(state, {"post_reconciliation"})
        if result.verdicts_are_final:
            raise PipelineStateError("Gate 3 requires an Agent 3 preview, not an already-final result")

        working_result = self._apply_mapping_review(
            report_id=report_id,
            result=result,
            decisions=mapping_decisions,
            actor=actor,
            context=state["context"],
            require_complete=True,
            log_events=True,
        )

        defaults = state["materiality_defaults"]
        internal_verdict, external_verdict, final_result = reconciliation_gate(
            result=working_result,
            report_id=report_id,
            internal_pct_threshold=internal_pct_threshold,
            internal_absolute_threshold=internal_absolute_threshold,
            external_pct_threshold=external_pct_threshold,
            external_absolute_threshold=external_absolute_threshold,
            default_pct_threshold=defaults["default_pct_threshold"],
            default_absolute_threshold=defaults["default_absolute_threshold"],
            internal_threshold_deviation_reason=internal_threshold_deviation_reason,
            external_threshold_deviation_reason=external_threshold_deviation_reason,
            acknowledge_incomplete=acknowledge_incomplete,
            context_match_verdict=state["context_match_verdict"],
            control_total_verdict=state["control_total_check"].status,
            actor=actor,
            audit_log=self._audit_log,
            context=state["context"],
        )
        if not final_result.verdicts_are_final:
            raise PipelineStateError("Gate 3 returned a result that is still marked provisional")

        # Replace the preview before anything downstream reads reconciliation.
        state["reconciliation_result"] = final_result
        state["internal_verdict"] = internal_verdict
        state["external_verdict"] = external_verdict
        state["stage"] = "post_gate3"
        self._snapshot(report_id, "post_gate3", actor)

        self.prepare_report(
            report_id,
            actor=actor,
            use_ai_documentation=use_ai_documentation,
            ai_transmission_acknowledged=ai_transmission_acknowledged,
        )
        return internal_verdict, external_verdict, final_result

    def prepare_report(
        self,
        report_id: str,
        *,
        actor: str,
        use_ai_documentation: bool,
        ai_transmission_acknowledged: bool = False,
    ) -> AuditReport:
        """Continue from a completed Gate 3 after a downstream service failure.

        Gate 3 is already evidentiary and must not be replayed merely because
        documentation or traceability preparation failed. Keeping this as a
        separate resumable transition lets the UI retry those downstream steps
        against the durable post-Gate-3 snapshot — including retrying with a
        different ``use_ai_documentation`` choice after an earlier attempt
        failed, without a second Gate 3 decision or a replayed Anthropic call.

        ``use_ai_documentation`` has no default: every call must state the
        reviewer's explicit choice. When True, ``ai_transmission_acknowledged``
        must also be True — the synthetic/authorised-data confirmation is only
        meaningful when it was actually shown and checked for THIS attempt.

        The ``llm_use_decision`` audit event is written before any Anthropic
        client is touched. If writing it fails, the exception propagates and
        no call is made — the control fails closed, not open.
        """
        _require_actor(actor)
        state = self._state_for(report_id)
        _require_stage(state, {"post_gate3"})
        if use_ai_documentation and not ai_transmission_acknowledged:
            raise ValueError(
                "AI documentation requires the synthetic/authorized-data "
                "transmission confirmation before any Anthropic call is made"
            )

        final_result = state["reconciliation_result"]
        traceability_index = build_traceability_index(
            parsed_file=state["parsed_file"],
            result=final_result,
            findings=state["findings"],
            reference_figures=state["reference_figures"],
        )

        # Recorded before any possible Anthropic call, whichever path is
        # chosen. Control metadata only — never the workbook payload or the
        # disclosure text itself.
        self._audit_log.log_event(
            report_id=report_id,
            event_type="llm_use_decision",
            payload={
                "decision": "use" if use_ai_documentation else "decline",
                "disclosure_version": AI_DISCLOSURE_VERSION,
            },
            actor=actor,
            context=state["context"],
        )

        if use_ai_documentation:
            documentation, manifests = document_tabs(
                state["parsed_file"],
                state["file_context"],
                state["authoritative_outputs"],
                audit_log=self._audit_log,
                report_id=report_id,
                audit_context=state["context"],
                client=self._documentation_client,
            )
            ai_documentation_status = (
                "validation_failed"
                if any(doc.method_summary == FALLBACK_METHOD_SUMMARY for doc in documentation)
                else "generated"
            )
        else:
            documentation, manifests = [], []
            ai_documentation_status = "declined"

        report = _assemble_report(
            state=state,
            result=final_result,
            traceability_index=traceability_index,
            documentation=documentation,
            manifests=manifests,
            internal_verdict=state["internal_verdict"],
            external_verdict=state["external_verdict"],
            ai_documentation_status=ai_documentation_status,
        )
        state["report"] = report
        state["stage"] = "pre_approval_record"
        self._snapshot(report_id, "pre_approval_record", actor)
        return report

    def submit_approval_record(
        self,
        report_id: str,
        approval_name: str,
        role: str,
    ) -> AuditReport:
        """Complete Gate 4 and return the report with its named approval record."""
        state = self._state_for(report_id)
        _require_stage(state, {"pre_approval_record"})
        authorized_approvers = _load_authorized_approvers(self._authorized_approvers_path)
        report = approval_record_gate(
            report=state["report"],
            approval_name=approval_name,
            role=role,
            authorized_approvers=authorized_approvers,
            actor=approval_name,
            audit_log=self._audit_log,
            context=state["context"],
        )
        state["report"] = report
        state["stage"] = "post_approval_record"
        self._snapshot(report_id, "post_approval_record", approval_name)
        return report

    def resume(self, report_id: str) -> dict:
        """Reload the latest durable state for a report into this process.

        load_latest_snapshot() verifies the COMPLETE global audit chain before
        it reads anything, and raises ChainIntegrityError if any row disagrees —
        including a row belonging to another report. Nothing reaches memory in
        that case, because the failure happens before this line.
        """
        snapshot = self._state_store.load_latest_snapshot(report_id)
        if snapshot is None:
            raise PipelineStateError(f"no persisted state exists for report_id {report_id!r}")
        state = _restore_state(json.loads(snapshot.state_json))
        self._state[report_id] = state

        # Fail closed: a recovery that cannot be evidenced does not stand. If
        # recording the verification fails, the restored state is discarded
        # rather than left in memory unrecorded — an unevidenced recovery is
        # the shape of gap this control exists to close.
        if report_id not in self._chain_verification_recorded:
            try:
                self._state_store.record_chain_verification(
                    report_id=report_id,
                    snapshot=snapshot,
                    context=state["context"],
                )
            except Exception:
                self._state.pop(report_id, None)
                raise
            self._chain_verification_recorded.add(report_id)
        return state

    def get_report(self, report_id: str) -> AuditReport:
        state = self._state_for(report_id)
        report = state.get("report")
        if report is None:
            raise PipelineStateError("the report has not been assembled yet")
        return report

    def get_reconciliation_preview(self, report_id: str) -> tuple[str, ReconciliationResult]:
        state = self._state_for(report_id)
        result = state.get("reconciliation_result")
        if result is None or result.verdicts_are_final:
            raise PipelineStateError("no pending Gate 3 preview exists for this report")
        return state.get("reconciliation_label", RECONCILIATION_PREVIEW_LABEL), result

    def get_audit_rows(self, report_id: str) -> list[dict]:
        return self._audit_log.get_rows(report_id)

    def get_materiality_defaults(self, report_id: str) -> dict[str, float]:
        state = self._state_for(report_id)
        return dict(state["materiality_defaults"])

    def get_context_match_verdict(self, report_id: str) -> str:
        return self._state_for(report_id)["context_match_verdict"]

    def get_control_total_check(self, report_id: str) -> ControlTotalCheck:
        return self._state_for(report_id)["control_total_check"]

    def get_stage(self, report_id: str) -> str:
        return self._state_for(report_id)["stage"]

    def preview_independence_disclosure(self, report_id: str, approval_name: str) -> str:
        return independence_disclosure_preview(
            report_id=report_id,
            approval_name=approval_name,
            audit_log=self._audit_log,
        )

    def is_approver_registered(self, approval_name: str) -> bool:
        approvers = _load_authorized_approvers(self._authorized_approvers_path)
        return any(_same_name(entry.get("name"), approval_name) for entry in approvers)

    def verify_evidence_integrity(self) -> tuple[bool, list[str]]:
        return self._audit_log.verify_chain()

    def _state_for(self, report_id: str) -> dict:
        if report_id not in self._state:
            return self.resume(report_id)
        return self._state[report_id]

    def _snapshot(self, report_id: str, gate_name: str, actor: str) -> None:
        state = self._state[report_id]
        self._state_store.save_snapshot(
            report_id=report_id,
            gate_name=gate_name,
            state=_serialize_state(state),
            context=state["context"],
            actor=actor,
        )

    def _apply_mapping_review(
        self,
        *,
        report_id: str,
        result: ReconciliationResult,
        decisions: list[MappingReviewDecision],
        actor: str,
        context: dict,
        require_complete: bool,
        log_events: bool,
    ) -> ReconciliationResult:
        working = result.model_copy(deep=True)
        decisions_by_id = {decision.mapping_id: decision for decision in decisions}
        if len(decisions_by_id) != len(decisions):
            raise PipelineStateError("each mapping may have only one Gate 3 decision")

        known_ids = {mapping.mapping_id for mapping in working.mappings}
        unknown = sorted(set(decisions_by_id) - known_ids)
        if unknown:
            raise PipelineStateError(
                "mapping decision IDs are not present in the reconciliation preview: "
                + ", ".join(unknown)
            )
        missing = sorted(known_ids - set(decisions_by_id))
        if require_complete and missing:
            raise GateBlockedError(
                "Gate 3 blocked: mapping proposals still need a human disposition: "
                + ", ".join(missing)
            )

        approved_at = datetime.now(timezone.utc)
        external_by_mapping = {
            line.mapping_id: line
            for line in working.lines
            if line.check_type == "python_vs_accounts" and line.mapping_id
        }
        added_mappings: list[AccountMapping] = []
        added_lines: list[ReconciliationLine] = []

        for mapping in working.mappings:
            mapping.is_approved = False
            mapping.approved_by = None
            mapping.approved_at = None
            decision = decisions_by_id.get(mapping.mapping_id)
            if decision is None:
                continue

            if mapping.mapping_type != "one_to_one":
                if decision.action != "acknowledge_manual":
                    raise GateBlockedError(
                        f"Gate 3 blocked: {mapping.mapping_id} is {mapping.mapping_type}; "
                        "it can only be acknowledged as requiring manual reconciliation"
                    )
                self._log_mapping_decision(
                    report_id, mapping, "acknowledged_manual_reconciliation", actor, context,
                    enabled=log_events,
                )
                continue

            if decision.action == "acknowledge_manual":
                raise GateBlockedError(
                    f"Gate 3 blocked: one-to-one mapping {mapping.mapping_id} must be "
                    "approved, rejected, or edited"
                )
            if decision.action == "reject":
                self._log_mapping_decision(
                    report_id, mapping, "rejected", actor, context, enabled=log_events
                )
                continue
            if decision.action == "approve":
                mapping.is_approved = True
                mapping.approved_by = actor
                mapping.approved_at = approved_at
                self._log_mapping_decision(
                    report_id, mapping, "approved", actor, context, enabled=log_events
                )
                continue

            replacement = self._replacement_reference_line(
                report_id,
                mapping,
                decision.replacement_reference_line_id,
            )
            original_line = external_by_mapping.get(mapping.mapping_id)
            if original_line is None:
                raise PipelineStateError(
                    f"mapping {mapping.mapping_id} has no accounts reconciliation line to edit"
                )
            new_mapping = AccountMapping(
                mapping_id=f"{mapping.mapping_id}-HUMAN",
                python_output_cell_ref=mapping.python_output_cell_ref,
                reference_line_id=replacement.line_id,
                mapping_type="one_to_one",
                suggested_by="human_direct",
                suggested_confidence=None,
                approved_by=actor,
                approved_at=approved_at,
                is_approved=True,
            )
            if new_mapping.mapping_id in known_ids:
                raise PipelineStateError(
                    f"human-direct mapping ID already exists: {new_mapping.mapping_id}"
                )
            replacement_target = signed_reference_amount(replacement)
            delta, delta_pct = calculate_delta(original_line.source_value, replacement_target)
            added_mappings.append(new_mapping)
            added_lines.append(
                original_line.model_copy(
                    update={
                        "label": f"{replacement.label} ({replacement.line_id})",
                        "target_value": replacement_target,
                        "delta": delta,
                        "delta_pct": delta_pct,
                        "mapping_id": new_mapping.mapping_id,
                    }
                )
            )
            if log_events:
                self._audit_log.log_event(
                    report_id=report_id,
                    event_type="mapping_decision",
                    payload={
                        "mapping_id": mapping.mapping_id,
                        "action": "replaced_with_human_direct_mapping",
                        "replacement_mapping_id": new_mapping.mapping_id,
                        "replacement_reference_line_id": replacement.line_id,
                        "python_output_cell_ref": mapping.python_output_cell_ref,
                    },
                    actor=actor,
                    context=context,
                )

        working.mappings.extend(added_mappings)
        approved_mapping_ids = {
            mapping.mapping_id for mapping in working.mappings if mapping.is_approved
        }
        working.lines = [
            line
            for line in working.lines
            if line.check_type == "excel_vs_python" or line.mapping_id in approved_mapping_ids
        ]
        working.lines.extend(added_lines)

        state = self._state_for(report_id)
        approved_reference_ids = {
            mapping.reference_line_id for mapping in working.mappings if mapping.is_approved
        }
        approved_output_refs = {
            mapping.python_output_cell_ref for mapping in working.mappings if mapping.is_approved
        }
        reference_figures = state["reference_figures"]
        working.unmatched_reference_items = (
            [
                line.line_id
                for line in reference_figures.lines
                if line.line_id not in approved_reference_ids
            ]
            if reference_figures
            else []
        )
        working.unmapped_python_outputs = [
            ref for ref in state["authoritative_outputs"] if ref not in approved_output_refs
        ] if reference_figures else []
        working.verdicts_are_final = False
        return working

    def _replacement_reference_line(
        self,
        report_id: str,
        mapping: AccountMapping,
        replacement_reference_line_id: Optional[str],
    ) -> ReferenceFigureLine:
        replacement_id = (replacement_reference_line_id or "").strip()
        if replacement_id == mapping.reference_line_id:
            raise GateBlockedError(
                f"Gate 3 blocked: edited mapping {mapping.mapping_id} must select a different "
                "reference line"
            )
        reference_figures = self._state_for(report_id)["reference_figures"]
        if reference_figures is None:
            raise PipelineStateError("cannot edit an account mapping without reference figures")
        by_id = {line.line_id: line for line in reference_figures.lines}
        if replacement_id not in by_id:
            raise GateBlockedError(
                f"Gate 3 blocked: replacement reference line {replacement_id!r} does not exist"
            )
        return by_id[replacement_id]

    def _log_mapping_decision(
        self,
        report_id: str,
        mapping: AccountMapping,
        action: str,
        actor: str,
        context: dict,
        *,
        enabled: bool,
    ) -> None:
        if not enabled:
            return
        self._audit_log.log_event(
            report_id=report_id,
            event_type="mapping_decision",
            payload={
                "mapping_id": mapping.mapping_id,
                "reference_line_id": mapping.reference_line_id,
                "python_output_cell_ref": mapping.python_output_cell_ref,
                "action": action,
            },
            actor=actor,
            context=context,
        )


def _assemble_report(
    *,
    state: dict,
    result: ReconciliationResult,
    traceability_index: list,
    documentation: list,
    manifests: list,
    internal_verdict: str,
    external_verdict: str,
    ai_documentation_status: str,
) -> AuditReport:
    """Assemble the model from finalized upstream outputs; do not recompute them."""
    overall = _translation_and_reconciliation_verdict(
        internal_verdict,
        external_verdict,
        state["findings"],
    )
    report_id = state["report_id"]
    return AuditReport(
        file_context=state["file_context"],
        reference_figures=state["reference_figures"],
        authoritative_outputs=state["authoritative_outputs"],
        parsed_file=state["parsed_file"],
        findings=state["findings"],
        mappings=result.mappings,
        reconciliation=result.lines,
        unmatched_reference_items=result.unmatched_reference_items,
        unmapped_python_outputs=result.unmapped_python_outputs,
        context_match_verdict=state["context_match_verdict"],
        control_total_check=state["control_total_check"],
        traceability_index=traceability_index,
        documentation=documentation,
        llm_data_manifest=manifests,
        ai_documentation_status=ai_documentation_status,
        translation_and_reconciliation_verdict=overall,
        internal_verdict=internal_verdict,
        external_verdict=external_verdict,
        workbook_hash=state["parsed_file"].workbook_meta.workbook_hash,
        code_version=state["context"]["code_version"],
        validation_run_id=state["validation_run_id"],
        disclaimer=_DISCLAIMER,
        independence_disclosure=_PENDING_APPROVAL_DISCLOSURE,
        report_approval_name=None,
        report_approval_at=None,
        report_approval_role=None,
        generated_at=datetime.now(timezone.utc),
        report_id=report_id,
        audit_log_verification_note=(
            "This report's underlying evidence trail is hash-chained in a local, "
            "tamper-evident log. To independently verify no entry for report_id "
            f"{report_id} has been altered since it was written, run the audit "
            "log's verify_chain() check against audit.db."
        ),
    )


def _translation_and_reconciliation_verdict(
    internal_verdict: str,
    external_verdict: str,
    findings: list[AnomalyFinding],
) -> str:
    if any(
        finding.severity == "blocker" and finding.human_decision == "confirmed"
        for finding in findings
    ):
        return "block"

    ranking = {"pass": 0, "not_performed": 0, "warn": 1, "incomplete": 2, "block": 3}
    return max(
        (internal_verdict, external_verdict),
        key=lambda verdict: ranking[verdict],
    ).replace("not_performed", "pass")


def _load_materiality_defaults(path: Path) -> dict[str, float]:
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return {
        "default_pct_threshold": float(raw["default_pct_threshold"]),
        "default_absolute_threshold": float(raw["default_absolute_threshold"]),
    }


def _audit_db_path_from_environment() -> str:
    """Deployment wiring for the shared audit-log/state-snapshot database.

    Local execution intentionally stays at ``./audit.db``. Containers set
    ``AUDIT_DB_PATH=/data/audit.db`` and mount ``/data`` so sensitive evidence
    is never part of the image's writable layer.
    """
    return os.getenv("AUDIT_DB_PATH", "").strip() or _DEFAULT_AUDIT_DB_PATH


def _load_authorized_approvers(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    entries = raw.get("approvers", []) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError("authorized approvers configuration must contain a list")
    return entries


def _detect_code_version() -> str:
    configured = os.getenv("CODE_VERSION")
    if configured:
        return configured
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        return f"{head}-dirty" if dirty else head
    except (OSError, subprocess.SubprocessError):
        return "unknown-code-version"


def _serialize_state(state: dict) -> dict:
    return {key: _jsonable(value) for key, value in state.items()}


def _jsonable(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _restore_state(raw: dict) -> dict:
    state = dict(raw)
    state["file_context"] = FileContext.model_validate(raw["file_context"])
    if raw.get("reference_figures") is not None:
        state["reference_figures"] = ReferenceFigures.model_validate(raw["reference_figures"])
    state["control_total_check"] = ControlTotalCheck.model_validate(
        raw.get("control_total_check", {"status": "not_checked"})
    )
    state["parsed_file"] = ParsedFile.model_validate(raw["parsed_file"])
    state["findings"] = [AnomalyFinding.model_validate(item) for item in raw.get("findings", [])]
    if raw.get("reconciliation_result") is not None:
        state["reconciliation_result"] = ReconciliationResult.model_validate(
            raw["reconciliation_result"]
        )
    if raw.get("report") is not None:
        state["report"] = AuditReport.model_validate(raw["report"])
    return state


def _require_actor(actor: str) -> None:
    if not (actor or "").strip():
        raise ValueError("a named actor is required for every human gate")


def _same_name(left: Optional[str], right: Optional[str]) -> bool:
    return (left or "").strip().casefold() == (right or "").strip().casefold()


def _require_stage(state: dict, allowed: set[str]) -> None:
    if state.get("stage") not in allowed:
        raise PipelineStateError(
            f"pipeline is at stage {state.get('stage')!r}; expected one of {sorted(allowed)}"
        )
