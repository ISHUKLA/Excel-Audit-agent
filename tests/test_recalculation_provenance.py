"""Tests for the build-time synthetic-fixture recalculation and its provenance
record (Work Package 2: stale calculation evidence must fail closed).

These tests inspect only committed static files — the fixtures themselves,
the demo/output-pack workbooks, and demo/recalculation_provenance.json. None
of them invoke LibreOffice, Microsoft Excel, or any other recalculation
engine; the recalculation that produced these files was a one-time, manual,
build-time step, not something the automated suite reproduces.
"""

import hashlib
import json
import pathlib

from agents.parser import parse_workbook

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
DEMO_WORKBOOKS_DIR = REPO_ROOT / "demo" / "workbooks"
OUTPUT_PACK_DIR = REPO_ROOT / "outputs" / "ai2_2026_demo_pack_20260824"
PROVENANCE_PATH = REPO_ROOT / "demo" / "recalculation_provenance.json"
OUTPUT_PACK_PROVENANCE_PATH = OUTPUT_PACK_DIR / "recalculation_provenance.json"


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_provenance() -> dict:
    return json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))


def test_recalculated_clean_fixture_reports_automatic_and_is_not_flagged_stale():
    parsed = parse_workbook((FIXTURES_DIR / "clean.xlsx").read_bytes())
    assert parsed.workbook_meta.calc_mode == "automatic"
    formula_cells = [c for c in parsed.cells.values() if c.formula is not None]
    assert formula_cells
    for cell in formula_cells:
        assert cell.calculation_freshness == "fresh"
        assert cell.is_stale is False


def test_recalculated_reserves_fixture_reports_automatic_and_preserves_values():
    parsed = parse_workbook((FIXTURES_DIR / "reserves.xlsx").read_bytes())
    assert parsed.workbook_meta.calc_mode == "automatic"
    provenance = _load_provenance()
    entry = next(
        w for w in provenance["workbooks"] if w["relative_path"] == "tests/fixtures/reserves.xlsx"
    )
    assert entry["cached_numeric_values_unchanged"] is True
    assert entry["calculation_mode_after"] == "automatic"


def test_all_three_canonical_demo_workbooks_report_automatic_mode():
    for name in (
        "case_1_clean_reserve_calculation.xlsx",
        "case_2_spreadsheet_control_failures.xlsx",
        "case_3_accounting_reconciliation_failure.xlsx",
    ):
        parsed = parse_workbook((DEMO_WORKBOOKS_DIR / name).read_bytes())
        assert parsed.workbook_meta.calc_mode == "automatic", name


def test_canonical_and_output_pack_copies_have_identical_hashes():
    for name in (
        "case_1_clean_reserve_calculation.xlsx",
        "case_2_spreadsheet_control_failures.xlsx",
        "case_3_accounting_reconciliation_failure.xlsx",
    ):
        canonical_hash = _sha256(DEMO_WORKBOOKS_DIR / name)
        output_pack_hash = _sha256(OUTPUT_PACK_DIR / name)
        assert canonical_hash == output_pack_hash, name


def test_provenance_contains_the_actual_current_workbook_hashes():
    provenance = _load_provenance()
    for entry in provenance["workbooks"]:
        actual_hash = _sha256(REPO_ROOT / entry["relative_path"])
        assert actual_hash == entry["post_recalculation_sha256"], entry["relative_path"]


def test_provenance_formula_manifest_hashes_before_and_after_are_recorded_equal_or_explained():
    provenance = _load_provenance()
    for entry in provenance["workbooks"]:
        if entry["formula_manifest_sha256_before"] != entry["formula_manifest_sha256_after"]:
            # Any manifest-hash change must be explained as semantically
            # neutral — never left silent.
            assert entry["formula_manifest_note"], entry["relative_path"]
            assert "formula_count_before" in entry and "formula_count_after" in entry
            assert entry["formula_count_before"] == entry["formula_count_after"]
        assert entry["cached_numeric_values_unchanged"] is True


def test_provenance_does_not_claim_excel_equivalence_or_runtime_recalculation():
    provenance = _load_provenance()
    limitations_text = " ".join(provenance["limitations"]).lower()
    assert "not proof of microsoft excel" in limitations_text or "not proof of microsoft excel-semantic equivalence" in limitations_text
    assert "build-time" in limitations_text
    assert "never invokes" in limitations_text or "not runtime" in limitations_text or "never recalculated by it" in limitations_text
    assert "real or sensitive data" in limitations_text


def test_provenance_two_copies_are_byte_identical():
    assert PROVENANCE_PATH.read_bytes() == OUTPUT_PACK_PROVENANCE_PATH.read_bytes()


def test_no_test_file_invokes_a_recalculation_engine():
    """recalculate_workbook() exists in fixture_helpers.py for manual,
    one-time fixture generation only. No test file may call it, and no test
    file may shell out to soffice/libreoffice directly."""
    # Mentioning "LibreOffice" in a comment or docstring (e.g. explaining how a
    # static fixture was originally produced) is fine and expected; actually
    # invoking it is not. This checks for the real gateway calls, not the word.
    tests_dir = REPO_ROOT / "tests"
    for path in tests_dir.glob("test_*.py"):
        if path.name == "test_recalculation_provenance.py":
            continue  # this file's own docstring names the forbidden call
        source = path.read_text(encoding="utf-8")
        assert "recalculate_workbook(" not in source, path.name
        assert "soffice" not in source, path.name
        assert "subprocess" not in source, path.name


def test_arbitrary_upload_is_described_only_as_not_flagged_stale_in_app_and_readme():
    """The application must never claim an uploaded workbook was proven
    freshly recalculated — only that no staleness indicator was detected."""
    app_source = (REPO_ROOT / "app.py").read_text(encoding="utf-8").lower()
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8").lower()

    assert "not flagged stale" in app_source
    assert "not flagged stale" in readme
    assert "never recalculated by it" in readme or "never recalculates an uploaded workbook" in readme
