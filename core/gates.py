"""The four human gates. Pure logic — no Streamlit.

Every gate that records a decision chains it into the tamper-evident log with a
context dict, so the log says what the decision was made against and not merely
that one occurred.

Gate 4 produces a NAMED APPROVAL RECORD: a typed name, checked against a local
registry, with a timestamp. It is not a signature and not an attestation. The
vocabulary in this module is "approval" throughout, deliberately — both earlier
words were found in review to claim more than this tool can deliver.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from core.audit_log import AuditLog
from core.models import (
    AnomalyFinding,
    AuditReport,
    FileContext,
    ParsedFile,
    ReconciliationResult,
    ReferenceFigures,
)
from core.verdict_logic import compute_verdict

_SAME_PERSON_DISCLOSURE = (
    "The preparer and the approver were the same individual for this report. "
    "No independent review was performed."
)
_NO_PREPARER_DISCLOSURE = (
    "No preparer was recorded separately from the approver for this report. "
    "No independent review was performed."
)


class GateBlockedError(Exception):
    """Raised when a gate's conditions are not met. Never caught and ignored."""


# ---------------------------------------------------------------------------
# Gate 1 — context confirmation
# ---------------------------------------------------------------------------


def context_gate(
    file_context: FileContext,
    reference_figures: Optional[ReferenceFigures],
    confirmed: bool,
    report_id: str,
    actor: str,
    audit_log: AuditLog,
    context: dict,
) -> tuple[bool, str]:
    """Confirm the tool understood the assignment, and check the two sides
    describe the same thing.

    A context mismatch does NOT block here. The human should still be able to
    proceed with the Excel-side work even when the accounting comparison can't
    be trusted; the mismatch flows through to cap external_verdict at "block"
    in Gate 3.
    """
    if not confirmed:
        raise GateBlockedError(
            f"Gate 1 blocked: context for '{file_context.filename}' has not been confirmed"
        )

    context_match_verdict, basis_warning = _compare_context(file_context, reference_figures)

    audit_log.log_event(
        report_id=report_id,
        event_type="gate_decision",
        payload={
            "gate": 1,
            "action": "context_confirmed",
            "filename": file_context.filename,
            "description": file_context.description,
            "context_match_verdict": context_match_verdict,
            "basis_warning": basis_warning,
        },
        actor=actor,
        context=context,
    )

    return True, context_match_verdict


def _compare_context(
    file_context: FileContext, reference_figures: Optional[ReferenceFigures]
) -> tuple[str, Optional[str]]:
    """Entity, period and currency must agree. Basis is only a warning.

    Accounting bases can legitimately differ by design in some reconciliations.
    Whether a given difference is acceptable is a CFO judgment, not a rule this
    tool gets to enforce — so it is surfaced, not acted on.
    """
    if reference_figures is None:
        return "not_checked", None

    disagreements = [
        field
        for field in ("entity", "period", "currency")
        if not _same(getattr(file_context, field), getattr(reference_figures, field))
    ]

    basis_warning = None
    if not _same(file_context.basis, reference_figures.basis):
        basis_warning = (
            f"basis differs: workbook says {file_context.basis!r}, "
            f"reference figures say {reference_figures.basis!r} — review whether "
            f"this comparison is intended"
        )

    return ("mismatch" if disagreements else "match"), basis_warning


def _same(left: Optional[str], right: Optional[str]) -> bool:
    return (left or "").strip().casefold() == (right or "").strip().casefold()


# ---------------------------------------------------------------------------
# Gate 2 — findings review and output designation
# ---------------------------------------------------------------------------


def findings_review_gate(
    findings: list[AnomalyFinding],
    parsed_file: ParsedFile,
    authoritative_outputs: list[str],
    report_id: str,
    actor: str,
    audit_log: AuditLog,
    context: dict,
) -> tuple[list[AnomalyFinding], list[str]]:
    """Two things a human must do before reconstruction can start.

    Designating the authoritative outputs belongs here rather than being
    inferred: an earlier design guessed the output tab by keyword-matching the
    file description, which is far too weak a basis for deciding which figures
    a validation report is about.
    """
    undecided = [finding.finding_id for finding in findings if finding.human_decision is None]
    if undecided:
        raise GateBlockedError(
            f"Gate 2 blocked: findings without a human decision: {', '.join(undecided)}"
        )

    if not authoritative_outputs:
        raise GateBlockedError(
            "Gate 2 blocked: no authoritative outputs designated. A human must name "
            "the cells to reconstruct and reconcile; they are never inferred."
        )

    unknown = [ref for ref in authoritative_outputs if ref not in parsed_file.cells]
    if unknown:
        raise GateBlockedError(
            f"Gate 2 blocked: designated outputs do not exist in the workbook: {', '.join(unknown)}"
        )

    audit_log.log_event(
        report_id=report_id,
        event_type="gate_decision",
        payload={
            "gate": 2,
            "action": "findings_reviewed_and_outputs_designated",
            "authoritative_outputs": authoritative_outputs,
            # Every disposition, not just the confirmations. A dismissed finding
            # is reviewed, not approved, and the log has to be able to tell them
            # apart later.
            "dispositions": [
                {
                    "finding_id": f.finding_id,
                    "severity": f.severity,
                    "disposition": f.human_decision,
                    "reason": f.human_reason,
                    "decided_by": f.decided_by,
                }
                for f in findings
            ],
        },
        actor=actor,
        context=context,
    )

    return findings, authoritative_outputs


