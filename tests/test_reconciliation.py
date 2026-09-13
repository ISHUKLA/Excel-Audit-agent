"""Tests for agents/reconciliation.py — Agent 3.

The tests that matter most here are the ones proving Pass 2 cannot approve
anything on its own, and that the two completeness directions are genuinely two
different questions rather than the same one asked twice.
"""

from datetime import datetime, timezone

import pytest

from agents.reconciliation import run_reconciliation
from core.models import CellRecord, ParsedFile, ReferenceFigureLine, ReferenceFigures, WorkbookMeta

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


def cell(ref, formula=None, value=None, stale=False, freshness=None):
    if freshness is None:
        freshness = "stale" if stale else "fresh"
    return CellRecord(
        cell_ref=ref,
        formula=formula,
        cached_value=value,
        data_type="number" if isinstance(value, (int, float)) else "text",
        number_format="General",
        is_error=False,
        error_type=None,
        is_stale=stale or freshness != "fresh",
        calculation_freshness=freshness,
    )


def parsed(cells, graph, tabs=("Provisions",)):
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


def simple_sum_workbook(total_cached=100.0, formula="=SUM(C1:C4)"):
    """C1..C4 = 10, 20, 30, 40. C5 sums them to 100."""
    cells = {
        "Provisions!B5": cell("Provisions!B5", value="Technical provisions"),
        "Provisions!C1": cell("Provisions!C1", value=10.0),
        "Provisions!C2": cell("Provisions!C2", value=20.0),
        "Provisions!C3": cell("Provisions!C3", value=30.0),
        "Provisions!C4": cell("Provisions!C4", value=40.0),
        "Provisions!C5": cell("Provisions!C5", formula=formula, value=total_cached),
    }
    graph = {
        "Provisions!C5": ["Provisions!C1", "Provisions!C2", "Provisions!C3", "Provisions!C4"],
        "Provisions!C1": [],
        "Provisions!C2": [],
        "Provisions!C3": [],
        "Provisions!C4": [],
    }
    return parsed(cells, graph)


def reference(lines, **overrides):
    defaults = dict(
        source_label="Q4 trial balance",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        lines=lines,
        uploaded_at=NOW,
    )
    return ReferenceFigures(**{**defaults, **overrides})


def gl_line(line_id, label, amount, **overrides):
    defaults = dict(
        line_id=line_id,
        label=label,
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        ledger_source="SAP FI",
        debit_credit="debit",
        amount=amount,
    )
    return ReferenceFigureLine(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# return shape
# ---------------------------------------------------------------------------


def test_returns_a_reconciliation_result_not_a_tuple():
    result = run_reconciliation(simple_sum_workbook(), ["Provisions!C5"])
    assert not isinstance(result, tuple)
    assert hasattr(result, "lines") and hasattr(result, "mappings")
    assert hasattr(result, "unmatched_reference_items")
    assert hasattr(result, "unmapped_python_outputs")


def test_verdicts_are_never_final_coming_out_of_agent_3():
    """Every verdict here is a preview against thresholds nobody has approved."""
    result = run_reconciliation(simple_sum_workbook(), ["Provisions!C5"])
    assert result.verdicts_are_final is False


def test_accounts_comparison_applies_debit_and_credit_orientation():
    debit = run_reconciliation(
        simple_sum_workbook(total_cached=100.0),
        ["Provisions!C5"],
        reference([gl_line("D", "Technical provisions", 100.0, debit_credit="debit")]),
    ).lines[-1]
    credit = run_reconciliation(
        simple_sum_workbook(total_cached=-100.0, formula="=-SUM(C1:C4)"),
        ["Provisions!C5"],
        reference([gl_line("C", "Technical provisions", 100.0, debit_credit="credit")]),
    ).lines[-1]

    assert debit.target_value == 100.0 and debit.delta == 0.0
    assert credit.target_value == -100.0 and credit.delta == 0.0


# ---------------------------------------------------------------------------
# Pass 1 — reconstruction
# ---------------------------------------------------------------------------


def test_a_fully_supported_chain_reconstructs_and_passes():
    result = run_reconciliation(simple_sum_workbook(), ["Provisions!C5"])
    line = result.lines[0]

    assert line.check_type == "excel_vs_python"
    assert line.source_value == 100.0
    assert line.target_value == 100.0
    assert line.delta == 0.0
    assert line.completeness == "complete"
    assert line.reconstruction_coverage_pct == 100.0
    assert line.verdict == "pass"
    assert line.unsupported_elements == []


def test_the_derivation_chain_is_attached_to_the_line():
    line = run_reconciliation(simple_sum_workbook(), ["Provisions!C5"]).lines[0]
    refs = {step.cell_ref for step in line.derivation}
    assert "Provisions!C5" in refs
    assert "Provisions!C1" in refs
    assert all(step.is_supported for step in line.derivation)


def test_a_disagreement_between_excel_and_python_is_detected():
    """Excel says 999, the formula actually sums to 100."""
    result = run_reconciliation(simple_sum_workbook(total_cached=999.0), ["Provisions!C5"])
    line = result.lines[0]

    assert line.source_value == 999.0
    assert line.target_value == 100.0
    assert line.delta == 899.0
    assert line.verdict == "block"


def test_arithmetic_operators_and_parentheses_are_supported():
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=10.0),
        "Provisions!A2": cell("Provisions!A2", value=4.0),
        "Provisions!A3": cell("Provisions!A3", formula="=(A1-A2)*2/3", value=4.0),
    }
    graph = {"Provisions!A3": ["Provisions!A1", "Provisions!A2"], "Provisions!A1": [], "Provisions!A2": []}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!A3"]).lines[0]
    assert line.target_value == pytest.approx(4.0)


def test_unary_minus_is_supported():
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=10.0),
        "Provisions!A2": cell("Provisions!A2", formula="=-A1", value=-10.0),
    }
    line = run_reconciliation(
        parsed(cells, {"Provisions!A2": ["Provisions!A1"], "Provisions!A1": []}), ["Provisions!A2"]
    ).lines[0]
    assert line.target_value == -10.0


def test_absolute_and_relative_references_resolve_identically():
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=7.0),
        "Provisions!A2": cell("Provisions!A2", formula="=$A$1*2", value=14.0),
    }
    line = run_reconciliation(
        parsed(cells, {"Provisions!A2": ["Provisions!A1"], "Provisions!A1": []}), ["Provisions!A2"]
    ).lines[0]
    assert line.target_value == 14.0
    assert line.verdict == "pass"


def test_a_cross_tab_reference_resolves():
    cells = {
        "Inputs!A1": cell("Inputs!A1", value=50.0),
        "Provisions!C5": cell("Provisions!C5", formula="=Inputs!A1*2", value=100.0),
    }
    graph = {"Provisions!C5": ["Inputs!A1"], "Inputs!A1": []}
    line = run_reconciliation(parsed(cells, graph, tabs=("Inputs", "Provisions")), ["Provisions!C5"]).lines[0]
    assert line.target_value == 100.0


def test_a_stale_source_value_is_warned_about_not_silently_compared():
    warnings = []
    cells = simple_sum_workbook().cells
    cells["Provisions!C5"] = cell("Provisions!C5", formula="=SUM(C1:C4)", value=100.0, stale=True)
    graph = simple_sum_workbook().cell_dependency_graph

    run_reconciliation(parsed(cells, graph), ["Provisions!C5"], warnings=warnings)
    assert any("stale" in w for w in warnings)


# ---------------------------------------------------------------------------
# Work Package 2 — stale calculation evidence must fail closed
# ---------------------------------------------------------------------------


def _stale_workbook(freshness="stale"):
    workbook = simple_sum_workbook()
    cells = dict(workbook.cells)
    cells["Provisions!C5"] = cell(
        "Provisions!C5", formula="=SUM(C1:C4)", value=100.0, freshness=freshness
    )
    return parsed(cells, workbook.cell_dependency_graph)


def test_stale_authoritative_output_with_zero_delta_is_incomplete():
    line = run_reconciliation(_stale_workbook(), ["Provisions!C5"]).lines[0]
    assert line.delta == 0.0
    assert line.calculation_evidence_status == "stale"
    assert line.verdict == "incomplete"
    # Numerical evidence remains visible even though the verdict is capped.
    assert line.source_value == 100.0
    assert line.target_value == 100.0
    assert line.reconstruction_coverage_pct == 100.0


def test_stale_authoritative_output_with_zero_thresholds_is_incomplete():
    line = run_reconciliation(
        _stale_workbook(), ["Provisions!C5"], pct_threshold=0.0, absolute_threshold=0.0
    ).lines[0]
    assert line.verdict == "incomplete"


def test_stale_authoritative_output_with_generous_thresholds_is_incomplete():
    line = run_reconciliation(
        _stale_workbook(), ["Provisions!C5"], pct_threshold=0.5, absolute_threshold=1_000_000.0
    ).lines[0]
    assert line.verdict == "incomplete"


