"""Work Package 4 — demo case integration and Case 4 acceptance tests.

Uses the real parser, anomaly detector, reconciliation, gates, and report
generator via demo_cases.load_case() and the orchestrator — the same
production path app.py drives. No test in this module invokes LibreOffice,
Microsoft Excel, or a live Anthropic API call.
"""

import hashlib
import json
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace

import openpyxl
import pandas as pd
import pytest

import agents.documentation as documentation_module
import demo_cases
from agents.orchestrator import Orchestrator
from core.audit_log import AuditLog
from core.models import FileContext, MappingReviewDecision
from core.state_store import StateStore
from core.ui_inputs import build_reference_figures, validate_reference_csv_columns
from core.workbook_identity import sha256_bytes
from report.generator import generate_report_pdf

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEMO_DIR = REPO_ROOT / "demo"
OUTPUT_PACK_DIR = REPO_ROOT / "outputs" / "ai2_2026_demo_pack_20260824"
EXPECTED_RESULTS_DIR = DEMO_DIR / "expected_results"

ACTOR = "Isaac Shukla"
NOW = datetime.now(timezone.utc)
DEFAULT_PCT = 0.01
DEFAULT_ABSOLUTE = 100.0


class _FakeMessagesAPI:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text=json.dumps(
                        {
                            "method_summary": "Documents the tab's calculation structure.",
                            "assumptions": ["Human review remains required."],
                            "data_sources": ["Minimized workbook payload"],
                            "anomalies_noted": [],
                            "role_notes": "Review formulas and source-cell lineage.",
                        }
                    ),
                )
            ]
        )


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessagesAPI()


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected(case_number: int) -> dict:
    return json.loads((EXPECTED_RESULTS_DIR / f"case_{case_number}_expected.json").read_text())


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(documentation_module.time, "sleep", lambda _: None)
    db_path = str(tmp_path / "audit.db")
    audit_log = AuditLog(db_path)
    state_store = StateStore(db_path, audit_log=audit_log)
    client = _FakeClient()
    orchestrator = Orchestrator(
        audit_log=audit_log,
        state_store=state_store,
        documentation_client=client,
        code_version="wp4-demo-cases",
    )
    return orchestrator, audit_log, state_store, client


def _case4_reference_figures():
    frame = pd.read_csv(DEMO_DIR / "reference_figures" / "case_4_reference_figures.csv")
    validate_reference_csv_columns(frame.columns)
    return build_reference_figures(
        source_label="Synthetic trial balance",
        entity="Aurora General Insurance SA",
        period="2025-Q4",
        currency="EUR",
        basis="IFRS 17 – synthetic demonstration",
        control_total=-1_400_000.0,
        control_total_confirmed_by_human=True,
        rows=frame.to_dict(orient="records"),
        require_account_number=True,
        uploaded_at=NOW,
    )


def _file_context(case: dict, workbook_hash: str) -> FileContext:
    return FileContext(
        filename="case_4_claims_reserve_roll_forward.xlsx",
        description=case["description"],
        user_role="actuary",
        entity=case["entity"],
        period=case["period"],
        currency=case["currency"],
        basis=case["basis"],
        confirmed_workbook_hash=workbook_hash,
        uploaded_at=NOW,
    )


def _review(findings):
    return [
        finding.model_copy(
            update={
                "human_decision": "confirmed",
                "human_reason": "Reviewed for the Work Package 4 acceptance run.",
                "decided_by": ACTOR,
                "decided_at": NOW,
            }
        )
        for finding in findings
    ]


def _run_case4_to_gate3_preview(orchestrator):
    case = demo_cases.load_case(4)
    workbook_hash = sha256_bytes(case["workbook_bytes"])
    report_id, parsed_file, findings = orchestrator.run(
        case["workbook_bytes"],
        _file_context(case, workbook_hash),
        _case4_reference_figures(),
        expected_workbook_hash=workbook_hash,
        context_confirmed=True,
        actor=ACTOR,
    )
    label, preview = orchestrator.submit_gate2_decisions(
        report_id, _review(findings), ["Controls!B4"], actor=ACTOR
    )
    assert label == "Preview, pending Gate 3 approval"
    return case, report_id, parsed_file, findings, preview


# ---------------------------------------------------------------------------
# 1-4: case listing and asset existence
# ---------------------------------------------------------------------------


