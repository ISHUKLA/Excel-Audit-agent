"""Adversarial argument-arithmetic cases across the functions confirmed
affected by the MAX/MIN/DATE root-cause investigation: nested arithmetic,
a function call nested inside another function's argument, negative
numbers, and a cell reference combined with a literal in one expression.

MAX/MIN were fixed (agents/reconciliation.py's `_max_evaluator`/
`_min_evaluator` now route a non-bare-reference argument through
`_resolve_and_eval_expr`). SUM, MINIFS, and MAXIFS were identified as
sharing the same "extract a reference, discard the surrounding arithmetic"
pattern but were explicitly out of scope for the confirmed fix — this file
documents their CURRENT behavior under adversarial shapes, not an
assumption that they were also fixed.
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


# ---------------------------------------------------------------------------
# MAX/MIN — fixed. Adversarial shapes beyond the original 4 cases.
# ---------------------------------------------------------------------------


def test_max_of_nested_arithmetic_and_a_literal():
    """MAX(A1*2+B1, 3) with A1=2, B1=1 -> 2*2+1=5, max(5,3)=5."""
    cells = {
        "Sheet1!A1": cell("Sheet1!A1", value=2.0),
        "Sheet1!B1": cell("Sheet1!B1", value=1.0),
        "Sheet1!C1": cell("Sheet1!C1", formula="=MAX(A1*2+B1,3)", value=5.0),
    }
    graph = {"Sheet1!C1": ["Sheet1!A1", "Sheet1!B1"]}
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!C1"]).lines[0]
    assert line.target_value == 5.0


def test_min_of_nested_arithmetic_and_a_literal():
    """MIN(A1*2+B1, 30) with A1=2, B1=1 -> 5, min(5,30)=5."""
    cells = {
        "Sheet1!A1": cell("Sheet1!A1", value=2.0),
        "Sheet1!B1": cell("Sheet1!B1", value=1.0),
        "Sheet1!C1": cell("Sheet1!C1", formula="=MIN(A1*2+B1,30)", value=5.0),
    }
    graph = {"Sheet1!C1": ["Sheet1!A1", "Sheet1!B1"]}
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!C1"]).lines[0]
    assert line.target_value == 5.0


def test_max_of_a_nested_function_call_argument():
    """MAX(ROUND(A1,0), 3) with A1=4.4 -> ROUND(4.4,0)=4, max(4,3)=4."""
    cells = {
        "Sheet1!A1": cell("Sheet1!A1", value=4.4),
        "Sheet1!C1": cell("Sheet1!C1", formula="=MAX(ROUND(A1,0),3)", value=4.0),
    }
    graph = {"Sheet1!C1": ["Sheet1!A1"]}
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!C1"]).lines[0]
    assert line.target_value == 4.0


def test_min_of_a_nested_function_call_argument():
    """MIN(ROUND(A1,0), 3) with A1=4.4 -> ROUND=4, min(4,3)=3."""
    cells = {
        "Sheet1!A1": cell("Sheet1!A1", value=4.4),
        "Sheet1!C1": cell("Sheet1!C1", formula="=MIN(ROUND(A1,0),3)", value=3.0),
    }
    graph = {"Sheet1!C1": ["Sheet1!A1"]}
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!C1"]).lines[0]
    assert line.target_value == 3.0


def test_max_with_a_negative_multiplier():
    """MAX(A1*-2, -5) with A1=2 -> 2*-2=-4, max(-4,-5)=-4."""
    cells = {
        "Sheet1!A1": cell("Sheet1!A1", value=2.0),
        "Sheet1!C1": cell("Sheet1!C1", formula="=MAX(A1*-2,-5)", value=-4.0),
    }
    graph = {"Sheet1!C1": ["Sheet1!A1"]}
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!C1"]).lines[0]
    assert line.target_value == -4.0


def test_min_with_a_negative_multiplier():
    """MIN(A1*-2, -5) with A1=2 -> -4, min(-4,-5)=-5."""
    cells = {
        "Sheet1!A1": cell("Sheet1!A1", value=2.0),
        "Sheet1!C1": cell("Sheet1!C1", formula="=MIN(A1*-2,-5)", value=-5.0),
    }
    graph = {"Sheet1!C1": ["Sheet1!A1"]}
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!C1"]).lines[0]
    assert line.target_value == -5.0


def test_max_of_a_reference_and_literal_combined_in_one_expression():
    """MAX(A1*3-2, 10) with A1=5 -> 5*3-2=13, max(13,10)=13."""
    cells = {
        "Sheet1!A1": cell("Sheet1!A1", value=5.0),
        "Sheet1!C1": cell("Sheet1!C1", formula="=MAX(A1*3-2,10)", value=13.0),
    }
    graph = {"Sheet1!C1": ["Sheet1!A1"]}
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!C1"]).lines[0]
    assert line.target_value == 13.0


# ---------------------------------------------------------------------------
# SUM — same root-cause pattern identified but explicitly NOT fixed.
# ---------------------------------------------------------------------------


def test_sum_of_nested_arithmetic_documents_current_unfixed_behavior():
    """SUM(A1*2+B1, C1) with A1=2, B1=1, C1=10 -> correct answer is
    2*2+1+10=15. SUM was flagged as sharing MAX/MIN's root cause but was
    NOT part of the confirmed fix scope — this records its current,
    unfixed behavior rather than assuming it was fixed alongside MAX/MIN."""
    cells = {
        "Sheet1!A1": cell("Sheet1!A1", value=2.0),
        "Sheet1!B1": cell("Sheet1!B1", value=1.0),
        "Sheet1!C1": cell("Sheet1!C1", value=10.0),
        "Sheet1!D1": cell("Sheet1!D1", formula="=SUM(A1*2+B1,C1)", value=15.0),
    }
    graph = {"Sheet1!D1": ["Sheet1!A1", "Sheet1!B1", "Sheet1!C1"]}
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!D1"]).lines[0]
    assert line.target_value == 15.0


# ---------------------------------------------------------------------------
# MINIFS/MAXIFS — same root-cause pattern in the criteria-value argument,
# also NOT part of the confirmed fix scope. Prior investigation showed
# these fail CLOSED (unsupported/incomplete), not silently wrong; this
# records that same behavior under a new shape.
# ---------------------------------------------------------------------------


def test_minifs_with_an_arithmetic_criteria_value_documents_current_behavior():
    """MINIFS(B1:B3, A1:A3, D1*2) — criteria threshold should resolve to
    10 (D1=5, *2). Two rows match (A1=10 -> B1=100; A3=10 -> B3=50), so
    the correct answer is min(100,50)=50."""
    cells = {
        "Sheet1!A1": cell("Sheet1!A1", value=10.0),
        "Sheet1!A2": cell("Sheet1!A2", value=20.0),
        "Sheet1!A3": cell("Sheet1!A3", value=10.0),
        "Sheet1!B1": cell("Sheet1!B1", value=100.0),
        "Sheet1!B2": cell("Sheet1!B2", value=200.0),
        "Sheet1!B3": cell("Sheet1!B3", value=50.0),
        "Sheet1!D1": cell("Sheet1!D1", value=5.0),
        "Sheet1!E1": cell(
            "Sheet1!E1", formula="=_xlfn.MINIFS(B1:B3,A1:A3,D1*2)", value=50.0
        ),
    }
    graph = {
        "Sheet1!E1": [
            "Sheet1!A1", "Sheet1!A2", "Sheet1!A3",
            "Sheet1!B1", "Sheet1!B2", "Sheet1!B3", "Sheet1!D1",
        ]
    }
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!E1"]).lines[0]
    assert line.target_value == 50.0


def test_maxifs_with_an_arithmetic_criteria_value_documents_current_behavior():
    """MAXIFS(B1:B3, A1:A3, D1*2) — same setup as MINIFS above; the
    correct answer is max(100,50)=100."""
    cells = {
        "Sheet1!A1": cell("Sheet1!A1", value=10.0),
        "Sheet1!A2": cell("Sheet1!A2", value=20.0),
        "Sheet1!A3": cell("Sheet1!A3", value=10.0),
        "Sheet1!B1": cell("Sheet1!B1", value=100.0),
        "Sheet1!B2": cell("Sheet1!B2", value=200.0),
        "Sheet1!B3": cell("Sheet1!B3", value=50.0),
        "Sheet1!D1": cell("Sheet1!D1", value=5.0),
        "Sheet1!E1": cell(
            "Sheet1!E1", formula="=_xlfn.MAXIFS(B1:B3,A1:A3,D1*2)", value=100.0
        ),
    }
    graph = {
        "Sheet1!E1": [
            "Sheet1!A1", "Sheet1!A2", "Sheet1!A3",
            "Sheet1!B1", "Sheet1!B2", "Sheet1!B3", "Sheet1!D1",
        ]
    }
    line = run_reconciliation(parsed(cells, graph), ["Sheet1!E1"]).lines[0]
    assert line.target_value == 100.0