def test_unknown_freshness_authoritative_output_is_also_incomplete():
    line = run_reconciliation(_stale_workbook(freshness="unknown"), ["Provisions!C5"]).lines[0]
    assert line.calculation_evidence_status == "unknown"
    assert line.verdict == "incomplete"


def test_fresh_looking_output_with_a_stale_dependency_is_incomplete():
    """The root cell (C5) itself has a cached value under automatic mode — it
    looks fresh on its own. One of its dependencies (C2) is a formula cell
    whose own cached value was never confirmed. The line must still be capped,
    per the conservative chain-wide freshness policy."""
    cells = {
        "Provisions!C1": cell("Provisions!C1", value=10.0),
        "Provisions!C2": cell("Provisions!C2", formula="=C1*2", value=20.0, stale=True),
        "Provisions!C3": cell("Provisions!C3", value=30.0),
        "Provisions!C5": cell("Provisions!C5", formula="=SUM(C1:C3)", value=60.0),
    }
    graph = {
        "Provisions!C5": ["Provisions!C1", "Provisions!C2", "Provisions!C3"],
        "Provisions!C2": ["Provisions!C1"],
        "Provisions!C1": [],
        "Provisions!C3": [],
    }
    line = run_reconciliation(parsed(cells, graph), ["Provisions!C5"]).lines[0]
    assert line.calculation_evidence_status == "stale"
    assert line.stale_cell_refs == ["Provisions!C2"]
    assert line.verdict == "incomplete"
    # Python's own reconstruction is unaffected — it independently recomputed
    # C2 rather than trusting its cached value — so the number itself is not
    # wrong; only the verdict is capped, per the class-level design note.
    assert line.target_value == 60.0


def test_multiple_stale_dependency_cells_are_identified_without_duplication():
    cells = {
        "Provisions!C1": cell("Provisions!C1", formula="=1+1", value=2.0, stale=True),
        "Provisions!C2": cell("Provisions!C2", formula="=2+2", value=4.0, freshness="unknown"),
        "Provisions!C3": cell("Provisions!C3", value=10.0),
        "Provisions!C5": cell("Provisions!C5", formula="=C1+C2+C3", value=16.0, stale=True),
    }
    graph = {
        "Provisions!C5": ["Provisions!C1", "Provisions!C2", "Provisions!C3"],
        "Provisions!C1": [],
        "Provisions!C2": [],
        "Provisions!C3": [],
    }
    line = run_reconciliation(parsed(cells, graph), ["Provisions!C5"]).lines[0]
    # Root is "stale" (the stronger claim), even though a dependency is only
    # "unknown" — and every affected ref appears exactly once.
    assert line.calculation_evidence_status == "stale"
    assert sorted(line.stale_cell_refs) == ["Provisions!C1", "Provisions!C2", "Provisions!C5"]
    assert len(line.stale_cell_refs) == len(set(line.stale_cell_refs))


def test_mixed_fresh_and_stale_authoritative_outputs_only_affects_the_stale_one():
    cells = dict(simple_sum_workbook().cells)
    cells["Provisions!D1"] = cell("Provisions!D1", formula="=C1*2", value=20.0, stale=True)
    graph = dict(simple_sum_workbook().cell_dependency_graph)
    graph["Provisions!D1"] = ["Provisions!C1"]

    result = run_reconciliation(parsed(cells, graph), ["Provisions!C5", "Provisions!D1"])
    by_label = {line.label: line for line in result.lines}
    fresh_line = next(l for l in result.lines if "D1" not in l.stale_cell_refs and l.calculation_evidence_status == "fresh")
    stale_line = next(l for l in result.lines if l.calculation_evidence_status == "stale")

    assert fresh_line.verdict == "pass"
    assert stale_line.verdict == "incomplete"


def test_external_accounts_equality_cannot_pass_on_stale_workbook_evidence():
    """The accounts figure matches Python's reconstruction exactly, but the
    Python line it came from rests on a stale authoritative-output cell."""
    result = run_reconciliation(
        _stale_workbook(),
        ["Provisions!C5"],
        reference([gl_line("GL-001", "Technical provisions", 100.0, debit_credit="debit")]),
    )
    external_line = next(line for line in result.lines if line.check_type == "python_vs_accounts")
    assert external_line.delta == 0.0
    assert external_line.calculation_evidence_status == "stale"
    assert external_line.verdict == "incomplete"


def test_a_fully_fresh_clean_case_still_passes():
    """Regression guard: nothing about the freshness cap changes a genuinely
    fresh line's verdict."""
    line = run_reconciliation(simple_sum_workbook(), ["Provisions!C5"]).lines[0]
    assert line.calculation_evidence_status == "fresh"
    assert line.stale_cell_refs == []
    assert line.verdict == "pass"


def test_a_blank_cell_inside_a_sum_is_zero_and_warned_about():
    cells = {
        "Provisions!C1": cell("Provisions!C1", value=10.0),
        "Provisions!C2": cell("Provisions!C2", value=None),
        "Provisions!C3": cell("Provisions!C3", formula="=SUM(C1:C2)", value=10.0),
    }
    graph = {"Provisions!C3": ["Provisions!C1", "Provisions!C2"], "Provisions!C1": [], "Provisions!C2": []}
    warnings = []
    line = run_reconciliation(parsed(cells, graph), ["Provisions!C3"], warnings=warnings).lines[0]

    assert line.target_value == 10.0
    assert any("treated as 0" in w for w in warnings)


# ---------------------------------------------------------------------------
# catalogue / evaluator parity guard
# ---------------------------------------------------------------------------


def test_every_supported_function_has_an_evaluator_and_vice_versa():
    """The drift guard: core/formula_catalogue.py's catalogue and
    agents/reconciliation.py's dispatch table must name exactly the same
    functions. A function declared supported without something able to
    compute it (or an evaluator for a function the catalogue doesn't know
    about) is exactly the gap this test exists to make impossible to ship."""
    from agents.reconciliation import _EVALUATORS
    from core.formula_catalogue import SUPPORTED_FUNCTIONS

    assert set(_EVALUATORS) == SUPPORTED_FUNCTIONS


# ---------------------------------------------------------------------------
# Group A: ABS, INT
# ---------------------------------------------------------------------------


def _single_cell_workbook(formula, cached_value, referenced_value=None):
    """One formula cell, optionally depending on one plain value cell."""
    cells = {
        "Provisions!C1": cell("Provisions!C1", formula=formula, value=cached_value),
    }
    graph = {"Provisions!C1": []}
    if referenced_value is not None:
        cells["Provisions!B1"] = cell("Provisions!B1", value=referenced_value)
        graph["Provisions!C1"] = ["Provisions!B1"]
    return parsed(cells, graph)


def test_abs_of_a_positive_literal():
    line = run_reconciliation(_single_cell_workbook("=ABS(5)", 5.0), ["Provisions!C1"]).lines[0]
    assert line.target_value == 5.0
    assert line.completeness == "complete"


def test_abs_of_a_negative_literal():
    line = run_reconciliation(_single_cell_workbook("=ABS(-5)", 5.0), ["Provisions!C1"]).lines[0]
    assert line.target_value == 5.0


def test_abs_of_zero():
    line = run_reconciliation(_single_cell_workbook("=ABS(0)", 0.0), ["Provisions!C1"]).lines[0]
    assert line.target_value == 0.0


def test_abs_of_a_decimal():
    line = run_reconciliation(_single_cell_workbook("=ABS(-3.25)", 3.25), ["Provisions!C1"]).lines[0]
    assert line.target_value == 3.25


def test_abs_of_a_cell_reference():
    line = run_reconciliation(
        _single_cell_workbook("=ABS(B1)", 5.0, referenced_value=-5.0), ["Provisions!C1"]
    ).lines[0]
    assert line.target_value == 5.0


def test_int_of_a_positive_decimal_rounds_toward_negative_infinity():
    line = run_reconciliation(_single_cell_workbook("=INT(8.9)", 8.0), ["Provisions!C1"]).lines[0]
    assert line.target_value == 8.0


def test_int_of_a_negative_decimal_rounds_toward_negative_infinity_not_toward_zero():
    """The trap: Excel's INT(-8.9) is -9, NOT -8. INT is floor, not truncation —
    getting this backwards is a sign-dependent bug that only shows up on
    negative inputs, exactly the kind of asymmetry this catalogue's
    correctness notes exist to catch."""
    line = run_reconciliation(_single_cell_workbook("=INT(-8.9)", -9.0), ["Provisions!C1"]).lines[0]
    assert line.target_value == -9.0


def test_int_of_zero():
    line = run_reconciliation(_single_cell_workbook("=INT(0)", 0.0), ["Provisions!C1"]).lines[0]
    assert line.target_value == 0.0


def test_int_of_an_exact_integer_is_unchanged():
    line = run_reconciliation(_single_cell_workbook("=INT(4)", 4.0), ["Provisions!C1"]).lines[0]
    assert line.target_value == 4.0