def test_list_cases_returns_four_cases():
    cases = demo_cases.list_cases()
    assert [c["number"] for c in cases] == [1, 2, 3, 4]


def test_every_listed_case_loads():
    for case_meta in demo_cases.list_cases():
        case = demo_cases.load_case(case_meta["number"])
        assert case["workbook_bytes"]
        assert case["entity"]


def test_every_documented_asset_exists():
    for path in (
        DEMO_DIR / "workbooks" / "case_1_clean_reserve_calculation.xlsx",
        DEMO_DIR / "workbooks" / "case_2_spreadsheet_control_failures.xlsx",
        DEMO_DIR / "workbooks" / "case_3_accounting_reconciliation_failure.xlsx",
        DEMO_DIR / "workbooks" / "case_4_claims_reserve_roll_forward.xlsx",
        DEMO_DIR / "reference_figures" / "case_1_reference_figures.csv",
        DEMO_DIR / "reference_figures" / "case_3_reference_figures.csv",
        DEMO_DIR / "reference_figures" / "case_4_reference_figures.csv",
        DEMO_DIR / "recalculation_provenance.json",
        OUTPUT_PACK_DIR / "case_4_claims_reserve_roll_forward.xlsx",
        OUTPUT_PACK_DIR / "case_4_reference_figures.csv",
        OUTPUT_PACK_DIR / "recalculation_provenance.json",
        EXPECTED_RESULTS_DIR / "case_1_expected.json",
        EXPECTED_RESULTS_DIR / "case_2_expected.json",
        EXPECTED_RESULTS_DIR / "case_3_expected.json",
        EXPECTED_RESULTS_DIR / "case_4_expected.json",
    ):
        assert path.exists(), path


def test_case_4_context_is_correct():
    case = demo_cases.load_case(4)
    assert case["entity"] == "Aurora General Insurance SA"
    assert case["period"] == "2025-Q4"
    assert case["currency"] == "EUR"
    assert "IFRS 17" in case["basis"]
    assert case["reference_csv_path"] is not None


# ---------------------------------------------------------------------------
# 5-7: reference-line signed orientation
# ---------------------------------------------------------------------------


def test_case_4_reference_line_is_credit_with_non_negative_amount():
    frame = pd.read_csv(DEMO_DIR / "reference_figures" / "case_4_reference_figures.csv")
    row = frame.iloc[0]
    assert row["debit_credit"] == "credit"
    assert row["amount"] >= 0


def test_case_4_signed_amount_equals_negative_1_4_million():
    reference_figures = _case4_reference_figures()
    line = reference_figures.lines[0]
    from core.accounting import signed_reference_amount

    assert signed_reference_amount(line) == pytest.approx(-1_400_000.0)


def test_case_4_signed_control_total_equals_the_reference_population():
    from core.accounting import evaluate_control_total

    reference_figures = _case4_reference_figures()
    check = evaluate_control_total(reference_figures)
    assert check.status == "match"
    assert check.signed_line_total == pytest.approx(-1_400_000.0)


# ---------------------------------------------------------------------------
# 8-13: committed fixture facts (parser-level, no LibreOffice invoked here)
# ---------------------------------------------------------------------------


def test_case_4_workbook_calculation_mode_is_automatic():
    from agents.parser import parse_workbook

    case = demo_cases.load_case(4)
    parsed = parse_workbook(case["workbook_bytes"])
    assert parsed.workbook_meta.calc_mode == "automatic"


def test_case_4_no_formula_cell_is_stale_or_unknown():
    from agents.parser import parse_workbook

    case = demo_cases.load_case(4)
    parsed = parse_workbook(case["workbook_bytes"])
    formula_cells = [c for c in parsed.cells.values() if c.formula is not None]
    assert formula_cells
    for cell in formula_cells:
        assert cell.calculation_freshness == "fresh"


def test_case_4_workbook_has_no_vba_or_external_links():
    wb = openpyxl.load_workbook(
        DEMO_DIR / "workbooks" / "case_4_claims_reserve_roll_forward.xlsx"
    )
    assert wb.vba_archive is None
    assert list(wb.defined_names) == []


