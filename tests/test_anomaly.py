"""Tests for agents/anomaly_detector.py — Agent 2.

The negative test for circular references is the important one here. A detector
that finds every real cycle and also flags every workbook where two tabs talk to
each other is not a detector; it is noise that trains a reviewer to click
"dismiss" without reading.
"""

from agents.anomaly_detector import detect_anomalies
from core.models import CellRecord, ParsedFile, WorkbookMeta


def _cell(ref, formula=None, value=None, data_type="number"):
    return CellRecord(
        cell_ref=ref,
        formula=formula,
        cached_value=value,
        data_type=data_type,
        number_format="General",
        is_error=False,
        error_type=None,
        is_stale=False,
        calculation_freshness="fresh",
    )


def _parsed(cells=None, named_ranges=None, cell_graph=None, tab_graph=None, tabs=None):
    cells = cells or {}
    return ParsedFile(
        tab_names=tabs or ["Provisions"],
        cells=cells,
        named_ranges=named_ranges or {},
        external_links=[],
        has_vba=False,
        workbook_meta=WorkbookMeta(calc_mode="automatic", workbook_hash="a" * 64),
        tab_dependency_graph=tab_graph or {},
        cell_dependency_graph=cell_graph or {},
        warnings=[],
    )


# ---------------------------------------------------------------------------
# 1. hardcoded literals
# ---------------------------------------------------------------------------


def test_hardcoded_literal_in_a_formula_is_flagged():
    parsed = _parsed({"Provisions!C5": _cell("Provisions!C5", formula="=A1*1.75", value=175.0)})
    findings = detect_anomalies(parsed)

    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert "1.75" in findings[0].description
    assert findings[0].tab == "Provisions"
    assert findings[0].cell_ref == "C5"


def test_literal_inside_a_larger_formula_is_flagged():
    parsed = _parsed(
        {"Provisions!C5": _cell("Provisions!C5", formula="=SUM(B1:B10)*0.035", value=3.5)}
    )
    findings = detect_anomalies(parsed)
    assert [f.description for f in findings if "0.035" in f.description]


def test_row_numbers_in_cell_references_are_not_mistaken_for_literals():
    """=SUM(B1:B10) contains "1" and "10" but no hardcoded assumption."""
    parsed = _parsed({"Provisions!C5": _cell("Provisions!C5", formula="=SUM(B1:B10)", value=55)})
    assert detect_anomalies(parsed) == []


def test_group_a_function_with_only_a_cell_reference_is_not_flagged():
    """Regression guard for Step 3 (ABS, INT): =ABS(C1) has no bare literal at
    all, so adding these functions to the reconstruction catalogue must not
    make the detector start seeing a hardcoded assumption where there is
    none."""
    parsed = _parsed(
        {
            "Provisions!C5": _cell("Provisions!C5", formula="=ABS(C1)", value=5.0),
            "Provisions!C6": _cell("Provisions!C6", formula="=INT(C1)", value=4.0),
        }
    )
    assert detect_anomalies(parsed) == []


def test_group_a_function_with_a_hardcoded_argument_is_still_flagged():
    """The other half of the guard: ABS/INT's "value" argument role is a
    business number, not a structural literal like a rounding digit count —
    a hardcoded number inside one must still be caught."""
    parsed = _parsed({"Provisions!C5": _cell("Provisions!C5", formula="=ABS(-5)", value=5.0)})
    findings = detect_anomalies(parsed)
    assert [f.description for f in findings if "5" in f.description]


def test_group_b_digit_count_and_significance_literals_are_not_flagged():
    """Regression guard for Step 4 (ROUND/ROUNDUP/ROUNDDOWN/CEILING/FLOOR):
    a rounding function's structural argument (a digit count, a significance)
    is not a hardcoded business assumption the way a bare multiplier is —
    ROUND(C1,2)'s "2" and CEILING(C1,0.5)'s "0.5" describe HOW to round, not
    a number someone typed in and forgot about."""
    parsed = _parsed(
        {
            "Provisions!C5": _cell("Provisions!C5", formula="=ROUND(C1,2)", value=1.23),
            "Provisions!C6": _cell("Provisions!C6", formula="=CEILING(C1,0.5)", value=1.5),
            "Provisions!C7": _cell("Provisions!C7", formula="=ROUNDDOWN(C1,-2)", value=1200.0),
        }
    )
    assert detect_anomalies(parsed) == []