def test_int_of_a_cell_reference():
    line = run_reconciliation(
        _single_cell_workbook("=INT(B1)", -9.0, referenced_value=-8.9), ["Provisions!C1"]
    ).lines[0]
    assert line.target_value == -9.0


def test_abs_nested_inside_sum():
    """The innermost-out unwrapping is exercised by nesting a Group A function
    inside the pre-existing SUM support: SUM(ABS(C1), C2)."""
    cells = {
        "Provisions!C1": cell("Provisions!C1", value=-10.0),
        "Provisions!C2": cell("Provisions!C2", value=20.0),
        "Provisions!C3": cell("Provisions!C3", formula="=SUM(ABS(C1),C2)", value=30.0),
    }
    graph = {"Provisions!C3": ["Provisions!C1", "Provisions!C2"], "Provisions!C1": [], "Provisions!C2": []}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!C3"]).lines[0]
    assert line.target_value == 30.0
    assert line.completeness == "complete"


# ---------------------------------------------------------------------------
# Group B: ROUND, ROUNDUP, ROUNDDOWN, CEILING, FLOOR
# ---------------------------------------------------------------------------


def test_round_positive_half_tie_reconstructs():
    line = run_reconciliation(_single_cell_workbook("=ROUND(2.5,0)", 3.0), ["Provisions!C1"]).lines[0]
    assert line.target_value == 3.0


def test_round_negative_half_tie_reconstructs():
    line = run_reconciliation(_single_cell_workbook("=ROUND(-2.5,0)", -3.0), ["Provisions!C1"]).lines[0]
    assert line.target_value == -3.0


def test_roundup_mixed_sign_asymmetry_in_one_fixture():
    """ROUNDUP is always away from zero — the sign of the input changes the
    sign of the answer but not the direction relative to magnitude."""
    up_positive = run_reconciliation(
        _single_cell_workbook("=ROUNDUP(1.1,0)", 2.0), ["Provisions!C1"]
    ).lines[0]
    up_negative = run_reconciliation(
        _single_cell_workbook("=ROUNDUP(-1.1,0)", -2.0), ["Provisions!C1"]
    ).lines[0]
    assert up_positive.target_value == 2.0
    assert up_negative.target_value == -2.0


def test_rounddown_mixed_sign_asymmetry_in_one_fixture():
    """ROUNDDOWN is always toward zero — the direct contrast with ROUNDUP on
    the same magnitude."""
    down_positive = run_reconciliation(
        _single_cell_workbook("=ROUNDDOWN(1.9,0)", 1.0), ["Provisions!C1"]
    ).lines[0]
    down_negative = run_reconciliation(
        _single_cell_workbook("=ROUNDDOWN(-1.9,0)", -1.0), ["Provisions!C1"]
    ).lines[0]
    assert down_positive.target_value == 1.0
    assert down_negative.target_value == -1.0


def test_roundup_rounddown_half_ties_both_signs():
    up_pos = run_reconciliation(_single_cell_workbook("=ROUNDUP(0.5,0)", 1.0), ["Provisions!C1"]).lines[0]
    up_neg = run_reconciliation(_single_cell_workbook("=ROUNDUP(-0.5,0)", -1.0), ["Provisions!C1"]).lines[0]
    down_pos = run_reconciliation(
        _single_cell_workbook("=ROUNDDOWN(0.5,0)", 0.0), ["Provisions!C1"]
    ).lines[0]
    down_neg = run_reconciliation(
        _single_cell_workbook("=ROUNDDOWN(-0.5,0)", 0.0), ["Provisions!C1"]
    ).lines[0]
    assert up_pos.target_value == 1.0
    assert up_neg.target_value == -1.0
    assert down_pos.target_value == 0.0
    assert down_neg.target_value == 0.0


def test_round_negative_digit_count_rounds_to_hundreds():
    line = run_reconciliation(_single_cell_workbook("=ROUND(1250,-2)", 1300.0), ["Provisions!C1"]).lines[0]
    assert line.target_value == 1300.0


def test_ceiling_rounds_to_significance_not_digit_count():
    """The load-bearing case distinguishing significance from digit count:
    CEILING(1.23, 0.5) is 1.5, not 2 (which a digit-count misreading of the
    second argument would wrongly produce)."""
    line = run_reconciliation(
        _single_cell_workbook("=CEILING(1.23,0.5)", 1.5), ["Provisions!C1"]
    ).lines[0]
    assert line.target_value == 1.5


def test_floor_rounds_to_significance_not_digit_count():
    line = run_reconciliation(
        _single_cell_workbook("=FLOOR(1.23,0.5)", 1.0), ["Provisions!C1"]
    ).lines[0]
    assert line.target_value == 1.0


def test_ceiling_floor_significance_of_ten():
    ceiling_line = run_reconciliation(
        _single_cell_workbook("=CEILING(12,10)", 20.0), ["Provisions!C1"]
    ).lines[0]
    floor_line = run_reconciliation(
        _single_cell_workbook("=FLOOR(12,10)", 10.0), ["Provisions!C1"]
    ).lines[0]
    assert ceiling_line.target_value == 20.0
    assert floor_line.target_value == 10.0


def test_ceiling_hand_checked_value_negative_value_positive_significance():
    """The Step 14 hand-checked case: CEILING(-0.5, 1) == -1 in real Excel —
    significance's sign need not match value's sign for plain CEILING."""
    line = run_reconciliation(
        _single_cell_workbook("=CEILING(-0.5,1)", -1.0), ["Provisions!C1"]
    ).lines[0]
    assert line.target_value == -1.0


def test_rounding_function_argument_is_a_cell_reference():
    line = run_reconciliation(
        _single_cell_workbook("=ROUND(B1,0)", 3.0, referenced_value=2.5), ["Provisions!C1"]
    ).lines[0]
    assert line.target_value == 3.0


def test_round_nested_inside_sum():
    cells = {
        "Provisions!C1": cell("Provisions!C1", value=2.5),
        "Provisions!C2": cell("Provisions!C2", value=10.0),
        "Provisions!C3": cell("Provisions!C3", formula="=SUM(ROUND(C1,0),C2)", value=13.0),
    }
    graph = {"Provisions!C3": ["Provisions!C1", "Provisions!C2"], "Provisions!C1": [], "Provisions!C2": []}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!C3"]).lines[0]
    assert line.target_value == 13.0


# ---------------------------------------------------------------------------
# Group C: SUMIF (reference implementation)
# ---------------------------------------------------------------------------


def test_sumif_numeric_comparison_criteria_no_sum_range():
    """SUMIF(range, criteria) — sum_range omitted, so range itself is summed."""
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=50.0),
        "Provisions!A2": cell("Provisions!A2", value=150.0),
        "Provisions!A3": cell("Provisions!A3", value=250.0),
        "Provisions!B1": cell("Provisions!B1", formula='=SUMIF(A1:A3,">100")', value=400.0),
    }
    graph = {
        "Provisions!B1": ["Provisions!A1", "Provisions!A2", "Provisions!A3"],
        "Provisions!A1": [],
        "Provisions!A2": [],
        "Provisions!A3": [],
    }
    line = run_reconciliation(parsed(cells, graph), ["Provisions!B1"]).lines[0]
    assert line.target_value == 400.0
    assert line.completeness == "complete"


def test_sumif_wildcard_text_criteria_with_separate_sum_range():
    """Text categories (a class-of-business column) are the whole point of
    SUMIF — a wildcard criteria matches "Motor Comprehensive" but not
    "Property"."""
    cells = {
        "Provisions!B1": cell("Provisions!B1", value="Motor"),
        "Provisions!B2": cell("Provisions!B2", value="Property"),
        "Provisions!B3": cell("Provisions!B3", value="Motor Comprehensive"),
        "Provisions!C1": cell("Provisions!C1", value=10.0),
        "Provisions!C2": cell("Provisions!C2", value=20.0),
        "Provisions!C3": cell("Provisions!C3", value=30.0),
        "Provisions!D1": cell(
            "Provisions!D1", formula='=SUMIF(B1:B3,"Motor*",C1:C3)', value=40.0
        ),
    }
    graph = {
        "Provisions!D1": [
            "Provisions!B1",
            "Provisions!B2",
            "Provisions!B3",
            "Provisions!C1",
            "Provisions!C2",
            "Provisions!C3",
        ],
        "Provisions!B1": [],
        "Provisions!B2": [],
        "Provisions!B3": [],
        "Provisions!C1": [],
        "Provisions!C2": [],
        "Provisions!C3": [],
    }
    line = run_reconciliation(parsed(cells, graph), ["Provisions!D1"]).lines[0]
    assert line.target_value == 40.0
    assert line.completeness == "complete"
    assert line.unsupported_elements == []