def test_case_4_formulas_use_only_the_supported_catalogue():
    from agents.parser import parse_workbook

    case = demo_cases.load_case(4)
    parsed = parse_workbook(case["workbook_bytes"])
    forbidden = ("VLOOKUP", "INDEX", "MATCH", "OFFSET", "INDIRECT", "RAND", "NOW", "TODAY")
    for cell in parsed.cells.values():
        if cell.formula is None:
            continue
        upper = cell.formula.upper()
        for token in forbidden:
            assert token not in upper, (cell.formula, token)


def test_case_4_deterministic_anomaly_findings_are_empty():
    from agents.anomaly_detector import detect_anomalies
    from agents.parser import parse_workbook

    case = demo_cases.load_case(4)
    parsed = parse_workbook(case["workbook_bytes"])
    assert detect_anomalies(parsed) == []


def test_case_4_controls_b4_reconstructs_to_negative_1_4_million():
    from agents.parser import parse_workbook
    from agents.reconciliation import _build_derivation

    case = demo_cases.load_case(4)
    parsed = parse_workbook(case["workbook_bytes"])
    warnings = []
    _, coverage, unsupported, root_value, _ = _build_derivation(
        "Controls!B4", parsed, warnings
    )
    assert coverage == 100.0
    assert unsupported == []
    assert root_value == pytest.approx(-1_400_000.0)


# ---------------------------------------------------------------------------
# 14-19: full pipeline through the orchestrator
# ---------------------------------------------------------------------------


def test_case_4_internal_delta_is_zero_and_verdict_passes(pipeline):
    orchestrator, *_ = pipeline
    _, _, _, _, preview = _run_case4_to_gate3_preview(orchestrator)
    internal_line = next(
        line for line in preview.lines if line.check_type == "excel_vs_python"
    )
    assert internal_line.delta == pytest.approx(0.0)
    assert internal_line.verdict == "pass"
    assert internal_line.completeness == "complete"


def test_case_4_formula_coverage_is_100_percent(pipeline):
    orchestrator, *_ = pipeline
    _, _, _, _, preview = _run_case4_to_gate3_preview(orchestrator)
    internal_line = next(
        line for line in preview.lines if line.check_type == "excel_vs_python"
    )
    assert internal_line.reconstruction_coverage_pct == pytest.approx(100.0)


def test_case_4_external_comparison_is_one_to_one_and_requires_approval(pipeline):
    orchestrator, *_ = pipeline
    _, _, _, _, preview = _run_case4_to_gate3_preview(orchestrator)
    assert len(preview.mappings) == 1
    mapping = preview.mappings[0]
    assert mapping.is_approved is False
    assert preview.unmatched_reference_items == []
    assert preview.unmapped_python_outputs == []


def test_case_4_external_verdict_passes_only_after_mapping_approval(pipeline):
    orchestrator, *_ = pipeline
    _, report_id, _, _, preview = _run_case4_to_gate3_preview(orchestrator)
    mapping = preview.mappings[0]

    internal_verdict, external_verdict, final_result = orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[
            MappingReviewDecision(mapping_id=mapping.mapping_id, action="approve")
        ],
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABSOLUTE,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABSOLUTE,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=False,
        ai_transmission_acknowledged=False,
        acknowledge_incomplete=False,
    )
    assert internal_verdict == "pass"
    assert external_verdict == "pass"
    approved = next(m for m in final_result.mappings if m.mapping_id == mapping.mapping_id)
    assert approved.is_approved is True
    assert approved.approved_by == ACTOR


def test_case_4_can_complete_the_pipeline_with_ai_declined_and_makes_no_provider_call(pipeline):
    orchestrator, audit_log, state_store, client = pipeline
    _, report_id, _, _, preview = _run_case4_to_gate3_preview(orchestrator)
    mapping = preview.mappings[0]

    orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[
            MappingReviewDecision(mapping_id=mapping.mapping_id, action="approve")
        ],
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABSOLUTE,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABSOLUTE,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=False,
        ai_transmission_acknowledged=False,
        acknowledge_incomplete=False,
    )
    assert client.messages.calls == []

    report = orchestrator.get_report(report_id)
    assert report.internal_verdict == "pass"
    assert report.external_verdict == "pass"


