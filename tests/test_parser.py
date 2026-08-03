"""Tests for agents/parser.py: clean-fixture and messy-input Excel parsing."""

import openpyxl
from openpyxl.workbook.defined_name import DefinedName

from agents.parser import parse_workbook


def _make_clean_workbook(path):
    wb = openpyxl.Workbook()
    summary = wb.active
    summary.title = "Summary"
    data = wb.create_sheet("Data")

    data["A1"] = 100
    summary["B1"] = "=Data!A1*2"

    wb.save(path)


def _make_workbook_with_scoped_names(path):
    wb = openpyxl.Workbook()
    tab_a = wb.active
    tab_a.title = "TabA"
    tab_a["B5"] = 1.75
    tab_b = wb.create_sheet("TabB")
    tab_b["B5"] = 1.80

    tab_a.defined_names["taux_technique"] = DefinedName(
        "taux_technique", attr_text="TabA!$B$5"
    )
    tab_b.defined_names["taux_technique"] = DefinedName(
        "taux_technique", attr_text="TabB!$B$5"
    )

    wb.save(path)


def _make_messy_workbook(path):
    wb = openpyxl.Workbook()
    blank = wb.active
    blank.title = "Blank"

    data = wb.create_sheet("Data")
    data["A1"] = "#REF!"
    data["B1"] = "1,234"

    provisions = wb.create_sheet("Provisions")
    provisions["A1"] = 50

    provisions_dup = wb.create_sheet("provisions ")
    provisions_dup["A1"] = 60

    wb.save(path)


def test_parses_clean_workbook(tmp_path):
    path = tmp_path / "clean.xlsx"
    _make_clean_workbook(path)

    result = parse_workbook(str(path))

    assert set(result.tab_names) == {"Summary", "Data"}
    assert result.cells["Summary!B1"] == "=Data!A1*2"
    assert result.dependency_graph["Summary"] == ["Data"]
    assert result.has_vba is False

    # Literal cells: cached_values mirrors cells (no formula to distinguish it from).
    assert result.cached_values["Data!A1"] == 100
    # Formula cells: cells holds the formula text, cached_values holds Excel's
    # last computed result for it (None here since openpyxl doesn't evaluate
    # formulas -- a real Excel-saved file would have a number).
    assert result.cells["Summary!B1"] != result.cached_values["Summary!B1"]


def test_parses_messy_workbook_without_raising(tmp_path):
    path = tmp_path / "messy.xlsx"
    _make_messy_workbook(path)

    result = parse_workbook(str(path))

    assert "Blank" in result.tab_names
    assert not any(key.startswith("Blank!") for key in result.cells)

    assert result.cells["Data!A1"] == "#REF!"
    assert result.cells["Data!B1"] == "1,234"

    warnings_text = " | ".join(result.warnings)
    assert "#REF!" in warnings_text
    assert "number stored as text" in warnings_text
    assert "look like duplicates" in warnings_text


def test_captures_sheet_scoped_named_ranges_separately(tmp_path):
    path = tmp_path / "scoped_names.xlsx"
    _make_workbook_with_scoped_names(path)

    result = parse_workbook(str(path))

    assert result.named_ranges["TabA::taux_technique"] == "TabA!$B$5"
    assert result.named_ranges["TabB::taux_technique"] == "TabB!$B$5"