# ---------------------------------------------------------------------------
# Gate 3 — reconciliation
# ---------------------------------------------------------------------------


def reconciliation_gate(
    result: ReconciliationResult,
    report_id: str,
    internal_pct_threshold: float,
    internal_absolute_threshold: float,
    external_pct_threshold: float,
    external_absolute_threshold: float,
    default_pct_threshold: float,
    default_absolute_threshold: float,
    internal_threshold_deviation_reason: Optional[str],
    external_threshold_deviation_reason: Optional[str],
    acknowledge_incomplete: bool,
    context_match_verdict: str,
    actor: str,
    audit_log: AuditLog,
    context: dict,
) -> tuple[str, str, ReconciliationResult]:
    """Recompute every verdict against the approved thresholds, then aggregate.

    Takes the whole ReconciliationResult rather than its pieces, so there is one
    object carrying the lines, the mappings, and both completeness directions.

    ORDERING NOTE: the two checks that can raise — threshold deviation and
    mapping approval — run BEFORE the recompute mutates `result`. The step lists
    the recompute first, and its reason is that no aggregation may read a
    verdict Agent 3 set. That intent is preserved: nothing below reads a
    provisional verdict. Validating first only means a blocked gate does not
    leave behind a result object marked verdicts_are_final=True.
    """
    internal_deviates = _require_threshold_reason(
        label="internal",
        pct_threshold=internal_pct_threshold,
        absolute_threshold=internal_absolute_threshold,
        default_pct_threshold=default_pct_threshold,
        default_absolute_threshold=default_absolute_threshold,
        reason=internal_threshold_deviation_reason,
    )
    external_deviates = _require_threshold_reason(
        label="external",
        pct_threshold=external_pct_threshold,
        absolute_threshold=external_absolute_threshold,
        default_pct_threshold=default_pct_threshold,
        default_absolute_threshold=default_absolute_threshold,
        reason=external_threshold_deviation_reason,
    )

    _require_approved_mappings(result)

    internal_verdict, external_verdict = _evaluate_reconciliation(
        result=result,
        internal_pct_threshold=internal_pct_threshold,
        internal_absolute_threshold=internal_absolute_threshold,
        external_pct_threshold=external_pct_threshold,
        external_absolute_threshold=external_absolute_threshold,
        default_pct_threshold=default_pct_threshold,
        default_absolute_threshold=default_absolute_threshold,
        context_match_verdict=context_match_verdict,
    )
    result.verdicts_are_final = True

    audit_log.log_event(
        report_id=report_id,
        event_type="gate_decision",
        payload={
            "gate": 3,
            "action": "reconciliation_reviewed",
            "internal_verdict": internal_verdict,
            "external_verdict": external_verdict,
            "context_match_verdict": context_match_verdict,
            "acknowledge_incomplete": acknowledge_incomplete,
            "unmatched_reference_items": result.unmatched_reference_items,
            "unmapped_python_outputs": result.unmapped_python_outputs,
            # Flagged on its own, not folded into the general context, so a
            # deviation cannot slide past a reader scanning the payload.
            "threshold_deviation": {
                "internal": {
                    "deviated": internal_deviates,
                    "reason": internal_threshold_deviation_reason,
                    "pct_threshold": internal_pct_threshold,
                    "absolute_threshold": internal_absolute_threshold,
                    "default_pct_threshold": default_pct_threshold,
                    "default_absolute_threshold": default_absolute_threshold,
                },
                "external": {
                    "deviated": external_deviates,
                    "reason": external_threshold_deviation_reason,
                    "pct_threshold": external_pct_threshold,
                    "absolute_threshold": external_absolute_threshold,
                    "default_pct_threshold": default_pct_threshold,
                    "default_absolute_threshold": default_absolute_threshold,
                },
            },
        },
        actor=actor,
        context={
            **context,
            "internal_pct_threshold": internal_pct_threshold,
            "internal_absolute_threshold": internal_absolute_threshold,
            "internal_threshold_deviation_reason": internal_threshold_deviation_reason,
            "external_pct_threshold": external_pct_threshold,
            "external_absolute_threshold": external_absolute_threshold,
            "external_threshold_deviation_reason": external_threshold_deviation_reason,
            "acknowledge_incomplete": acknowledge_incomplete,
            "context_match_verdict": context_match_verdict,
        },
    )

    if "block" in (internal_verdict, external_verdict):
        raise GateBlockedError(
            f"Gate 3 blocked: internal_verdict={internal_verdict}, "
            f"external_verdict={external_verdict}. Blockers cannot be bypassed here."
        )

    if "incomplete" in (internal_verdict, external_verdict) and not acknowledge_incomplete:
        raise GateBlockedError(
            f"Gate 3 blocked: internal_verdict={internal_verdict}, "
            f"external_verdict={external_verdict}. An incomplete reconciliation must be "
            "explicitly acknowledged before the pipeline continues."
        )

    return internal_verdict, external_verdict, result


