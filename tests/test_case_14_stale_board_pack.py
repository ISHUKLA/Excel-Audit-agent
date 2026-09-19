"""Acceptance evidence for Case 14's stale board-pack calculation caches.

The saved XLSX and CSV run through the production parser, detector,
reconciliation, gates, and orchestrator. No spreadsheet engine or LLM provider
is invoked by these tests.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
import pytest

from agents.anomaly_detector import detect_anomalies
from agents.orchestrator import Orchestrator, PipelineStateError
from agents.parser import parse_workbook
from agents.reconciliation import run_reconciliation
from core.audit_log import AuditLog
from core.gates import GateBlockedError
from core.models import FileContext, MappingReviewDecision
from core.state_store import StateStore
from core.ui_inputs import build_reference_figures
from core.workbook_identity import sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "demo" / "final cases" / "case_14_stale_board_pack"
EXPECTED_PATH = CASE_DIR / "expected_results" / "case_14_stale_board_pack_expected.json"
EXPECTED = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
WORKBOOK_PATH = ROOT / EXPECTED["workbook_file"]
REFERENCE_PATH = ROOT / EXPECTED["reference_file"]
ACTOR = "Isaac Shukla"
NOW = datetime.now(timezone.utc)

# Technical fixture inputs only. Gate 3 still requires a human to choose the
# real report thresholds; these values are neither defaults nor recommendations.
FIXTURE_PCT_THRESHOLD = 0.01
FIXTURE_ABSOLUTE_THRESHOLD = 100.0


class _ForbiddenMessages:
    def create(self, **_kwargs):  # pragma: no cover - failure sentinel
        raise AssertionError("Case 14 acceptance must not call an LLM provider")


class _ForbiddenClient:
    messages = _ForbiddenMessages()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reference_figures():
    with REFERENCE_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    context = EXPECTED["reference_context"]
    return build_reference_figures(
        source_label=rows[0]["ledger_source"],
        entity=context["entity"],
        period=context["period"],
        currency=context["currency"],
        basis=context["basis"],
        control_total=EXPECTED["reference_control_total"],
        control_total_confirmed_by_human=True,
        rows=rows,
        require_account_number=True,
        uploaded_at=NOW,
    )


def _orchestrator(tmp_path: Path) -> Orchestrator:
    db_path = str(tmp_path / "case14.db")
    audit_log = AuditLog(db_path)
    return Orchestrator(
        audit_log=audit_log,
        state_store=StateStore(db_path, audit_log=audit_log),
        documentation_client=_ForbiddenClient(),
        code_version="case-14-stale-board-pack",
    )


def _run_to_gate3(orchestrator: Orchestrator):
    workbook_bytes = WORKBOOK_PATH.read_bytes()
    workbook_hash = sha256_bytes(workbook_bytes)
    context = EXPECTED["gate1_context"]
    file_context = FileContext(
        filename=WORKBOOK_PATH.name,
        description="Synthetic Case 14 calculation-freshness demonstration",
        user_role="actuary",
        confirmed_workbook_hash=workbook_hash,
        entity=context["entity"],
        period=context["period"],
        currency=context["currency"],
        basis=context["basis"],
        uploaded_at=NOW,
    )
    report_id, parsed, findings = orchestrator.run(
        workbook_bytes,
        file_context,
        _reference_figures(),
        expected_workbook_hash=workbook_hash,
        context_confirmed=True,
        actor=ACTOR,
    )
    reviewed = [
        finding.model_copy(
            update={
                "human_decision": "confirmed",
                "human_reason": "Reviewed for the synthetic Case 14 acceptance run.",
                "decided_by": ACTOR,
                "decided_at": NOW,
            }
        )
        for finding in findings
    ]
    _, preview = orchestrator.submit_gate2_decisions(
        report_id,
        reviewed,
        EXPECTED["authoritative_output_cells"],
        actor=ACTOR,
    )
    return report_id, parsed, findings, preview


def _gate3_kwargs():
    return {
        "internal_pct_threshold": FIXTURE_PCT_THRESHOLD,
        "internal_absolute_threshold": FIXTURE_ABSOLUTE_THRESHOLD,
        "external_pct_threshold": FIXTURE_PCT_THRESHOLD,
        "external_absolute_threshold": FIXTURE_ABSOLUTE_THRESHOLD,
        "internal_threshold_deviation_reason": None,
        "external_threshold_deviation_reason": None,
        "actor": ACTOR,
        "use_ai_documentation": False,
        "ai_transmission_acknowledged": False,
        "acknowledge_incomplete": False,
    }


def test_case_14_artifact_hashes_and_ooxml_preconditions_are_frozen():
    assert _sha256(WORKBOOK_PATH) == EXPECTED["workbook_sha256"]
    assert _sha256(REFERENCE_PATH) == EXPECTED["reference_sha256"]

    formula_book = openpyxl.load_workbook(WORKBOOK_PATH, data_only=False)
    value_book = openpyxl.load_workbook(WORKBOOK_PATH, data_only=True)
    assert formula_book.calculation.calcMode == "manual"
    assert formula_book["Assumptions"]["C14"].value == pytest.approx(1.061)
    assert formula_book["Board Summary"]["B9"].value == "='Reserve Calculation'!B10"
    assert value_book["Board Summary"]["B8"].value == pytest.approx(148_200_000)
    assert value_book["Board Summary"]["B9"].value == pytest.approx(41_700_000)


def test_case_14_reconstructs_current_inputs_but_never_passes_stale_evidence():
    parsed = parse_workbook(WORKBOOK_PATH.read_bytes())
    assert parsed.workbook_meta.calc_mode == "manual"
    assert detect_anomalies(parsed) == []
    formula_cells = [cell for cell in parsed.cells.values() if cell.formula is not None]
    assert formula_cells
    assert all(cell.calculation_freshness == "stale" for cell in formula_cells)

    result = run_reconciliation(parsed, EXPECTED["authoritative_output_cells"])
    internal = {
        line.label: line for line in result.lines if line.check_type == "excel_vs_python"
    }
    opening = internal["Opening reserve"]
    assert opening.source_value == pytest.approx(65_000_000)
    assert opening.target_value == pytest.approx(65_000_000)
    assert opening.delta == pytest.approx(0)
    assert opening.verdict == "incomplete"
    assert opening.calculation_evidence_status == "stale"

    closing = internal["Closing IBNR"]
    assert closing.source_value == pytest.approx(41_700_000)
    assert closing.target_value == pytest.approx(51_000_000)
    assert closing.delta == pytest.approx(9_300_000)
    assert closing.verdict == "incomplete"
    assert closing.reconstruction_coverage_pct == pytest.approx(100)
    assert set(closing.stale_cell_refs) == {
        "Board Summary!B9",
        "Reserve Calculation!B7",
        "Reserve Calculation!B8",
        "Reserve Calculation!B9",
        "Reserve Calculation!B10",
    }

    ultimate = run_reconciliation(parsed, ["Board Summary!B8"]).lines[0]
    assert ultimate.source_value == pytest.approx(148_200_000)
    assert ultimate.target_value == pytest.approx(157_500_000)
    assert ultimate.delta == pytest.approx(9_300_000)
    assert ultimate.verdict == "incomplete"


def test_case_14_internal_and_external_verdicts_stop_at_gate_3(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    report_id, _, findings, preview = _run_to_gate3(orchestrator)
    assert findings == []
    assert orchestrator.get_context_match_verdict(report_id) == "match"
    assert len(preview.mappings) == 2
    decisions = [
        MappingReviewDecision(mapping_id=mapping.mapping_id, action="approve")
        for mapping in preview.mappings
    ]

    internal_verdict, external_verdict, reviewed = orchestrator.preview_gate3_decisions(
        report_id,
        preview,
        decisions,
        internal_pct_threshold=FIXTURE_PCT_THRESHOLD,
        internal_absolute_threshold=FIXTURE_ABSOLUTE_THRESHOLD,
        external_pct_threshold=FIXTURE_PCT_THRESHOLD,
        external_absolute_threshold=FIXTURE_ABSOLUTE_THRESHOLD,
        actor=ACTOR,
    )
    assert internal_verdict == "incomplete"
    assert external_verdict == "incomplete"

    external = {
        line.label.split(" (REF-")[0]: line
        for line in reviewed.lines
        if line.check_type == "python_vs_accounts"
    }
    assert external["Opening reserve"].delta == pytest.approx(0)
    assert external["Opening reserve"].verdict == "incomplete"
    assert external["Closing IBNR"].source_value == pytest.approx(51_000_000)
    assert external["Closing IBNR"].target_value == pytest.approx(41_700_000)
    assert external["Closing IBNR"].delta == pytest.approx(9_300_000)
    assert external["Closing IBNR"].verdict == "incomplete"
    assert external["Closing IBNR"].calculation_evidence_status == "stale"
    assert external["Closing IBNR"].stale_cell_refs

    with pytest.raises(
        GateBlockedError,
        match="internal_verdict=incomplete, external_verdict=incomplete",
    ):
        orchestrator.submit_gate3_decisions(
            report_id,
            preview,
            mapping_decisions=decisions,
            **_gate3_kwargs(),
        )

    assert orchestrator.get_stage(report_id) == "post_reconciliation"
    with pytest.raises(PipelineStateError, match="expected one of"):
        orchestrator.submit_approval_record(report_id, ACTOR)
    with pytest.raises(PipelineStateError, match="not been assembled"):
        orchestrator.get_report(report_id)


def test_case_14_boundary_is_explicit_in_machine_and_human_evidence():
    guide = (CASE_DIR / "docs" / "CASE_GUIDE.md").read_text(encoding="utf-8")
    combined = (EXPECTED["boundary"] + " " + guide).lower()
    assert "calculation freshness" in combined
    assert "does not opine" in combined
    assert "reserve adequacy" in combined
    assert "reserving methodology" in combined
