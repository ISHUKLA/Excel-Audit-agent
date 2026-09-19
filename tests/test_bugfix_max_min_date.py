"""Regression cases for two reported defects, written and run BEFORE any fix
so the failure is on the record:

1. MAX/MIN's argument handling only recognizes a bare cell reference or a
   bare numeric literal per comma-separated argument — an argument that is
   itself an expression (e.g. "A1*2") is silently reduced to the referenced
   cell's raw, unevaluated value.
2. DATE's year argument is passed straight to Python's `date()` constructor,
   which accepts a literal year 24 as-is instead of applying Excel's
   documented 0-1899 -> 1900+year adjustment.

These four cases are intentionally run unmodified, expected to FAIL, before
any fix is written.
"""

from datetime import datetime, timezone

from agents.reconciliation import run_reconciliation
from core.models import CellRecord, ParsedFile, WorkbookMeta

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def cell(ref, formula=None, value=None):
    return CellRecord(
        cell_ref=ref,
        formula=formula,
        cached_value=value,
        data_type="number" if isinstance(value, (int, float)) else "text",
        number_format="General",
        is_error=False,
        error_type=None,
        is_stale=False,
        calculation_freshness="fresh",
    )


def parsed(cells, graph, tabs=("Sheet1",)):
    return ParsedFile(
        tab_names=list(tabs),
        cells=cells,
        named_ranges={},
        external_links=[],
        has_vba=False,
        workbook_meta=WorkbookMeta(calc_mode="automatic", workbook_hash="a" * 64),
        tab_dependency_graph={},
        cell_dependency_graph=graph,
        warnings=[],
    )


def test_max_of_an_expression_argument_and_a_literal():
    """MAX(A1*2, 3) with A1=2 -> expected 4."""
    cells = {
        "Sheet1!A1": cell("Sheet1!A1", value=2.0),
        "Sheet1!B1": cell("Sheet1!B1", formula="=MAX(A1*2,3)", value=4.0),
    }
    graph = {"Sheet1!B1": ["Sheet1!A1"]}
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!B1"]).lines[0]
    assert line.target_value == 4.0


def test_min_of_an_expression_argument_and_a_literal():
    """MIN(A1+10, 30) with A1=2 -> expected 12."""
    cells = {
        "Sheet1!A1": cell("Sheet1!A1", value=2.0),
        "Sheet1!B1": cell("Sheet1!B1", formula="=MIN(A1+10,30)", value=12.0),
    }
    graph = {"Sheet1!B1": ["Sheet1!A1"]}
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!B1"]).lines[0]
    assert line.target_value == 12.0


def test_max_of_two_literal_expressions():
    """MAX(2+3, 1) -> expected 5."""
    cells = {
        "Sheet1!B1": cell("Sheet1!B1", formula="=MAX(2+3,1)", value=5.0),
    }
    graph = {"Sheet1!B1": []}
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!B1"]).lines[0]
    assert line.target_value == 5.0


def test_date_with_a_two_digit_year_applies_excels_1900_offset():
    """DATE(24,1,1) -> expected 1924-01-01 (Excel serial date semantics:
    years 0-1899 map to 1900+year, per Microsoft's DATE function
    specification). Excel serial for 1924-01-01 is 8767."""
    cells = {
        "Sheet1!B1": cell("Sheet1!B1", formula="=DATE(24,1,1)", value=8767.0),
    }
    graph = {"Sheet1!B1": []}
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!B1"]).lines[0]
    assert line.target_value == 8767.0