def preview_reconciliation(
    result: ReconciliationResult,
    *,
    internal_pct_threshold: float,
    internal_absolute_threshold: float,
    external_pct_threshold: float,
    external_absolute_threshold: float,
    default_pct_threshold: float,
    default_absolute_threshold: float,
    context_match_verdict: str,
) -> tuple[str, str, ReconciliationResult]:
    """Return a side-effect-free Gate 3 preview for a rendering layer."""
    preview = result.model_copy(deep=True)
    internal, external = _evaluate_reconciliation(
        result=preview,
        internal_pct_threshold=internal_pct_threshold,
        internal_absolute_threshold=internal_absolute_threshold,
        external_pct_threshold=external_pct_threshold,
        external_absolute_threshold=external_absolute_threshold,
        default_pct_threshold=default_pct_threshold,
        default_absolute_threshold=default_absolute_threshold,
        context_match_verdict=context_match_verdict,
    )
    preview.verdicts_are_final = False
    return internal, external, preview


def _evaluate_reconciliation(
    *,
    result: ReconciliationResult,
    internal_pct_threshold: float,
    internal_absolute_threshold: float,
    external_pct_threshold: float,
    external_absolute_threshold: float,
    default_pct_threshold: float,
    default_absolute_threshold: float,
    context_match_verdict: str,
) -> tuple[str, str]:
    """Recompute both passes independently and aggregate without logging."""
    mappings_by_id = {mapping.mapping_id: mapping for mapping in result.mappings}
    for line in result.lines:
        is_internal = line.check_type == "excel_vs_python"
        pct_threshold = internal_pct_threshold if is_internal else external_pct_threshold
        absolute_threshold = (
            internal_absolute_threshold if is_internal else external_absolute_threshold
        )
        mapping = mappings_by_id.get(line.mapping_id) if line.mapping_id else None
        is_ambiguous = (
            not is_internal and mapping is not None and mapping.mapping_type != "one_to_one"
        )
        line.verdict = compute_verdict(
            delta=line.delta,
            delta_pct=line.delta_pct,
            pct_threshold=pct_threshold,
            absolute_threshold=absolute_threshold,
            completeness=line.completeness,
            is_ambiguous_match=is_ambiguous,
        )
        line.pct_threshold = pct_threshold
        line.absolute_threshold = absolute_threshold
        line.threshold_is_default = (
            pct_threshold == default_pct_threshold
            and absolute_threshold == default_absolute_threshold
        )

    internal_lines = [line for line in result.lines if line.check_type == "excel_vs_python"]
    external_lines = [line for line in result.lines if line.check_type == "python_vs_accounts"]
    internal_verdict = _aggregate(internal_lines, empty="incomplete")
    external_verdict = _aggregate(
        external_lines,
        empty=(
            "not_performed"
            if context_match_verdict == "not_checked"
            else "incomplete"
        ),
    )
    if result.unmatched_reference_items or result.unmapped_python_outputs:
        # A numeric warning cannot hide a population gap.  Incompleteness is
        # the more important statement because the comparison did not cover
        # everything; a genuine numeric block remains a block.
        if external_verdict in ("pass", "warn", "not_performed"):
            external_verdict = "incomplete"
    if context_match_verdict == "mismatch":
        external_verdict = "block"
    return internal_verdict, external_verdict


def _require_threshold_reason(
    *,
    label: str,
    pct_threshold: float,
    absolute_threshold: float,
    default_pct_threshold: float,
    default_absolute_threshold: float,
    reason: Optional[str],
) -> bool:
    deviates = (
        pct_threshold != default_pct_threshold
        or absolute_threshold != default_absolute_threshold
    )
    if deviates and not (reason or "").strip():
        raise GateBlockedError(
            f"Gate 3 blocked: {label} thresholds were changed from the organization "
            f"defaults (pct {default_pct_threshold} -> {pct_threshold}, absolute "
            f"{default_absolute_threshold} -> {absolute_threshold}) without a stated "
            "reason. Deviations are permitted; silent ones are not."
        )
    return deviates