def test_group_b_value_argument_hardcoded_literal_is_still_flagged():
    """The other half of the guard: ROUND's first argument (the "value" role)
    is a business number — a hardcoded one there must still be caught, the
    structural-role exclusion must not swallow it too."""
    parsed = _parsed({"Provisions!C5": _cell("Provisions!C5", formula="=ROUND(1.755,2)", value=1.76)})
    findings = detect_anomalies(parsed)
    descriptions = [f.description for f in findings]
    assert any("1.755" in d for d in descriptions)
    assert "Hardcoded literal 2 embedded in formula" not in descriptions


def test_group_b_digit_count_beyond_default_allowed_literals_is_not_flagged():
    """0, 1, and 100 were already excluded by _ALLOWED_LITERALS before this
    change. This test is about digit counts OUTSIDE that set — e.g. ROUND's
    "3" — which only the new argument-role awareness excludes."""
    parsed = _parsed({"Provisions!C5": _cell("Provisions!C5", formula="=ROUND(C1,3)", value=1.234)})
    assert detect_anomalies(parsed) == []


def test_sumif_criteria_threshold_literal_is_still_flagged():
    """The deliberate contrast with digit_count/significance: a SUMIF
    criteria like ">150" is a genuine business assumption (a materiality
    cutoff, a reserving threshold) hiding inside a formula, not a structural
    argument — "criteria" is intentionally excluded from
    _STRUCTURAL_ARG_ROLES, and this must stay true as Group C grows."""
    parsed = _parsed(
        {"Provisions!D1": _cell("Provisions!D1", formula='=SUMIF(B1:B3,">150",C1:C3)', value=5.0)}
    )
    findings = detect_anomalies(parsed)
    assert [f.description for f in findings if "150" in f.description]


def test_common_constants_are_not_flagged():
    parsed = _parsed(
        {
            "Provisions!C1": _cell("Provisions!C1", formula="=A1*1", value=5),
            "Provisions!C2": _cell("Provisions!C2", formula="=A1*100", value=500),
            "Provisions!C3": _cell("Provisions!C3", formula="=A1+0", value=5),
        }
    )
    assert detect_anomalies(parsed) == []


def test_literals_in_non_formula_cells_are_not_flagged():
    """A number typed into a cell is data. A number buried in a formula is an
    assumption nobody can see."""
    parsed = _parsed({"Provisions!A1": _cell("Provisions!A1", value=1.75)})
    assert detect_anomalies(parsed) == []


def test_several_literals_in_one_formula_each_get_a_finding():
    parsed = _parsed(
        {"Provisions!C5": _cell("Provisions!C5", formula="=A1*1.75+B1*2.5", value=10.0)}
    )
    assert len(detect_anomalies(parsed)) == 2


# ---------------------------------------------------------------------------
# 2. cross-tab inconsistency
# ---------------------------------------------------------------------------


def test_named_range_with_different_values_across_tabs_is_a_blocker():
    parsed = _parsed(
        cells={
            "Provisions!A1": _cell("Provisions!A1", value=0.0175),
            "Hypotheses!A1": _cell("Hypotheses!A1", value=0.0180),
        },
        named_ranges={
            "Provisions::taux_technique": "Provisions!$A$1",
            "Hypotheses::taux_technique": "Hypotheses!$A$1",
        },
        tabs=["Provisions", "Hypotheses"],
    )
    findings = detect_anomalies(parsed)

    assert len(findings) == 1
    assert findings[0].severity == "blocker"
    assert "taux_technique" in findings[0].description
    assert "0.0175" in findings[0].description and "0.018" in findings[0].description


def test_named_range_consistent_across_tabs_is_not_flagged():
    parsed = _parsed(
        cells={
            "Provisions!A1": _cell("Provisions!A1", value=0.0175),
            "Hypotheses!A1": _cell("Hypotheses!A1", value=0.0175),
        },
        named_ranges={
            "Provisions::taux_technique": "Provisions!$A$1",
            "Hypotheses::taux_technique": "Hypotheses!$A$1",
        },
        tabs=["Provisions", "Hypotheses"],
    )
    assert detect_anomalies(parsed) == []


def test_a_named_range_pointing_at_a_missing_cell_is_skipped_not_crashed():
    parsed = _parsed(
        named_ranges={"Provisions::ghost": "Provisions!$Z$99"},
        tabs=["Provisions"],
    )
    assert detect_anomalies(parsed) == []


def test_non_numeric_named_ranges_are_ignored():
    parsed = _parsed(
        cells={
            "Provisions!A1": _cell("Provisions!A1", value="IFRS 17", data_type="text"),
            "Hypotheses!A1": _cell("Hypotheses!A1", value="local GAAP", data_type="text"),
        },
        named_ranges={
            "Provisions::basis": "Provisions!$A$1",
            "Hypotheses::basis": "Hypotheses!$A$1",
        },
        tabs=["Provisions", "Hypotheses"],
    )
    assert detect_anomalies(parsed) == []