def test_sumif_comparison_operator_criteria_with_separate_sum_range():
    cells = {
        "Provisions!B1": cell("Provisions!B1", value=50.0),
        "Provisions!B2": cell("Provisions!B2", value=150.0),
        "Provisions!B3": cell("Provisions!B3", value=250.0),
        "Provisions!C1": cell("Provisions!C1", value=1.0),
        "Provisions!C2": cell("Provisions!C2", value=2.0),
        "Provisions!C3": cell("Provisions!C3", value=3.0),
        "Provisions!D1": cell("Provisions!D1", formula='=SUMIF(B1:B3,">100",C1:C3)', value=5.0),
    }
    graph = {
        "Provisions!D1": [
            "Provisions!B1",
            "Provisions!B2",
            "Provisions!B3",
            "Provisions!C1",
            "Provisions!C2",
            "Provisions!C3",
        ],
        "Provisions!B1": [],
        "Provisions!B2": [],
        "Provisions!B3": [],
        "Provisions!C1": [],
        "Provisions!C2": [],
        "Provisions!C3": [],
    }
    line = run_reconciliation(parsed(cells, graph), ["Provisions!D1"]).lines[0]
    assert line.target_value == 5.0


def test_sumif_cross_tab_range_and_sum_range():
    cells = {
        "Data!B1": cell("Data!B1", value="Motor"),
        "Data!B2": cell("Data!B2", value="Property"),
        "Data!C1": cell("Data!C1", value=100.0),
        "Data!C2": cell("Data!C2", value=200.0),
        "Provisions!D1": cell(
            "Provisions!D1", formula='=SUMIF(Data!B1:B2,"Motor",Data!C1:C2)', value=100.0
        ),
    }
    graph = {
        "Provisions!D1": ["Data!B1", "Data!B2", "Data!C1", "Data!C2"],
        "Data!B1": [],
        "Data!B2": [],
        "Data!C1": [],
        "Data!C2": [],
    }
    line = run_reconciliation(
        parsed(cells, graph, tabs=("Provisions", "Data")), ["Provisions!D1"]
    ).lines[0]
    assert line.target_value == 100.0


def test_sumif_criteria_built_from_a_cell_reference():
    """The trap Step 6 calls out by name: SUMIF(A:A, ">"&B1, C:C). The
    criteria is a live formula-built string, not a literal — many criteria
    engines silently fail exactly here."""
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=50.0),
        "Provisions!A2": cell("Provisions!A2", value=150.0),
        "Provisions!A3": cell("Provisions!A3", value=250.0),
        "Provisions!E1": cell("Provisions!E1", value=100.0),
        "Provisions!F1": cell(
            "Provisions!F1", formula='=SUMIF(A1:A3,">"&E1)', value=400.0
        ),
    }
    graph = {
        "Provisions!F1": ["Provisions!A1", "Provisions!A2", "Provisions!A3", "Provisions!E1"],
        "Provisions!A1": [],
        "Provisions!A2": [],
        "Provisions!A3": [],
        "Provisions!E1": [],
    }
    line = run_reconciliation(parsed(cells, graph), ["Provisions!F1"]).lines[0]
    assert line.target_value == 400.0
    assert line.completeness == "complete"


def test_sumif_criteria_string_literal_is_not_flagged_as_text_in_arithmetic():
    """Regression guard: SUMIF's own criteria literal must not trip the
    'text literal in arithmetic' check that (correctly) still fires for a
    string literal genuinely used outside a criteria-consuming function."""
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=50.0),
        "Provisions!B1": cell("Provisions!B1", formula='=SUMIF(A1:A1,">10")', value=50.0),
    }
    graph = {"Provisions!B1": ["Provisions!A1"], "Provisions!A1": []}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!B1"]).lines[0]
    assert line.unsupported_elements == []
    assert line.completeness == "complete"


def test_text_literal_outside_sumif_is_still_unsupported():
    """The other half of the guard above: a bare string literal used
    outside a criteria-consuming function must still be rejected."""
    cells = {
        "Provisions!A1": cell("Provisions!A1", formula='=A2&"x"', value=None),
        "Provisions!A2": cell("Provisions!A2", value=5.0),
    }
    graph = {"Provisions!A1": ["Provisions!A2"], "Provisions!A2": []}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!A1"]).lines[0]
    assert any("text literal in arithmetic" in element for element in line.unsupported_elements)


def test_sumif_no_matches_sums_to_zero():
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=10.0),
        "Provisions!B1": cell("Provisions!B1", formula='=SUMIF(A1:A1,">1000")', value=0.0),
    }
    graph = {"Provisions!B1": ["Provisions!A1"], "Provisions!A1": []}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!B1"]).lines[0]
    assert line.target_value == 0.0


# ---------------------------------------------------------------------------
# Group C: SUMIFS
# ---------------------------------------------------------------------------


def _sumifs_workbook():
    """Five rows: class, region, an "included" flag, and an amount —
    class-of-business, geography, and a boolean inclusion flag are the
    three criteria ranges, mixing text/wildcard, text/exact, and boolean
    criteria types in one fixture."""
    rows = [
        ("Motor", "North", True, 100.0),
        ("Motor", "South", True, 200.0),
        ("Property", "North", True, 300.0),
        ("Motor", "North", False, 400.0),
        ("Motor", "North", True, 500.0),
    ]
    cells = {}
    graph_deps = []
    for i, (klass, region, flag, amount) in enumerate(rows, start=1):
        cells[f"Provisions!A{i}"] = cell(f"Provisions!A{i}", value=klass)
        cells[f"Provisions!B{i}"] = cell(f"Provisions!B{i}", value=region)
        cells[f"Provisions!C{i}"] = cell(f"Provisions!C{i}", value=flag)
        cells[f"Provisions!D{i}"] = cell(f"Provisions!D{i}", value=amount)
        graph_deps.extend([f"Provisions!A{i}", f"Provisions!B{i}", f"Provisions!C{i}", f"Provisions!D{i}"])
    return cells, graph_deps


def test_sumifs_three_criteria_ranges_mixed_types():
    """Class (wildcard-capable text), region (exact text), and a boolean
    flag — three criteria ranges of genuinely different types must all
    match (AND) for a row to be included."""
    cells, deps = _sumifs_workbook()
    cells["Provisions!E1"] = cell(
        "Provisions!E1",
        formula='=SUMIFS(D1:D5,A1:A5,"Motor*",B1:B5,"North",C1:C5,TRUE)',
        value=600.0,
    )
    graph = {"Provisions!E1": deps, **{d: [] for d in deps}}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!E1"]).lines[0]
    # Rows 1 and 5 match (Motor, North, TRUE); row 4 is excluded by the flag.
    assert line.target_value == 600.0
    assert line.completeness == "complete"


def test_sumifs_criteria_built_from_a_cell_reference():
    cells, deps = _sumifs_workbook()
    cells["Provisions!F1"] = cell("Provisions!F1", value=150.0)
    cells["Provisions!E1"] = cell(
        "Provisions!E1",
        formula='=SUMIFS(D1:D5,A1:A5,"Motor",D1:D5,">"&F1)',
        value=1100.0,
    )
    graph = {"Provisions!E1": deps + ["Provisions!F1"], **{d: [] for d in deps}, "Provisions!F1": []}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!E1"]).lines[0]
    # Motor rows with amount > 150: 200 + 400 + 500 = 1100 (100 excluded).
    assert line.target_value == 1100.0


def test_sumifs_no_matching_row_sums_to_zero():
    cells, deps = _sumifs_workbook()
    cells["Provisions!E1"] = cell(
        "Provisions!E1", formula='=SUMIFS(D1:D5,A1:A5,"Liability")', value=0.0
    )
    graph = {"Provisions!E1": deps, **{d: [] for d in deps}}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!E1"]).lines[0]
    assert line.target_value == 0.0


# ---------------------------------------------------------------------------
# Group C: COUNTIF
# ---------------------------------------------------------------------------


def test_countif_numeric_comparison_criteria():
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=50.0),
        "Provisions!A2": cell("Provisions!A2", value=150.0),
        "Provisions!A3": cell("Provisions!A3", value=250.0),
        "Provisions!B1": cell("Provisions!B1", formula='=COUNTIF(A1:A3,">100")', value=2.0),
    }
    graph = {
        "Provisions!B1": ["Provisions!A1", "Provisions!A2", "Provisions!A3"],
        "Provisions!A1": [],
        "Provisions!A2": [],
        "Provisions!A3": [],
    }
    line = run_reconciliation(parsed(cells, graph), ["Provisions!B1"]).lines[0]
    assert line.target_value == 2.0


def test_countif_wildcard_text_criteria():
    cells = {
        "Provisions!B1": cell("Provisions!B1", value="Motor"),
        "Provisions!B2": cell("Provisions!B2", value="Property"),
        "Provisions!B3": cell("Provisions!B3", value="Motor Comprehensive"),
        "Provisions!D1": cell("Provisions!D1", formula='=COUNTIF(B1:B3,"Motor*")', value=2.0),
    }
    graph = {
        "Provisions!D1": ["Provisions!B1", "Provisions!B2", "Provisions!B3"],
        "Provisions!B1": [],
        "Provisions!B2": [],
        "Provisions!B3": [],
    }
    line = run_reconciliation(parsed(cells, graph), ["Provisions!D1"]).lines[0]
    assert line.target_value == 2.0


