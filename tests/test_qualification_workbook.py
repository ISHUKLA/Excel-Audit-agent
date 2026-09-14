"""Real-workbook acceptance tests for every declared formula function.

The committed workbook was recalculated once by the engine recorded in its
manifest.  Normal test runs need neither LibreOffice nor any mock: bytes go
through the production parser and production reconciliation path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agents.parser import parse_workbook
from agents.reconciliation import run_reconciliation
from core.formula_catalogue import SUPPORTED_FUNCTIONS

FIXTURES = Path(__file__).parent / "fixtures"
WORKBOOK_PATH = FIXTURES / "qualification_workbook.xlsx"
EXPECTED = json.loads((FIXTURES / "qualification_expected.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((FIXTURES / "qualification_manifest.json").read_text(encoding="utf-8"))
CASES = EXPECTED["cases"]
PARSED = parse_workbook(WORKBOOK_PATH.read_bytes())


def _case_id(case: dict) -> str:
    return case["case_id"]


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_qualified_formula_case_reconstructs_through_production_path(case):
    line = run_reconciliation(PARSED, [case["cell"]]).lines[0]
    cached_value = PARSED.cells[case["cell"]].cached_value

    assert line.completeness == case["expected_status"]
    assert line.verdict == case["expected_verdict"]
    if case["expected_status"] == "complete":
        assert line.target_value == pytest.approx(case["expected_value"])
        assert cached_value == pytest.approx(case["expected_value"])
        assert line.source_value == pytest.approx(case["expected_value"])
        assert line.unsupported_elements == []
    else:
        assert line.target_value is None
        assert line.verdict == "incomplete"


def test_every_catalogue_function_has_a_passing_real_workbook_case():
    covered = {
        function
        for case in CASES
        if case["expected_status"] == "complete"
        for function in case["functions"]
    }
    missing = sorted(SUPPORTED_FUNCTIONS - covered)
    unexpected = sorted(covered - SUPPORTED_FUNCTIONS)

    assert missing == [], f"supported functions without acceptance coverage: {missing}"
    assert unexpected == [], f"acceptance cases claim unsupported functions: {unexpected}"


def test_error_cells_retain_their_recalculated_cached_error_evidence():
    for expected in EXPECTED["error_evidence"]:
        record = PARSED.cells[expected["cell"]]
        assert record.is_error is True, expected["cell"]
        assert record.error_type == expected["expected_error"], expected["cell"]
        assert record.cached_value == expected["expected_error"], expected["cell"]


def test_qualification_manifest_binds_to_the_exact_committed_workbook():
    assert MANIFEST["recalculation_engine"] == "LibreOffice"
    assert MANIFEST["microsoft_excel_tested"] is False
    assert MANIFEST["supported_functions"] == sorted(SUPPORTED_FUNCTIONS)
    assert hashlib.sha256(WORKBOOK_PATH.read_bytes()).hexdigest() == MANIFEST["sha256"]
    assert PARSED.workbook_meta.calc_mode == "automatic"


def test_unrecalculated_source_is_retained_as_generation_evidence_only():
    source = FIXTURES / "qualification_workbook_unrecalculated.xlsx"
    assert source.exists()
    unrecalculated = parse_workbook(source.read_bytes())
    formula_records = [record for record in unrecalculated.cells.values() if record.formula]
    assert formula_records
    assert any(record.cached_value is None for record in formula_records)