# ---------------------------------------------------------------------------
# 3. excluded rows in sums
# ---------------------------------------------------------------------------


def test_sum_skipping_a_row_is_flagged_with_the_skipped_cell():
    parsed = _parsed(
        {"Provisions!A11": _cell("Provisions!A11", formula="=SUM(A1:A5,A7:A10)", value=45)}
    )
    findings = detect_anomalies(parsed)

    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert "A6" in findings[0].description


def test_a_contiguous_sum_is_not_flagged():
    parsed = _parsed(
        {"Provisions!A11": _cell("Provisions!A11", formula="=SUM(A1:A5,A6:A10)", value=55)}
    )
    assert detect_anomalies(parsed) == []


def test_a_single_range_sum_is_not_flagged():
    parsed = _parsed({"Provisions!A11": _cell("Provisions!A11", formula="=SUM(A1:A10)", value=55)})
    assert detect_anomalies(parsed) == []


def test_single_range_ending_before_adjacent_populated_source_row_is_flagged():
    parsed = _parsed(
        {
            "Summary!B5": _cell(
                "Summary!B5", formula="=SUM('Cash Flow Calculation'!B4:B8)", value=98.0
            ),
            "Cash Flow Calculation!B9": _cell(
                "Cash Flow Calculation!B9", value=6.2
            ),
        },
        tabs=["Summary", "Cash Flow Calculation"],
    )

    findings = detect_anomalies(parsed)

    assert len(findings) == 1
    assert findings[0].cell_ref == "B5"
    assert "Cash Flow Calculation!B9" in findings[0].description


def test_total_immediately_below_its_own_single_range_is_not_flagged():
    parsed = _parsed(
        {
            "Provisions!A1": _cell("Provisions!A1", value=10.0),
            "Provisions!A2": _cell("Provisions!A2", value=20.0),
            "Provisions!A3": _cell("Provisions!A3", formula="=SUM(A1:A2)", value=30.0),
        }
    )

    assert detect_anomalies(parsed) == []


def test_adjacent_control_total_used_elsewhere_in_formula_is_not_flagged():
    parsed = _parsed(
        {
            "Controls!B6": _cell(
                "Controls!B6",
                formula="=AccountingBridge!C9-SUM(AccountingBridge!C4:C8)",
                value=0.0,
            ),
            "AccountingBridge!C9": _cell(
                "AccountingBridge!C9", formula="=SUM(C4:C8)", value=95.8
            ),
        },
        tabs=["Controls", "AccountingBridge"],
    )

    assert detect_anomalies(parsed) == []


def test_explicit_range_plus_scalar_sum_is_not_treated_as_single_range():
    parsed = _parsed(
        {
            "Provisions!B10": _cell("Provisions!B10", value=50.0),
            "Provisions!B12": _cell(
                "Provisions!B12", formula="=SUM(B8:B9,B11)", value=100.0
            ),
        }
    )

    assert detect_anomalies(parsed) == []


def test_several_skipped_rows_are_all_named():
    parsed = _parsed(
        {"Provisions!A11": _cell("Provisions!A11", formula="=SUM(A1:A3,A7:A10)", value=40)}
    )
    description = detect_anomalies(parsed)[0].description
    assert "A4" in description and "A5" in description and "A6" in description


# ---------------------------------------------------------------------------
# 4. circular references — the cell graph, and only the cell graph
# ---------------------------------------------------------------------------


def test_a_genuine_cell_level_cycle_is_a_blocker():
    """Provisions!B5 -> Hypotheses!B3 -> Provisions!B5. A real cycle."""
    parsed = _parsed(
        cells={
            "Provisions!B5": _cell("Provisions!B5", formula="=Hypotheses!B3*2", value=None),
            "Hypotheses!B3": _cell("Hypotheses!B3", formula="=Provisions!B5+1", value=None),
        },
        cell_graph={
            "Provisions!B5": ["Hypotheses!B3"],
            "Hypotheses!B3": ["Provisions!B5"],
        },
        tab_graph={"Provisions": ["Hypotheses"], "Hypotheses": ["Provisions"]},
        tabs=["Provisions", "Hypotheses"],
    )
    cycles = [f for f in detect_anomalies(parsed) if "Circular" in f.description]

    assert len(cycles) == 1
    assert cycles[0].severity == "blocker"
    # Every cell in the cycle is named — "there is a cycle somewhere" is not
    # something a reviewer can act on.
    assert "Provisions!B5" in cycles[0].description
    assert "Hypotheses!B3" in cycles[0].description


