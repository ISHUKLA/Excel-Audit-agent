"""End-to-end acceptance for the large clean synthetic IFRS 17-related case."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.anomaly_detector import detect_anomalies
from agents.orchestrator import Orchestrator
from agents.parser import parse_workbook
from core.audit_log import AuditLog
from core.formula_catalogue import SUPPORTED_FUNCTIONS
from core.models import FileContext, MappingReviewDecision
from core.state_store import StateStore
from core.ui_inputs import build_reference_figures
from core.workbook_identity import sha256_bytes
from report.generator import generate_report_pdf

FIXTURES = Path(__file__).parent / "fixtures"
WORKBOOK_PATH = FIXTURES / "ifrs17_workbook_clean.xlsx"
REFERENCE_PATH = FIXTURES / "accounting_reference.csv"
EXPECTED = json.loads((FIXTURES / "ifrs17_expected.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((FIXTURES / "ifrs17_manifest.json").read_text(encoding="utf-8"))
ACTOR = "Isaac Shukla"
NOW = datetime.now(timezone.utc)


def _reference_figures(path: Path = REFERENCE_PATH):
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    first = rows[0]
    return build_reference_figures(
        source_label="Synthetic Q4 2025 trial balance",
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
    return FileContext(
        filename=WORKBOOK_PATH.name,
        description=(
            "Synthetic IFRS 17-related reserve roll-forward and accounting bridge; "
            "spreadsheet reconstruction demonstration only."
        ),
        user_role="actuary",
        entity=EXPECTED["entity"],
        period=EXPECTED["period"],
        currency=EXPECTED["currency"],
        basis=EXPECTED["basis"],
        confirmed_workbook_hash=workbook_hash,
        uploaded_at=NOW,
    )


@pytest.fixture
def pipeline(tmp_path):
    audit_log = AuditLog(str(tmp_path / "audit.db"))
    state_store = StateStore(audit_log.db_path, audit_log=audit_log)
    return Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        documentation_client=None,
        code_version="ifrs17-large-acceptance",
    )


def test_large_fixture_scale_inventory_hash_and_hand_checkable_outputs():
    workbook_bytes = WORKBOOK_PATH.read_bytes()
    parsed = parse_workbook(workbook_bytes)

    assert MANIFEST["formula_cell_count"] >= 5_000
    exercised_functions = set(MANIFEST["supported_functions"])
    assert exercised_functions <= SUPPORTED_FUNCTIONS
    assert set(MANIFEST["formula_count_by_function"]) == exercised_functions
    assert hashlib.sha256(workbook_bytes).hexdigest() == MANIFEST["sha256"]
    assert parsed.workbook_meta.calc_mode == "automatic"
    assert parsed.tab_names == [
        "Assumptions",
        "CohortData",
        "ReserveRollforward",
        "Aggregation",
        "AccountingBridge",
        "Controls",
        "DemoGuide",
    ]
    for output in EXPECTED["hand_checkable_outputs"]:
        assert parsed.cells[output["cell"]].cached_value == pytest.approx(
            output["expected_value"], abs=0.01
        )
    assert detect_anomalies(parsed) == []


def test_clean_large_case_completes_all_four_real_gates_and_generates_pdf(pipeline):
    workbook_bytes = WORKBOOK_PATH.read_bytes()
    workbook_hash = sha256_bytes(workbook_bytes)
    reference_figures = _reference_figures()

    report_id, parsed, findings = pipeline.run(
        workbook_bytes,
        _file_context(workbook_hash),
        reference_figures,
        expected_workbook_hash=workbook_hash,
        context_confirmed=True,
        actor=ACTOR,
    )
    assert pipeline.get_context_match_verdict(report_id) == "match"
    assert pipeline.get_control_total_check(report_id).status == "match"
    assert findings == []

    label, preview = pipeline.submit_gate2_decisions(
        report_id,
        findings,
        EXPECTED["authoritative_outputs"],
        actor=ACTOR,
    )
    assert label == "Preview, pending Gate 3 approval"
    internal_preview = [line for line in preview.lines if line.check_type == "excel_vs_python"]
    assert [line.completeness for line in internal_preview] == ["complete"]
    assert [line.verdict for line in internal_preview] == ["pass"]
    assert preview.mappings and not any(mapping.is_approved for mapping in preview.mappings)

    decisions = [
        MappingReviewDecision(mapping_id=mapping.mapping_id, action="approve")
        for mapping in preview.mappings
    ]
    internal_verdict, external_verdict, final_result = pipeline.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=decisions,
        internal_pct_threshold=0.01,
        internal_absolute_threshold=100.0,
        external_pct_threshold=0.01,
        external_absolute_threshold=100.0,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=False,
        acknowledge_incomplete=False,
    )
    assert internal_verdict == EXPECTED["expected_internal_verdict"]
    assert external_verdict == EXPECTED["expected_external_verdict"]
    assert final_result.verdicts_are_final is True
    assert final_result.unmatched_reference_items == []
    assert final_result.unmapped_python_outputs == []
    assert all(mapping.is_approved and mapping.approved_by == ACTOR for mapping in final_result.mappings)

    pre_approval_report = pipeline.get_report(report_id)
    assert pre_approval_report.internal_verdict == "pass"
    assert pre_approval_report.external_verdict == "pass"
    assert pre_approval_report.report_approval_name is None
    with pytest.raises(ValueError, match="before Gate 4"):
        generate_report_pdf(pre_approval_report, pipeline.get_audit_rows(report_id))

    final_report = pipeline.submit_approval_record(report_id, ACTOR)
    pdf_bytes = generate_report_pdf(final_report, pipeline.get_audit_rows(report_id))
    assert final_report.report_approval_name == ACTOR
    assert final_report.report_approval_role == "cro"
    assert final_report.report_approval_at is not None
    assert pdf_bytes.startswith(b"%PDF")

    gate_numbers = []
    for row in pipeline.get_audit_rows(report_id):
        payload = json.loads(row["payload_json"])
        if payload.get("gate") in {1, 2, 3, 4}:
            gate_numbers.append(payload["gate"])
    assert gate_numbers == [1, 2, 3, 4]