def _require_approved_mappings(result: ReconciliationResult) -> None:
    """No unapproved mapping may contribute to a verdict.

    Checked against the mappings actually referenced by accounts-side lines, so
    an empty mappings list cannot satisfy this trivially — a line naming a
    mapping that isn't there is itself a failure.
    """
    mappings_by_id = {m.mapping_id: m for m in result.mappings}
    unapproved: list[str] = []
    missing: list[str] = []

    for line in result.lines:
        if line.check_type != "python_vs_accounts":
            continue
        if line.mapping_id is None:
            missing.append(f"{line.label} (no mapping_id)")
            continue
        mapping = mappings_by_id.get(line.mapping_id)
        if mapping is None:
            missing.append(f"{line.label} (mapping {line.mapping_id} not in result.mappings)")
        elif not mapping.is_approved:
            unapproved.append(
                f"{mapping.mapping_id} (confidence {mapping.suggested_confidence}, "
                f"suggested_by {mapping.suggested_by})"
            )

    if missing:
        raise GateBlockedError(
            "Gate 3 blocked: accounts-side lines with no resolvable mapping: "
            + ", ".join(missing)
        )
    if unapproved:
        raise GateBlockedError(
            "Gate 3 blocked: unapproved account mappings cannot produce a reconciliation "
            "verdict, whatever their match confidence: " + ", ".join(unapproved)
        )


def _aggregate(lines: list, empty: str) -> str:
    """Worst verdict wins. An empty set is never silently a pass."""
    if not lines:
        return empty
    verdicts = {line.verdict for line in lines}
    for candidate in ("block", "incomplete", "warn"):
        if candidate in verdicts:
            return candidate
    return "pass"


# ---------------------------------------------------------------------------
# Gate 4 — named approval record
# ---------------------------------------------------------------------------


def approval_record_gate(
    report: AuditReport,
    approval_name: str,
    role: str,
    authorized_approvers: list[dict],
    actor: str,
    audit_log: AuditLog,
    context: dict,
) -> AuditReport:
    """Record that a named person approved this report.

    This is a typed name, checked against a local registry, with a timestamp.
    It is not a signature, not an attestation, and makes no claim about identity
    beyond what someone typed.

    The registry check does NOT block. Blocking on it would present a
    spell-checker as an authentication control. An unregistered name is recorded
    as its own event so the discrepancy is visible in the trail instead of
    silently accepted.
    """
    if not (approval_name or "").strip():
        raise GateBlockedError("Gate 4 blocked: an approval requires a name")

    registered = any(
        _same(entry.get("name"), approval_name) for entry in authorized_approvers
    )
    if not registered:
        audit_log.log_event(
            report_id=report.report_id,
            event_type="gate_decision",
            payload={
                "gate": 4,
                "action": "approval_record_unregistered_name",
                "approval_name": approval_name,
                "note": (
                    "name is not in config/authorized_approvers.json. This is a "
                    "registry check, not authentication, so it does not block."
                ),
            },
            actor=actor,
            context=context,
        )

    report.report_approval_name = approval_name
    report.report_approval_at = datetime.now(timezone.utc)
    report.report_approval_role = role
    report.independence_disclosure = independence_disclosure_preview(
        report_id=report.report_id, approval_name=approval_name, audit_log=audit_log
    )

    audit_log.log_event(
        report_id=report.report_id,
        event_type="report_approved",
        payload={
            "gate": 4,
            "action": "approval_record_created",
            "approval_name": approval_name,
            "role": role,
            "name_in_registry": registered,
            "independence_disclosure": report.independence_disclosure,
        },
        actor=actor,
        context=context,
    )

    return report


def independence_disclosure_preview(
    report_id: str, approval_name: str, audit_log: AuditLog
) -> str:
    """Say plainly who prepared and who approved.

    Never phrased to imply an independent review that did not happen. The solo
    case — one person through all four gates — is the expected one here.
    """
    # Gates 2 and 3 only. Gate 4 logs its own gate_decision for an unregistered
    # name, and counting that would make the approver their own preparer and
    # produce "prepared by X and approved by X" — which reads as two people.
    preparers = set()
    for row in audit_log.get_rows(report_id):
        if row["event_type"] != "gate_decision" or not row["actor"]:
            continue
        try:
            gate = json.loads(row["payload_json"]).get("gate")
        except (ValueError, TypeError):
            continue
        if gate in (2, 3):
            preparers.add(row["actor"])

    if not preparers:
        return _NO_PREPARER_DISCLOSURE
    if all(_same(name, approval_name) for name in preparers):
        return _SAME_PERSON_DISCLOSURE
    others = ", ".join(sorted(preparers))
    return f"This report was prepared by {others} and approved by {approval_name}."
