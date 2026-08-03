"""Enforces the four non-bypassable human gates: context validation, findings review, reconciliation sign-off, final sign-off."""

from datetime import datetime, timezone

from core.audit_log import AuditLog
from core.models import AnomalyFinding, AuditReport, FileContext, ReconciliationLine, ReferenceFigures


class GateBlockedError(Exception):
    pass


def context_gate(
    file_context: FileContext,
    confirmed: bool,
    report_id: str,
    user_name: str,
    audit_log: AuditLog,
    reference_figures: ReferenceFigures | None = None,
    reference_figures_confirmed: bool = True,
) -> bool:
    if not confirmed:
        raise GateBlockedError(
            f"Gate 1 blocked: context for '{file_context.filename}' has not been confirmed"
        )
    if reference_figures is not None and not reference_figures_confirmed:
        raise GateBlockedError(
            f"Gate 1 blocked: reference figures '{reference_figures.source_label}' have not been confirmed"
        )

    audit_log.log_decision(
        report_id=report_id,
        gate=1,
        finding_id=None,
        action="context_confirmed",
        reason=f"Confirmed context for '{file_context.filename}': {file_context.description}",
        user_name=user_name,
    )

    if reference_figures is not None:
        audit_log.log_decision(
            report_id=report_id,
            gate=1,
            finding_id=None,
            action="reference_figures_confirmed",
            reason=f"Confirmed reference figures: '{reference_figures.source_label}'",
            user_name=user_name,
        )

    return True


def findings_review_gate(findings: list[AnomalyFinding], report_id: str) -> list[AnomalyFinding]:
    undecided = [finding.finding_id for finding in findings if finding.human_decision is None]
    if undecided:
        raise GateBlockedError(
            f"Gate 2 blocked: findings without a human decision: {', '.join(undecided)}"
        )
    return findings


def _aggregate_verdict(lines: list[ReconciliationLine]) -> str:
    if not lines:
        return "not_performed"
    verdicts = {line.verdict for line in lines}
    if "block" in verdicts:
        return "block"
    if "warn" in verdicts:
        return "warn"
    return "pass"


def reconciliation_gate(
    lines: list[ReconciliationLine],
    report_id: str,
    user_name: str,
    audit_log: AuditLog,
) -> tuple[str, str]:
    internal_lines = [line for line in lines if line.check_type == "excel_vs_python"]
    external_lines = [line for line in lines if line.check_type == "python_vs_accounts"]

    internal_verdict = _aggregate_verdict(internal_lines)
    external_verdict = _aggregate_verdict(external_lines)

    if internal_verdict == "block" or external_verdict == "block":
        raise GateBlockedError(
            f"Gate 3 blocked: internal_verdict={internal_verdict}, external_verdict={external_verdict}"
        )

    for line in lines:
        if line.verdict == "warn":
            audit_log.log_decision(
                report_id=report_id,
                gate=3,
                finding_id=None,
                action="reconciliation_warning",
                reason=f"[{line.check_type}] {line.label}: delta={line.delta} ({line.delta_pct}%)",
                user_name=user_name,
            )

    return internal_verdict, external_verdict


def sign_off_gate(report: AuditReport, signed_by: str, role: str, audit_log: AuditLog) -> AuditReport:
    if not signed_by:
        raise GateBlockedError("Gate 4 blocked: signed_by must not be empty")

    report.signed_by = signed_by
    report.signed_at = datetime.now(timezone.utc)
    report.signed_role = role

    audit_log.log_decision(
        report_id=report.report_id,
        gate=4,
        finding_id=None,
        action="signed_off",
        reason=f"Signed off by {signed_by} ({role})",
        user_name=signed_by,
    )

    return report