def test_case_4_report_generation_remains_gated_by_named_approval(pipeline):
    orchestrator, *_ = pipeline
    _, report_id, _, _, preview = _run_case4_to_gate3_preview(orchestrator)
    mapping = preview.mappings[0]

    orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[
            MappingReviewDecision(mapping_id=mapping.mapping_id, action="approve")
        ],
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABSOLUTE,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABSOLUTE,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=False,
        ai_transmission_acknowledged=False,
        acknowledge_incomplete=False,
    )

    report_before = orchestrator.get_report(report_id)
    assert report_before.report_approval_name is None

    with pytest.raises(ValueError, match="named approval record"):
        generate_report_pdf(report_before, orchestrator.get_audit_rows(report_id))

    final_report = orchestrator.submit_approval_record(report_id, ACTOR, "actuary")
    assert final_report.report_approval_name == ACTOR
    assert final_report.report_approval_at is not None

    pdf_bytes = generate_report_pdf(final_report, orchestrator.get_audit_rows(report_id))
    assert pdf_bytes.startswith(b"%PDF")


# ---------------------------------------------------------------------------
# 20-22: provenance and expected-results consistency
# ---------------------------------------------------------------------------


def test_canonical_and_output_pack_case_4_hashes_match():
    canonical = _sha256(DEMO_DIR / "workbooks" / "case_4_claims_reserve_roll_forward.xlsx")
    output_pack = _sha256(OUTPUT_PACK_DIR / "case_4_claims_reserve_roll_forward.xlsx")
    assert canonical == output_pack


def test_canonical_and_output_pack_provenance_json_match():
    canonical = (DEMO_DIR / "recalculation_provenance.json").read_bytes()
    output_pack = (OUTPUT_PACK_DIR / "recalculation_provenance.json").read_bytes()
    assert canonical == output_pack


def test_case_4_expected_results_json_matches_observed_pipeline(pipeline):
    orchestrator, *_ = pipeline
    expected = _expected(4)

    _, report_id, _, _, preview = _run_case4_to_gate3_preview(orchestrator)
    internal_line = next(
        line for line in preview.lines if line.check_type == "excel_vs_python"
    )
    assert internal_line.delta == pytest.approx(expected["internal_delta"])
    assert internal_line.verdict == expected["expected_internal_verdict"]
    assert internal_line.reconstruction_coverage_pct == pytest.approx(expected["reconstruction_coverage_pct"])
    assert expected["authoritative_output_cells"] == ["Controls!B4"]

    mapping = preview.mappings[0]
    assert expected["mapping"]["cardinality"] == "one-to-one"
    assert expected["mapping"]["requires_human_approval"] is True
    assert mapping.is_approved is False

    internal_verdict, external_verdict, _ = orchestrator.submit_gate3_decisions(
        report_id,
        preview,
        mapping_decisions=[
            MappingReviewDecision(mapping_id=mapping.mapping_id, action="approve")
        ],
        internal_pct_threshold=DEFAULT_PCT,
        internal_absolute_threshold=DEFAULT_ABSOLUTE,
        external_pct_threshold=DEFAULT_PCT,
        external_absolute_threshold=DEFAULT_ABSOLUTE,
        internal_threshold_deviation_reason=None,
        external_threshold_deviation_reason=None,
        actor=ACTOR,
        use_ai_documentation=False,
        ai_transmission_acknowledged=False,
        acknowledge_incomplete=False,
    )
    assert internal_verdict == expected["expected_internal_verdict"]
    assert external_verdict == "pass"


# ---------------------------------------------------------------------------
# 23-24: no regression to Cases 1-3; no dangling filenames in docs
# ---------------------------------------------------------------------------


def test_cases_1_through_3_retain_their_documented_outcomes():
    for number in (1, 2, 3):
        case = demo_cases.load_case(number)
        assert case["workbook_bytes"]
        expected = _expected(number)
        assert expected["case_number"] == number


def test_documentation_contains_no_nonexistent_demo_filename():
    import re

    pattern = re.compile(r"\b(?:case_\d[\w./-]*\.(?:xlsx|csv|json)|0[1-3]_[\w-]+\.xlsx)\b")
    docs = [
        DEMO_DIR / "README.md",
        DEMO_DIR / "workbooks" / "README.md",
        OUTPUT_PACK_DIR / "README.md",
        DEMO_DIR / "expected_results" / "README.md",
    ]
    for doc_path in docs:
        text = doc_path.read_text(encoding="utf-8")
        for match in pattern.findall(text):
            filename = match.split("/")[-1]
            candidates = list(REPO_ROOT.rglob(filename))
            assert candidates, f"{doc_path}: references nonexistent file {match}"