def test_countif_cross_tab_range():
    cells = {
        "Data!B1": cell("Data!B1", value="Motor"),
        "Data!B2": cell("Data!B2", value="Property"),
        "Provisions!D1": cell("Provisions!D1", formula='=COUNTIF(Data!B1:B2,"Motor")', value=1.0),
    }
    graph = {"Provisions!D1": ["Data!B1", "Data!B2"], "Data!B1": [], "Data!B2": []}
    line = run_reconciliation(
        parsed(cells, graph, tabs=("Provisions", "Data")), ["Provisions!D1"]
    ).lines[0]
    assert line.target_value == 1.0


def test_countif_criteria_built_from_a_cell_reference():
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=50.0),
        "Provisions!A2": cell("Provisions!A2", value=150.0),
        "Provisions!A3": cell("Provisions!A3", value=250.0),
        "Provisions!E1": cell("Provisions!E1", value=100.0),
        "Provisions!F1": cell("Provisions!F1", formula='=COUNTIF(A1:A3,">"&E1)', value=2.0),
    }
    graph = {
        "Provisions!F1": ["Provisions!A1", "Provisions!A2", "Provisions!A3", "Provisions!E1"],
        "Provisions!A1": [],
        "Provisions!A2": [],
        "Provisions!A3": [],
        "Provisions!E1": [],
    }
    line = run_reconciliation(parsed(cells, graph), ["Provisions!F1"]).lines[0]
    assert line.target_value == 2.0


# ---------------------------------------------------------------------------
# Group C: COUNTIFS
# ---------------------------------------------------------------------------


def test_countifs_multiple_criteria_pairs():
    cells, deps = _sumifs_workbook()
    cells["Provisions!E1"] = cell(
        "Provisions!E1", formula='=COUNTIFS(A1:A5,"Motor",B1:B5,"North")', value=3.0
    )
    graph = {"Provisions!E1": deps, **{d: [] for d in deps}}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!E1"]).lines[0]
    # Rows 1, 4, 5 are Motor/North (row 4's flag doesn't matter — COUNTIFS
    # here only has two criteria pairs, not three).
    assert line.target_value == 3.0


def test_countifs_three_criteria_pairs_matches_sumifs_row_selection():
    cells, deps = _sumifs_workbook()
    cells["Provisions!E1"] = cell(
        "Provisions!E1",
        formula='=COUNTIFS(A1:A5,"Motor*",B1:B5,"North",C1:C5,TRUE)',
        value=2.0,
    )
    graph = {"Provisions!E1": deps, **{d: [] for d in deps}}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!E1"]).lines[0]
    # The same row selection as test_sumifs_three_criteria_ranges_mixed_types
    # (rows 1 and 5) — counted here instead of summed.
    assert line.target_value == 2.0


def test_countifs_no_matching_row_is_zero():
    cells, deps = _sumifs_workbook()
    cells["Provisions!E1"] = cell(
        "Provisions!E1", formula='=COUNTIFS(A1:A5,"Liability",B1:B5,"East")', value=0.0
    )
    graph = {"Provisions!E1": deps, **{d: [] for d in deps}}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!E1"]).lines[0]
    assert line.target_value == 0.0


# ---------------------------------------------------------------------------
# Group C: SUMIF vs COUNTIF on the same range — sum vs count made explicit
# ---------------------------------------------------------------------------


def test_sumif_and_countif_on_the_same_range_are_not_the_same_number():
    """The explicit contrast Step 7 asks for: SUMIF totals the matching
    amounts, COUNTIF counts the matching cells — same range, same criteria,
    deliberately different answers."""
    cells = {
        "Provisions!A1": cell("Provisions!A1", value="Motor"),
        "Provisions!A2": cell("Provisions!A2", value="Property"),
        "Provisions!A3": cell("Provisions!A3", value="Motor"),
        "Provisions!B1": cell("Provisions!B1", value=100.0),
        "Provisions!B2": cell("Provisions!B2", value=300.0),
        "Provisions!B3": cell("Provisions!B3", value=500.0),
        "Provisions!C1": cell("Provisions!C1", formula='=SUMIF(A1:A3,"Motor",B1:B3)', value=600.0),
        "Provisions!C2": cell("Provisions!C2", formula='=COUNTIF(A1:A3,"Motor")', value=2.0),
    }
    deps = ["Provisions!A1", "Provisions!A2", "Provisions!A3", "Provisions!B1", "Provisions!B2", "Provisions!B3"]
    graph = {"Provisions!C1": deps, "Provisions!C2": deps[:3], **{d: [] for d in deps}}
    result = run_reconciliation(parsed(cells, graph), ["Provisions!C1", "Provisions!C2"])
    sumif_line, countif_line = result.lines
    assert sumif_line.target_value == 600.0
    assert countif_line.target_value == 2.0
    assert sumif_line.target_value != countif_line.target_value


# ---------------------------------------------------------------------------
# Group C: AVERAGEIF, AVERAGEIFS
# ---------------------------------------------------------------------------


def _averageif_workbook():
    """Five rows by class of business. Row 5 is Motor with a BLANK amount —
    it matches the criteria but must not count toward the denominator, and
    "Property" is the only other class, exercising the case where "Motor"
    matches several rows and "Liability" matches none at all."""
    rows = [
        ("Motor", 100.0),
        ("Motor", 300.0),
        ("Property", 500.0),
        ("Motor", 700.0),
        ("Motor", None),
    ]
    cells = {}
    deps = []
    for i, (klass, amount) in enumerate(rows, start=1):
        cells[f"Provisions!A{i}"] = cell(f"Provisions!A{i}", value=klass)
        cells[f"Provisions!B{i}"] = cell(f"Provisions!B{i}", value=amount)
        deps.extend([f"Provisions!A{i}", f"Provisions!B{i}"])
    return cells, deps


def test_averageif_denominator_is_matching_cells_not_all_cells():
    """The Section 3.12 trap: row 5 matches "Motor" but its amount is blank —
    it must be excluded from the denominator (average of 3 values), not
    counted as a 4th row averaging in a 0."""
    cells, deps = _averageif_workbook()
    cells["Provisions!C1"] = cell(
        "Provisions!C1", formula='=AVERAGEIF(A1:A5,"Motor",B1:B5)', value=1100.0 / 3
    )
    graph = {"Provisions!C1": deps, **{d: [] for d in deps}}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!C1"]).lines[0]
    assert line.target_value == pytest.approx(1100.0 / 3)
    # Explicitly NOT (100+300+700+0)/4 == 275.0 — the off-by-one-denominator
    # bug this test exists to catch.
    assert line.target_value != pytest.approx(275.0)


def test_averageif_no_matching_class_fails_closed_not_zero():
    """No row is "Liability" — Excel's own answer here is #DIV/0!, not 0.
    Without Step 12's error-category verdict work, this reconstructs to
    None (an honest gap) rather than a false numeric agreement at 0."""
    cells, deps = _averageif_workbook()
    cells["Provisions!C1"] = cell(
        "Provisions!C1", formula='=AVERAGEIF(A1:A5,"Liability",B1:B5)', value="#DIV/0!"
    )
    graph = {"Provisions!C1": deps, **{d: [] for d in deps}}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!C1"]).lines[0]
    assert line.target_value is None
    assert line.completeness == "partial"


def test_averageif_and_sumif_on_the_same_data_are_not_the_same_number():
    """The explicit contrast Step 8 asks for: SUMIF totals the matching
    amounts (including the blank as 0), AVERAGEIF averages only the
    matching NUMERIC cells — same range, same criteria, different answers
    for a different reason than SUMIF-vs-COUNTIF's sum-vs-count contrast."""
    cells, deps = _averageif_workbook()
    cells["Provisions!C1"] = cell(
        "Provisions!C1", formula='=SUMIF(A1:A5,"Motor",B1:B5)', value=1100.0
    )
    cells["Provisions!C2"] = cell(
        "Provisions!C2", formula='=AVERAGEIF(A1:A5,"Motor",B1:B5)', value=1100.0 / 3
    )
    graph = {"Provisions!C1": deps, "Provisions!C2": deps, **{d: [] for d in deps}}
    result = run_reconciliation(parsed(cells, graph), ["Provisions!C1", "Provisions!C2"])
    sumif_line, averageif_line = result.lines
    assert sumif_line.target_value == 1100.0
    assert averageif_line.target_value == pytest.approx(1100.0 / 3)
    assert sumif_line.target_value != averageif_line.target_value


def test_averageifs_two_criteria_pairs():
    cells, deps = _sumifs_workbook()
    cells["Provisions!E1"] = cell(
        "Provisions!E1",
        formula='=AVERAGEIFS(D1:D5,A1:A5,"Motor",B1:B5,"North")',
        value=1000.0 / 3,
    )
    graph = {"Provisions!E1": deps, **{d: [] for d in deps}}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!E1"]).lines[0]
    # Rows 1, 4, 5 are Motor/North: amounts 100, 400, 500 -> average 333.33.
    assert line.target_value == pytest.approx(1000.0 / 3)


