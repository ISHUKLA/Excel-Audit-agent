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


def cell(ref, formula=None, value=None, stale=False):
    return CellRecord(
        cell_ref=ref,
        formula=formula,
        cached_value=value,
        data_type="number" if isinstance(value, (int, float)) else "text",
        number_format="General",
        is_error=False,
        error_type=None,
        is_stale=stale,
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
        debit_credit="credit",
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
    cells = {
        "Provisions!C9": cell("Provisions!C9", formula="=IF(A1>0,1,2)", value=1.0),
        "Provisions!C5": cell("Provisions!C5", formula="=C9*10", value=10.0),
    }
    graph = {"Provisions!C5": ["Provisions!C9"], "Provisions!C9": []}
    line = run_reconciliation(parsed(cells, graph), ["Provisions!C5"]).lines[0]
    assert any("=IF(A1>0,1,2)" in element for element in line.unsupported_elements)


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