def test_two_tabs_referencing_each_other_without_a_cell_cycle_is_not_flagged():
    """THE false-positive case. Provisions!A1 reads Hypotheses!A1, and
    Hypotheses!B1 reads Provisions!B1 — two separate, one-way chains. The
    tab-level graph shows a loop. Nothing here is circular.

    A detector built on tab_dependency_graph flags this workbook as a blocker
    and stops a perfectly sound audit."""
    parsed = _parsed(
        cells={
            "Provisions!A1": _cell("Provisions!A1", formula="=Hypotheses!A1", value=1),
            "Hypotheses!B1": _cell("Hypotheses!B1", formula="=Provisions!B1", value=2),
            "Hypotheses!A1": _cell("Hypotheses!A1", value=1),
            "Provisions!B1": _cell("Provisions!B1", value=2),
        },
        cell_graph={
            "Provisions!A1": ["Hypotheses!A1"],
            "Hypotheses!B1": ["Provisions!B1"],
            "Hypotheses!A1": [],
            "Provisions!B1": [],
        },
        # The coarse graph genuinely does contain a loop. It is not evidence.
        tab_graph={"Provisions": ["Hypotheses"], "Hypotheses": ["Provisions"]},
        tabs=["Provisions", "Hypotheses"],
    )
    findings = detect_anomalies(parsed)
    assert [f for f in findings if "Circular" in f.description] == []


def test_a_cell_referring_to_itself_is_flagged():
    parsed = _parsed(
        cells={"Provisions!A1": _cell("Provisions!A1", formula="=A1+1", value=None)},
        cell_graph={"Provisions!A1": ["Provisions!A1"]},
    )
    findings = [f for f in detect_anomalies(parsed) if "itself" in f.description]
    assert len(findings) == 1
    assert findings[0].severity == "blocker"


def test_a_long_cycle_lists_every_cell_in_it():
    parsed = _parsed(
        cell_graph={
            "Provisions!A1": ["Provisions!A2"],
            "Provisions!A2": ["Provisions!A3"],
            "Provisions!A3": ["Provisions!A1"],
        }
    )
    finding = [f for f in detect_anomalies(parsed) if "Circular" in f.description][0]
    for ref in ("Provisions!A1", "Provisions!A2", "Provisions!A3"):
        assert ref in finding.description


def test_an_acyclic_chain_is_not_flagged():
    parsed = _parsed(
        cell_graph={
            "Provisions!A1": ["Provisions!A2"],
            "Provisions!A2": ["Provisions!A3"],
            "Provisions!A3": [],
        }
    )
    assert detect_anomalies(parsed) == []


# ---------------------------------------------------------------------------
# output shape
# ---------------------------------------------------------------------------


def test_findings_are_ranked_blockers_first_and_ids_follow_that_order():
    parsed = _parsed(
        cells={
            "Provisions!C5": _cell("Provisions!C5", formula="=A1*1.75", value=1.75),
            "Provisions!A1": _cell("Provisions!A1", value=0.0175),
            "Hypotheses!A1": _cell("Hypotheses!A1", value=0.0180),
        },
        named_ranges={
            "Provisions::taux": "Provisions!$A$1",
            "Hypotheses::taux": "Hypotheses!$A$1",
        },
        tabs=["Provisions", "Hypotheses"],
    )
    findings = detect_anomalies(parsed)

    assert [f.severity for f in findings] == ["blocker", "warning"]
    assert [f.finding_id for f in findings] == ["F0001", "F0002"]


def test_findings_arrive_undecided():
    """Gate 2 assigns dispositions. A finding that arrives pre-decided would
    make the gate ceremonial."""
    parsed = _parsed({"Provisions!C5": _cell("Provisions!C5", formula="=A1*1.75", value=1.75)})
    finding = detect_anomalies(parsed)[0]

    assert finding.human_decision is None
    assert finding.human_reason is None
    assert finding.decided_by is None
    assert finding.decided_at is None


def test_an_empty_workbook_produces_no_findings():
    assert detect_anomalies(_parsed()) == []


def test_a_workbook_of_only_stale_formulas_still_gets_scanned():
    """Anomaly detection reads formulas, so it works on a workbook that was
    never recalculated — unlike anything that needs cached values."""
    parsed = _parsed(
        {"Provisions!C5": _cell("Provisions!C5", formula="=A1*1.75", value=None, data_type="blank")}
    )
    assert len(detect_anomalies(parsed)) == 1