def test_averageifs_no_match_fails_closed_not_zero():
    cells, deps = _sumifs_workbook()
    cells["Provisions!E1"] = cell(
        "Provisions!E1",
        formula='=AVERAGEIFS(D1:D5,A1:A5,"Liability",B1:B5,"East")',
        value="#DIV/0!",
    )
    graph = {"Provisions!E1": deps, **{d: [] for d in deps}}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!E1"]).lines[0]
    assert line.target_value is None
    assert line.completeness == "partial"


def test_blank_cell_still_reads_as_zero_in_bare_arithmetic_and_abs():
    """Regression guard for the blank/zero distinction Step 8 introduced:
    a blank dependency must still read as 0 for arithmetic (SUM, bare
    arithmetic, ABS/INT/ROUND's argument), NOT become unresolvable just
    because AVERAGEIF-family evaluators now need to see it as None."""
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=None),
        "Provisions!A2": cell("Provisions!A2", formula="=A1*2", value=0.0),
        "Provisions!A3": cell("Provisions!A3", formula="=ABS(A1)", value=0.0),
    }
    graph = {
        "Provisions!A2": ["Provisions!A1"],
        "Provisions!A3": ["Provisions!A1"],
        "Provisions!A1": [],
    }
    lines = run_reconciliation(parsed(cells, graph), ["Provisions!A2", "Provisions!A3"]).lines
    assert lines[0].target_value == 0.0
    assert lines[0].completeness == "complete"
    assert lines[1].target_value == 0.0
    assert lines[1].completeness == "complete"


# ---------------------------------------------------------------------------
# unsupported elements
# ---------------------------------------------------------------------------


def test_vlookup_produces_incomplete_not_a_guess():
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=5.0),
        "Provisions!C9": cell("Provisions!C9", formula="=VLOOKUP(A1,Rates!A:B,2,FALSE)", value=0.05),
        "Provisions!C5": cell("Provisions!C5", formula="=C9*1000", value=50.0),
    }
    graph = {"Provisions!C5": ["Provisions!C9"], "Provisions!C9": ["Provisions!A1"], "Provisions!A1": []}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!C5"]).lines[0]

    assert line.verdict == "incomplete"
    assert line.completeness == "partial"
    assert line.target_value is None
    assert any("VLOOKUP" in element for element in line.unsupported_elements)


def test_the_unsupported_formula_appears_verbatim():
    """VLOOKUP is not yet implemented. Verify that unsupported formulas
    still get reported verbatim."""
    cells = {
        "Provisions!C9": cell("Provisions!C9", formula="=VLOOKUP(A1,B:D,2,0)", value=1.0),
        "Provisions!C5": cell("Provisions!C5", formula="=C9*10", value=10.0),
    }
    graph = {"Provisions!C5": ["Provisions!C9"], "Provisions!C9": []}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!C5"]).lines[0]
    assert any("=VLOOKUP(A1,B:D,2,0)" in element for element in line.unsupported_elements)


def test_coverage_reflects_how_much_of_the_chain_resolved():
    """One unsupported node out of three is 66.7% coverage, not a pass and not
    a total failure."""
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=5.0),
        "Provisions!C9": cell("Provisions!C9", formula="=VLOOKUP(A1,X,2,FALSE)", value=0.05),
        "Provisions!C5": cell("Provisions!C5", formula="=C9+A1", value=5.05),
    }
    graph = {
        "Provisions!C5": ["Provisions!C9", "Provisions!A1"],
        "Provisions!C9": ["Provisions!A1"],
        "Provisions!A1": [],
    }
    line = run_reconciliation(parsed(cells, graph), ["Provisions!C5"]).lines[0]
    assert 0 < line.reconstruction_coverage_pct < 100
    assert line.completeness == "partial"


def test_a_supported_parent_of_an_unsupported_child_is_still_marked_supported():
    """is_supported describes a node's own formula. The parent isn't the
    problem; it just can't produce a number because its child couldn't."""
    cells = {
        "Provisions!C9": cell("Provisions!C9", formula="=VLOOKUP(A1,X,2,FALSE)", value=1.0),
        "Provisions!C5": cell("Provisions!C5", formula="=C9*10", value=10.0),
    }
    graph = {"Provisions!C5": ["Provisions!C9"], "Provisions!C9": []}
    chain = {s.cell_ref: s for s in run_reconciliation(parsed(cells, graph), ["Provisions!C5"]).lines[0].derivation}

    assert chain["Provisions!C9"].is_supported is False
    assert chain["Provisions!C5"].is_supported is True
    assert chain["Provisions!C5"].resolved_value is None


def test_an_external_workbook_reference_is_unsupported():
    cells = {"Provisions!C5": cell("Provisions!C5", formula="='[Other.xlsx]Sheet1'!A1*2", value=10.0)}
    line = run_reconciliation(parsed(cells, {"Provisions!C5": []}), ["Provisions!C5"]).lines[0]
    assert line.completeness == "partial"


def test_a_circular_reference_is_unsupported_not_infinite():
    cells = {
        "Provisions!A1": cell("Provisions!A1", formula="=A2+1", value=None),
        "Provisions!A2": cell("Provisions!A2", formula="=A1+1", value=None),
    }
    graph = {"Provisions!A1": ["Provisions!A2"], "Provisions!A2": ["Provisions!A1"]}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!A1"]).lines[0]

    assert line.completeness == "partial"
    assert any("circular reference" in element for element in line.unsupported_elements)


def test_text_used_in_arithmetic_is_unsupported():
    cells = {
        "Provisions!A1": cell("Provisions!A1", value="n/a"),
        "Provisions!A2": cell("Provisions!A2", formula="=A1*2", value=None),
    }
    graph = {"Provisions!A2": ["Provisions!A1"], "Provisions!A1": []}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!A2"]).lines[0]
    assert line.completeness == "partial"


# ---------------------------------------------------------------------------
# delta — symmetry and zero-safety
# ---------------------------------------------------------------------------


def test_delta_pct_is_zero_safe_when_both_values_are_zero():
    """The ZeroDivisionError case."""
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=0.0),
        "Provisions!A2": cell("Provisions!A2", formula="=A1*1", value=0.0),
    }
    graph = {"Provisions!A2": ["Provisions!A1"], "Provisions!A1": []}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!A2"]).lines[0]

    assert line.delta == 0.0
    assert line.delta_pct == 0.0
    assert line.verdict == "pass"


def test_delta_pct_is_symmetric():
    """Same two numbers, swapped. The percentage must not depend on which side
    of the comparison each landed on."""
    from agents.reconciliation import _delta

    forward = _delta(100.0, 150.0)
    backward = _delta(150.0, 100.0)
    assert forward == backward
    assert forward[1] == pytest.approx(50.0 / 150.0)


def test_a_source_of_zero_against_a_nonzero_target_does_not_crash():
    from agents.reconciliation import _delta

    delta, delta_pct = _delta(0.0, 50.0)
    assert delta == 50.0
    assert delta_pct == 1.0


# ---------------------------------------------------------------------------
# Pass 1 alone
# ---------------------------------------------------------------------------


def test_pass_2_does_not_run_without_reference_figures():
    result = run_reconciliation(simple_sum_workbook(), ["Provisions!C5"])
    assert [line.check_type for line in result.lines] == ["excel_vs_python"]
    assert result.mappings == []
    assert result.unmatched_reference_items == []
    assert result.unmapped_python_outputs == []


# ---------------------------------------------------------------------------
# Pass 2 — proposals, never approvals
# ---------------------------------------------------------------------------


def test_a_confident_match_still_produces_an_unapproved_mapping():
    """THE CFO fix. A 100% label match is a good proposal and nothing more.
    There is no code path from string similarity to is_approved=True."""
    result = run_reconciliation(
        simple_sum_workbook(),
        ["Provisions!C5"],
        reference_figures=reference([gl_line("GL-001", "Technical provisions", 100.0)]),
    )

    assert len(result.mappings) == 1
    mapping = result.mappings[0]
    assert mapping.suggested_confidence >= 95.0
    assert mapping.is_approved is False
    assert mapping.approved_by is None
    assert mapping.approved_at is None
    assert mapping.suggested_by == "fuzzy_match"


def test_no_mapping_anywhere_is_ever_returned_approved():
    """Swept across several match qualities at once."""
    result = run_reconciliation(
        simple_sum_workbook(),
        ["Provisions!C5"],
        reference_figures=reference(
            [
                gl_line("GL-001", "Technical provisions", 100.0),
                gl_line("GL-002", "Technical provision", 100.0),
                gl_line("GL-003", "Tech. provisions total", 100.0),
            ]
        ),
    )
    assert all(mapping.is_approved is False for mapping in result.mappings)


