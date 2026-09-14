"""Detection contract for the six seeded large-workbook defects.

The evidence model stays honest: D1 and D2 are anomaly findings, D3 is formula
reconstruction evidence, D4 and D5 are the two directions of accounting
population completeness, and D6 is context evidence.  They are not all forced
into ``AnomalyFinding`` merely to fit one test shape.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from agents.anomaly_detector import detect_anomalies
from agents.orchestrator import Orchestrator
from agents.parser import parse_workbook
from agents.reconciliation import run_reconciliation
from core.audit_log import AuditLog
from core.models import FileContext, MappingReviewDecision
from core.state_store import StateStore
from core.ui_inputs import build_reference_figures
from core.workbook_identity import sha256_bytes

FIXTURES = Path(__file__).parent / "fixtures"
WORKBOOK_PATH = FIXTURES / "ifrs17_workbook_defective.xlsx"
REFERENCE_PATH = FIXTURES / "accounting_reference_defective.csv"
EXPECTED = json.loads((FIXTURES / "expected_defects.json").read_text(encoding="utf-8"))
ACTOR = "Isaac Shukla"
NOW = datetime.now(timezone.utc)


def _reference_figures():
    with REFERENCE_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    first = rows[0]
    return build_reference_figures(
        source_label="Synthetic defective Q4 2025 trial balance",
        entity=first["entity"],
        period=first["period"],
        currency=first["currency"],
        basis=first["basis"],
        control_total=float(first["control_total"]),
        control_total_confirmed_by_human=True,
        rows=rows,
        require_account_number=True,
        uploaded_at=NOW,
    )


def _file_context(workbook_hash: str) -> FileContext:
    context = EXPECTED["workbook_context"]
    return FileContext(
        filename=WORKBOOK_PATH.name,
        description="Controlled six-defect synthetic IFRS 17-related validation case.",
        user_role="actuary",
        entity=context["entity"],
        period=context["period"],
        currency=context["currency"],
        basis="IFRS 17-related synthetic reserve roll-forward",
        confirmed_workbook_hash=workbook_hash,
        uploaded_at=NOW,
    )


def _finding_for(findings, defect):
    tab, cell_ref = defect["cell_address"].split("!", 1)
    return next(
        (
            finding
            for finding in findings
            if finding.tab == tab
            and finding.cell_ref == cell_ref
            and defect["expected_description_contains"] in finding.description
        ),
        None,
    )


def test_defective_fixture_hashes_and_exact_evidence_contract():
    assert hashlib.sha256(WORKBOOK_PATH.read_bytes()).hexdigest() == EXPECTED["defective_workbook_sha256"]
    assert hashlib.sha256(REFERENCE_PATH.read_bytes()).hexdigest() == EXPECTED["defective_reference_sha256"]

    parsed = parse_workbook(WORKBOOK_PATH.read_bytes())
    findings = detect_anomalies(parsed)
    anomaly_defects = {
        defect_id: defect
        for defect_id, defect in EXPECTED["defects"].items()
        if defect["evidence_kind"] == "anomaly_finding"
    }
    matched_finding_ids = set()
    for defect_id, defect in anomaly_defects.items():
        finding = _finding_for(findings, defect)
        assert finding is not None, defect_id
        assert finding.severity == defect["expected_severity"], defect_id
        matched_finding_ids.add(finding.finding_id)
    false_positives = [finding for finding in findings if finding.finding_id not in matched_finding_ids]
    assert false_positives == []

    result = run_reconciliation(
        parsed,
        EXPECTED["authoritative_outputs"],
        _reference_figures(),
    )
    d3 = EXPECTED["defects"]["D3"]
    internal_d3 = next(
        line
        for line in result.lines
        if line.check_type == "excel_vs_python"
        and any(step.cell_ref == d3["cell_address"] for step in line.derivation)
    )
    assert internal_d3.completeness == d3["expected_completeness"]
    assert internal_d3.verdict == d3["expected_verdict"]
    assert any(d3["expected_unsupported_contains"] in item for item in internal_d3.unsupported_elements)
    assert EXPECTED["defects"]["D4"]["reference_line_id"] in result.unmatched_reference_items
    assert EXPECTED["defects"]["D5"]["cell_address"] in result.unmapped_python_outputs


def test_defective_case_reaches_gate3_preview_but_context_and_completeness_block(tmp_path):
    audit_log = AuditLog(str(tmp_path / "audit.db"))
    orchestrator = Orchestrator(
        audit_log=audit_log,
        state_store=StateStore(audit_log.db_path, audit_log=audit_log),
        documentation_client=None,
        code_version="ifrs17-defect-acceptance",
    )
    workbook_bytes = WORKBOOK_PATH.read_bytes()
    workbook_hash = sha256_bytes(workbook_bytes)
    report_id, _, findings = orchestrator.run(
        workbook_bytes,
        _file_context(workbook_hash),
        _reference_figures(),
        expected_workbook_hash=workbook_hash,
        context_confirmed=True,
        actor=ACTOR,
    )
    assert orchestrator.get_context_match_verdict(report_id) == EXPECTED["defects"]["D6"]["expected_status"]
    reviewed = [
        finding.model_copy(
            update={
                "human_decision": "confirmed",
                "human_reason": "Controlled expected defect reviewed in acceptance test.",
                "decided_by": ACTOR,
                "decided_at": NOW,
            }
        )
        for finding in findings
    ]
    _, preview = orchestrator.submit_gate2_decisions(
        report_id,
        reviewed,
        EXPECTED["authoritative_outputs"],
        actor=ACTOR,
    )
    decisions = [
        MappingReviewDecision(mapping_id=mapping.mapping_id, action="approve")
        for mapping in preview.mappings
    ]
    internal, external, reviewed_preview = orchestrator.preview_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=decisions,
        internal_pct_threshold=0.01,
        internal_absolute_threshold=100.0,
        external_pct_threshold=0.01,
        external_absolute_threshold=100.0,
        actor=ACTOR,
    )
    assert internal == "incomplete"
    assert external == "block"
    assert reviewed_preview.unmatched_reference_items == ["REF-0002"]
    assert reviewed_preview.unmapped_python_outputs == ["AccountingBridge!C10"]
