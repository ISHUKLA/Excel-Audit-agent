"""Tests for _detect_negative_reserve_bounds() in agents/anomaly_detector.py.

Case 4 is the messy-input case required by CLAUDE.md's Diligence rule: a
formula cell in a reserve-context tab whose cached_value is None (never
recalculated). See that test's docstring for what the heuristic does with it
and why.
"""

from agents.anomaly_detector import detect_anomalies
from core.models import CellRecord, ParsedFile, WorkbookMeta


def _cell(ref, formula=None, value=None, data_type="number", calculation_freshness="fresh"):
    return CellRecord(
        cell_ref=ref,
        formula=formula,
        cached_value=value,
        data_type=data_type,
        number_format="General",
        is_error=False,
        error_type=None,
        is_stale=calculation_freshness != "fresh",
        calculation_freshness=calculation_freshness,
    )


def _parsed(cells=None, named_ranges=None, tabs=None, calc_mode="automatic"):
    cells = cells or {}
    return ParsedFile(
        tab_names=tabs or ["Reserves"],
        cells=cells,
        named_ranges=named_ranges or {},
        external_links=[],
        has_vba=False,
        workbook_meta=WorkbookMeta(calc_mode=calc_mode, workbook_hash="a" * 64),
        tab_dependency_graph={},
        cell_dependency_graph={},
        warnings=[],
    )


def test_negative_value_in_reserve_context_with_no_reinsurance_term_is_flagged():
    parsed = _parsed(
        {"Reserves!B2": _cell("Reserves!B2", formula="=A1-A2", value=-500.0)},
        tabs=["Reserves"],
    )
    findings = detect_anomalies(parsed)

    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert findings[0].tab == "Reserves"
    assert findings[0].cell_ref == "B2"
    assert "-500" in findings[0].description


def test_negative_value_with_reinsurance_term_in_formula_is_not_flagged():
    parsed = _parsed(
        {
            "Reserves!B2": _cell(
                "Reserves!B2", formula="=Gross_Reserve-Reinsurance_Recovery", value=-200.0
            )
        },
        tabs=["Reserves"],
    )
    assert detect_anomalies(parsed) == []


def test_positive_value_in_reserve_context_is_never_flagged():
    parsed = _parsed(
        {"Reserves!B2": _cell("Reserves!B2", formula="=A1+A2", value=500.0)},
        tabs=["Reserves"],
    )
    assert detect_anomalies(parsed) == []


def test_never_recalculated_formula_in_reserve_context_is_not_flagged():
    """MESSY INPUT, per CLAUDE.md's Diligence rule.

    A formula cell in a reserve-context tab with cached_value=None — the
    workbook was never recalculated after this formula was entered (see
    CellRecord's docstring: cached_value may legitimately be None, and
    is_stale/calculation_freshness is what records that fact, not the
    formula/value choice itself). Here calc_mode="manual" and
    calculation_freshness="stale" make that explicit.

    _detect_negative_reserve_bounds() requires `isinstance(value, (int,
    float))` before it will test the sign at all, so a None cached_value is
    skipped, not coerced into 0 or crashed on. No finding is produced.

    Is that correct? Yes, for what this heuristic claims to check: it tests
    the SIGN of a cached value, and there is no cached value here to have a
    sign. Producing a finding would mean asserting something about a number
    that does not exist. Silently producing zero findings is the same
    "cannot compute" case _detect_cross_tab_inconsistencies already accepts
    for a named range with no resolvable value.

    This IS a real coverage gap worth naming explicitly (Discernment rule:
    "the numbers don't match" is not the same as "I'm not confident this is
    even the right comparison") — a workbook left in manual calc mode with a
    genuinely negative, never-recalculated reserve formula produces NO
    warning from this detector, silently. That is a scope note for a future
    step (e.g. cross-checking calculation_freshness the way
    ReconciliationLine.calculation_evidence_status does), not something to
    silently patch into this heuristic now.
    """
    parsed = _parsed(
        {
            "Reserves!B2": _cell(
                "Reserves!B2",
                formula="=A1-A2",
                value=None,
                data_type="blank",
                calculation_freshness="stale",
            )
        },
        tabs=["Reserves"],
        calc_mode="manual",
    )
    assert detect_anomalies(parsed) == []