def test_a_preliminary_accounts_line_carries_its_mapping_id():
    result = run_reconciliation(
        simple_sum_workbook(),
        ["Provisions!C5"],
        reference_figures=reference([gl_line("GL-001", "Technical provisions", 100.0)]),
    )
    external = [line for line in result.lines if line.check_type == "python_vs_accounts"]

    assert len(external) == 1
    assert external[0].mapping_id == result.mappings[0].mapping_id
    assert external[0].source_value == 100.0
    assert external[0].target_value == 100.0


def test_an_ambiguous_match_is_flagged_distinctly_from_the_numbers():
    """"The match itself needs confirmation" is a different message from "the
    numbers need confirmation", and Gate 3's UI has to be able to tell them
    apart."""
    result = run_reconciliation(
        simple_sum_workbook(),
        ["Provisions!C5"],
        reference_figures=reference([gl_line("GL-001", "Provision technique brute", 100.0)]),
    )

    if result.mappings:
        mapping = result.mappings[0]
        if mapping.suggested_confidence < 85.0:
            assert mapping.approval_note is not None
            assert "needs confirmation" in mapping.approval_note


def test_a_reference_line_matching_two_outputs_equally_is_not_silently_resolved():
    """Two identically-labelled outputs. Picking one would be inventing an
    accounting decision."""
    cells = {
        "Provisions!B5": cell("Provisions!B5", value="Technical provisions"),
        "Provisions!C5": cell("Provisions!C5", formula="=C1*1", value=100.0),
        "Provisions!B9": cell("Provisions!B9", value="Technical provisions"),
        "Provisions!C9": cell("Provisions!C9", formula="=C1*1", value=100.0),
        "Provisions!C1": cell("Provisions!C1", value=100.0),
    }
    graph = {
        "Provisions!C5": ["Provisions!C1"],
        "Provisions!C9": ["Provisions!C1"],
        "Provisions!C1": [],
    }
    result = run_reconciliation(
        parsed(cells, graph),
        ["Provisions!C5", "Provisions!C9"],
        reference_figures=reference([gl_line("GL-001", "Technical provisions", 100.0)]),
    )

    mapping = result.mappings[0]
    assert mapping.mapping_type == "one_to_many"
    assert "not computed by this tool" in mapping.approval_note
    # No comparison line: the aggregate was not silently invented.
    assert [line for line in result.lines if line.check_type == "python_vs_accounts"] == []


# ---------------------------------------------------------------------------
# bidirectional completeness — two questions, not one
# ---------------------------------------------------------------------------


def test_a_reference_line_with_no_counterpart_is_unmatched():
    result = run_reconciliation(
        simple_sum_workbook(),
        ["Provisions!C5"],
        reference_figures=reference(
            [
                gl_line("GL-001", "Technical provisions", 100.0),
                gl_line("GL-002", "Deferred acquisition costs", 44.0),
            ]
        ),
    )
    assert "GL-002" in result.unmatched_reference_items


def test_duplicate_reference_labels_do_not_reuse_one_python_output():
    """A dict-era regression: duplicate ledger labels must both survive.

    Once the single matching output is proposed for the first line, the second
    line remains unmatched instead of borrowing the same output and presenting
    two unrelated accounting balances as though both were reconciled.
    """
    result = run_reconciliation(
        simple_sum_workbook(),
        ["Provisions!C5"],
        reference_figures=reference(
            [
                gl_line("GL-001", "Technical provisions", 100.0),
                gl_line("GL-002", "Technical provisions", 500.0),
            ]
        ),
    )

    assert len(result.mappings) == 1
    assert result.mappings[0].reference_line_id == "GL-001"
    assert result.unmatched_reference_items == ["GL-002"]


def test_an_output_with_no_counterpart_is_unmapped():
    cells = dict(simple_sum_workbook().cells)
    cells["Provisions!B7"] = cell("Provisions!B7", value="Deferred acquisition costs")
    cells["Provisions!C7"] = cell("Provisions!C7", formula="=C1*1", value=10.0)
    graph = dict(simple_sum_workbook().cell_dependency_graph)
    graph["Provisions!C7"] = ["Provisions!C1"]

    result = run_reconciliation(
        parsed(cells, graph),
        ["Provisions!C5", "Provisions!C7"],
        reference_figures=reference([gl_line("GL-001", "Technical provisions", 100.0)]),
    )
    assert "Provisions!C7" in result.unmapped_python_outputs


def test_the_two_directions_are_independent():
    """Every GL line finds a home, and one designated output still doesn't.
    unmatched is empty while unmapped is not — proving these are two different
    checks rather than one computed twice."""
    cells = dict(simple_sum_workbook().cells)
    cells["Provisions!B7"] = cell("Provisions!B7", value="Zzzz unrelated figure")
    cells["Provisions!C7"] = cell("Provisions!C7", formula="=C1*1", value=10.0)
    graph = dict(simple_sum_workbook().cell_dependency_graph)
    graph["Provisions!C7"] = ["Provisions!C1"]

    result = run_reconciliation(
        parsed(cells, graph),
        ["Provisions!C5", "Provisions!C7"],
        reference_figures=reference([gl_line("GL-001", "Technical provisions", 100.0)]),
    )

    assert result.unmatched_reference_items == []
    assert result.unmapped_python_outputs == ["Provisions!C7"]


def test_both_directions_can_be_empty_on_a_clean_run():
    result = run_reconciliation(
        simple_sum_workbook(),
        ["Provisions!C5"],
        reference_figures=reference([gl_line("GL-001", "Technical provisions", 100.0)]),
    )
    assert result.unmatched_reference_items == []
    assert result.unmapped_python_outputs == []


# ---------------------------------------------------------------------------
# incompleteness propagation
# ---------------------------------------------------------------------------


def test_incompleteness_propagates_from_pass_1_into_pass_2():
    """A partial reconstruction cannot become a clean accounts comparison just
    because a ledger line happens to sit next to it."""
    cells = {
        "Provisions!B5": cell("Provisions!B5", value="Technical provisions"),
        "Provisions!C9": cell("Provisions!C9", formula="=VLOOKUP(A1,X,2,FALSE)", value=100.0),
        "Provisions!C5": cell("Provisions!C5", formula="=C9*1", value=100.0),
    }
    graph = {"Provisions!C5": ["Provisions!C9"], "Provisions!C9": []}

    result = run_reconciliation(
        parsed(cells, graph),
        ["Provisions!C5"],
        reference_figures=reference([gl_line("GL-001", "Technical provisions", 100.0)]),
    )
    external = [line for line in result.lines if line.check_type == "python_vs_accounts"][0]

    assert external.completeness == "partial"
    assert external.verdict == "incomplete"


# ---------------------------------------------------------------------------
# messy labels
# ---------------------------------------------------------------------------


def test_an_acronym_label_still_matches_its_spelled_out_counterpart():
    cells = {
        "Provisions!B5": cell("Provisions!B5", value="Net premium reserves"),
        "Provisions!C1": cell("Provisions!C1", value=100.0),
        "Provisions!C5": cell("Provisions!C5", formula="=C1*1", value=100.0),
    }
    graph = {"Provisions!C5": ["Provisions!C1"], "Provisions!C1": []}
    result = run_reconciliation(
        parsed(cells, graph),
        ["Provisions!C5"],
        reference_figures=reference([gl_line("GL-001", "NPR", 100.0)]),
    )
    assert result.mappings != []
    assert result.unmatched_reference_items == []


def test_an_output_with_no_adjacent_label_falls_back_to_its_cell_reference():
    cells = {
        "Provisions!A1": cell("Provisions!A1", value=5.0),
        "Provisions!A2": cell("Provisions!A2", formula="=A1*2", value=10.0),
    }
    line = run_reconciliation(
        parsed(cells, {"Provisions!A2": ["Provisions!A1"], "Provisions!A1": []}), ["Provisions!A2"]
    ).lines[0]
    assert line.label == "Provisions!A2"


# ---------------------------------------------------------------------------
# thresholds
# ---------------------------------------------------------------------------


def test_threshold_is_default_is_true_when_neither_was_changed():
    line = run_reconciliation(simple_sum_workbook(), ["Provisions!C5"]).lines[0]
    assert line.threshold_is_default is True


def test_threshold_is_default_is_false_when_either_was_changed():
    line = run_reconciliation(
        simple_sum_workbook(), ["Provisions!C5"], pct_threshold=0.05
    ).lines[0]
    assert line.threshold_is_default is False
    assert line.pct_threshold == 0.05


# ---------------------------------------------------------------------------
# Group C final — IF
# ---------------------------------------------------------------------------


def test_if_numeric_comparison_true_condition():
    cells = {
        "Provisions!C1": cell("Provisions!C1", value=100.0),
        "Provisions!C2": cell("Provisions!C2", formula="=IF(C1>50,C1*2,C1*1)", value=200.0),
    }
    line = run_reconciliation(
        parsed(cells, {"Provisions!C2": ["Provisions!C1"], "Provisions!C1": []}),
        ["Provisions!C2"],
    ).lines[0]
    assert line.target_value == 200.0


