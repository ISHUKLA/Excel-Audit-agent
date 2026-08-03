"""Coordinates the pipeline (agents + gates) in sequence; contains no business logic of its own."""

import uuid
from typing import Optional

import anthropic

from agents.anomaly_detector import detect_anomalies
from agents.documentation import document_tabs
from agents.parser import parse_workbook
from agents.reconciliation import apply_thresholds, run_reconciliation
from core.audit_log import AuditLog
from core.gates import context_gate, findings_review_gate, reconciliation_gate, sign_off_gate
from core.models import (
    AnomalyFinding,
    AuditReport,
    FileContext,
    ReconciliationLine,
    ReferenceFigures,
)
from core.traceability import build_traceability_index


class Orchestrator:
    def __init__(
        self,
        audit_log: Optional[AuditLog] = None,
        documentation_client: Optional[anthropic.Anthropic] = None,
    ):
        self._audit_log = audit_log or AuditLog()
        self._documentation_client = documentation_client
        self._state: dict[str, dict] = {}

    def run(
        self,
        file_path: str,
        file_context: FileContext,
        user_name: str,
        context_confirmed: bool,
        reference_figures: Optional[ReferenceFigures] = None,
        reference_figures_confirmed: bool = True,
    ) -> tuple[str, list[AnomalyFinding]]:
        report_id = str(uuid.uuid4())

        context_gate(
            file_context,
            confirmed=context_confirmed,
            report_id=report_id,
            user_name=user_name,
            audit_log=self._audit_log,
            reference_figures=reference_figures,
            reference_figures_confirmed=reference_figures_confirmed,
        )

        parsed_file = parse_workbook(file_path)
        findings = detect_anomalies(parsed_file)

        self._state[report_id] = {
            "file_context": file_context,
            "reference_figures": reference_figures,
            "user_name": user_name,
            "parsed_file": parsed_file,
            "findings": findings,
        }

        return report_id, findings

    def submit_findings_decisions(
        self, report_id: str, findings: list[AnomalyFinding]
    ) -> tuple[list[ReconciliationLine], list[str]]:
        state = self._state[report_id]
        findings_review_gate(findings, report_id=report_id)
        state["findings"] = findings

        lines, unmatched = run_reconciliation(
            state["parsed_file"],
            state["file_context"],
            reference_figures=state["reference_figures"],
        )
        state["unmatched_reference_items"] = unmatched

        return lines, unmatched

    def submit_reconciliation_decisions(
        self,
        report_id: str,
        lines: list[ReconciliationLine],
        internal_threshold: float,
        external_threshold: float,
        user_name: str,
    ) -> tuple[str, str]:
        state = self._state[report_id]
        lines = apply_thresholds(lines, internal_threshold, external_threshold)

        internal_verdict, external_verdict = reconciliation_gate(
            lines, report_id=report_id, user_name=user_name, audit_log=self._audit_log
        )

        traceability_index = build_traceability_index(
            state["parsed_file"], lines, state["findings"], state["file_context"]
        )
        documentation = document_tabs(
            state["parsed_file"], state["file_context"], client=self._documentation_client
        )

        # Gate 2 already guarantees every finding has a human_decision; "unresolved"
        # means the human confirmed it as real, not that it's merely undecided.
        unresolved_blocker = any(
            finding.severity == "blocker" and finding.human_decision == "confirmed"
            for finding in state["findings"]
        )
        overall_verdict = _combine_verdicts(internal_verdict, external_verdict, unresolved_blocker)

        report = AuditReport(
            file_context=state["file_context"],
            reference_figures=state["reference_figures"],
            parsed_file=state["parsed_file"],
            findings=state["findings"],
            reconciliation=lines,
            unmatched_reference_items=state["unmatched_reference_items"],
            traceability_index=traceability_index,
            documentation=documentation,
            overall_verdict=overall_verdict,
            internal_verdict=internal_verdict,
            external_verdict=external_verdict,
            signed_by=None,
            signed_at=None,
            report_id=report_id,
        )
        state["report"] = report

        return internal_verdict, external_verdict

    def submit_signoff(self, report_id: str, signed_by: str, role: str) -> AuditReport:
        state = self._state[report_id]
        return sign_off_gate(state["report"], signed_by=signed_by, role=role, audit_log=self._audit_log)

    def get_report(self, report_id: str) -> AuditReport:
        """Read-only access to the (possibly unsigned) assembled report, for UI preview before Gate 4."""
        return self._state[report_id]["report"]

    def get_decisions(self, report_id: str) -> list[dict]:
        """Read-only access to this report's audit trail, without exposing the AuditLog instance itself."""
        return self._audit_log.get_decisions(report_id)


def _combine_verdicts(internal_verdict: str, external_verdict: str, unresolved_blocker: bool) -> str:
    if internal_verdict == "block" or external_verdict == "block" or unresolved_blocker:
        return "block"
    if internal_verdict == "warn" or external_verdict == "warn":
        return "warn"
    return "pass"