def test_if_numeric_comparison_false_condition():
    cells = {
        "Provisions!C1": cell("Provisions!C1", value=30.0),
        "Provisions!C2": cell("Provisions!C2", formula="=IF(C1>50,C1*2,C1*1)", value=30.0),
    }
    line = run_reconciliation(
        parsed(cells, {"Provisions!C2": ["Provisions!C1"], "Provisions!C1": []}),
        ["Provisions!C2"],
    ).lines[0]
    assert line.target_value == 30.0


def test_if_text_comparison():
    cells = {
        "Provisions!B1": cell("Provisions!B1", value="Motor"),
        "Provisions!C1": cell("Provisions!C1", value=100.0),
        "Provisions!C2": cell("Provisions!C2", formula='=IF(B1="Motor",C1*1.5,C1*1)', value=150.0),
    }
    line = run_reconciliation(
        parsed(cells, {"Provisions!C2": ["Provisions!B1", "Provisions!C1"], "Provisions!B1": [], "Provisions!C1": []}),
        ["Provisions!C2"],
    ).lines[0]
    assert line.target_value == 150.0


def test_if_falsy_condition():
    cells = {
        "Provisions!C1": cell("Provisions!C1", value=0.0),
        "Provisions!C2": cell("Provisions!C2", formula="=IF(C1,100,200)", value=200.0),
    }
    line = run_reconciliation(
        parsed(cells, {"Provisions!C2": ["Provisions!C1"], "Provisions!C1": []}),
        ["Provisions!C2"],
    ).lines[0]
    assert line.target_value == 200.0


def test_if_nested_inside_sum():
    cells = {
        "Provisions!C1": cell("Provisions!C1", value=100.0),
        "Provisions!C2": cell("Provisions!C2", value=50.0),
        "Provisions!C3": cell("Provisions!C3", formula="=SUM(IF(C1>75,C1,0),IF(C2>75,C2,0))", value=100.0),
    }
    line = run_reconciliation(
        parsed(cells, {"Provisions!C3": ["Provisions!C1", "Provisions!C2"], "Provisions!C1": [], "Provisions!C2": []}),
        ["Provisions!C3"],
    ).lines[0]
    assert line.target_value == 100.0


def test_if_inequality_operators():
    cells = {
        "Provisions!C1": cell("Provisions!C1", value=100.0),
        "Provisions!C2": cell("Provisions!C2", formula="=IF(C1>=100,1,0)", value=1.0),
        "Provisions!C3": cell("Provisions!C3", formula="=IF(C1<=100,1,0)", value=1.0),
        "Provisions!C4": cell("Provisions!C4", formula="=IF(C1<>99,1,0)", value=1.0),
    }
    graph = {
        "Provisions!C2": ["Provisions!C1"],
        "Provisions!C3": ["Provisions!C1"],
        "Provisions!C4": ["Provisions!C1"],
        "Provisions!C1": [],
    }
    result = run_reconciliation(parsed(cells, graph), ["Provisions!C2", "Provisions!C3", "Provisions!C4"])
    assert result.lines[0].target_value == 1.0
    assert result.lines[1].target_value == 1.0
    assert result.lines[2].target_value == 1.0


# ---------------------------------------------------------------------------
# Step 13 — Acceptance test exercising all 16 implemented functions
# ---------------------------------------------------------------------------


def test_acceptance_all_supported_functions():
    """Comprehensive test exercising Group A-D functions (16 total).
    Group E (VLOOKUP/INDEX/MATCH) deferred to Phase 2."""
    cells = {
        # Inputs
        "Provisions!B1": cell("Provisions!B1", value=10.0),
        "Provisions!B2": cell("Provisions!B2", value=-15.0),
        "Provisions!B3": cell("Provisions!B3", value=1.755),
        "Provisions!B4": cell("Provisions!B4", value=0.5),
        "Provisions!B5": cell("Provisions!B5", value=100.0),
        # Group A — ABS, INT
        "Provisions!C1": cell("Provisions!C1", formula="=ABS(B2)", value=15.0),
        "Provisions!C2": cell("Provisions!C2", formula="=INT(B3)", value=1.0),
        # Group B — ROUND, ROUNDUP, ROUNDDOWN, CEILING, FLOOR
        "Provisions!C3": cell("Provisions!C3", formula="=ROUND(B3,2)", value=1.76),
        "Provisions!C4": cell("Provisions!C4", formula="=ROUNDUP(B3,1)", value=1.8),
        "Provisions!C5": cell("Provisions!C5", formula="=ROUNDDOWN(B3,1)", value=1.7),
        "Provisions!C6": cell("Provisions!C6", formula="=CEILING(B4,0.1)", value=0.5),
        "Provisions!C7": cell("Provisions!C7", formula="=FLOOR(B4,0.1)", value=0.5),
        # Group C — SUMIF, SUMIFS, COUNTIF, COUNTIFS, AVERAGEIF, AVERAGEIFS
        "Provisions!D1": cell("Provisions!D1", value="A"),
        "Provisions!D2": cell("Provisions!D2", value="B"),
        "Provisions!D3": cell("Provisions!D3", value="A"),
        "Provisions!E1": cell("Provisions!E1", value=100.0),
        "Provisions!E2": cell("Provisions!E2", value=200.0),
        "Provisions!E3": cell("Provisions!E3", value=150.0),
        "Provisions!C8": cell("Provisions!C8", formula='=SUMIF(D1:D3,"A",E1:E3)', value=250.0),
        "Provisions!C9": cell("Provisions!C9", formula='=COUNTIF(D1:D3,"A")', value=2.0),
        "Provisions!C10": cell("Provisions!C10", formula='=AVERAGEIF(D1:D3,"A",E1:E3)', value=125.0),
        # Group D — MINIFS, MAXIFS
        "Provisions!F1": cell("Provisions!F1", value=50.0),
        "Provisions!F2": cell("Provisions!F2", value=75.0),
        "Provisions!F3": cell("Provisions!F3", value=25.0),
        "Provisions!C11": cell("Provisions!C11", formula='=MINIFS(F1:F3,D1:D3,"A")', value=25.0),
        "Provisions!C12": cell("Provisions!C12", formula='=MAXIFS(F1:F3,D1:D3,"A")', value=50.0),
        # Group C final — IF
        "Provisions!C13": cell("Provisions!C13", formula="=IF(B5>50,1000,500)", value=1000.0),
        # Summary (all above rolled up)
        "Provisions!C14": cell("Provisions!C14", formula="=SUM(C1:C13)", value=1474.26),
    }
    graph = {
        "Provisions!C1": ["Provisions!B2"],
        "Provisions!C2": ["Provisions!B3"],
        "Provisions!C3": ["Provisions!B3"],
        "Provisions!C4": ["Provisions!B3"],
        "Provisions!C5": ["Provisions!B3"],
        "Provisions!C6": ["Provisions!B4"],
        "Provisions!C7": ["Provisions!B4"],
        "Provisions!C8": ["Provisions!D1", "Provisions!D2", "Provisions!D3", "Provisions!E1", "Provisions!E2", "Provisions!E3"],
        "Provisions!C9": ["Provisions!D1", "Provisions!D2", "Provisions!D3"],
        "Provisions!C10": ["Provisions!D1", "Provisions!D2", "Provisions!D3", "Provisions!E1", "Provisions!E2", "Provisions!E3"],
        "Provisions!C11": ["Provisions!F1", "Provisions!F2", "Provisions!F3", "Provisions!D1", "Provisions!D2", "Provisions!D3"],
        "Provisions!C12": ["Provisions!F1", "Provisions!F2", "Provisions!F3", "Provisions!D1", "Provisions!D2", "Provisions!D3"],
        "Provisions!C13": ["Provisions!B5"],
        "Provisions!C14": ["Provisions!C1", "Provisions!C2", "Provisions!C3", "Provisions!C4", "Provisions!C5", "Provisions!C6", "Provisions!C7", "Provisions!C8", "Provisions!C9", "Provisions!C10", "Provisions!C11", "Provisions!C12", "Provisions!C13"],
        "Provisions!B1": [],
        "Provisions!B2": [],
        "Provisions!B3": [],
        "Provisions!B4": [],
        "Provisions!B5": [],
        "Provisions!D1": [],
        "Provisions!D2": [],
        "Provisions!D3": [],
        "Provisions!E1": [],
        "Provisions!E2": [],
        "Provisions!E3": [],
        "Provisions!F1": [],
        "Provisions!F2": [],
        "Provisions!F3": [],
    }
    result = run_reconciliation(parsed(cells, graph), ["Provisions!C14"])

    # Verify all functions were reconstructed correctly
    assert len(result.lines) == 1
    assert result.lines[0].target_value == pytest.approx(1474.26)
    assert result.lines[0].delta == pytest.approx(0.0)
    assert result.lines[0].verdict == "pass"
    assert result.lines[0].completeness == "complete"
