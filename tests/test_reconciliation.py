"""Tests for agents/reconciliation.py: both the Excel-vs-Python and Python-vs-accounts passes."""

from datetime import datetime

from agents.reconciliation import apply_thresholds, run_reconciliation
from core.models import FileContext, ParsedFile, ReconciliationLine, ReferenceFigures


def _parsed_file(extra_cells=None, extra_cached=None) -> ParsedFile:
    cells = {
        "Reserves!A2": "Net premium reserves",
        "Reserves!B1": 1000,
        "Reserves!B2": "=B1*1.25",
    }
    cached_values = {
        "Reserves!B1": 1000,
        "Reserves!B2": 1250,
    }
    if extra_cells:
        cells.update(extra_cells)
    if extra_cached:
        cached_values.update(extra_cached)

    return ParsedFile(
        tab_names=["Reserves"],
        cells=cells,
        cached_values=cached_values,
        named_ranges={},
        external_links=[],
        has_vba=False,
        dependency_graph={"Reserves": []},
        warnings=[],
    )


def _file_context() -> FileContext:
    return FileContext(
        filename="reserves.xlsx",
        description="Q4 reserve calculation; final output values are on the Reserves tab",
        user_role="actuary",
        uploaded_at=datetime.now(),
    )


def _reference_figures(**line_items) -> ReferenceFigures:
    return ReferenceFigures(
        source_label="Q4 trial balance extract",
        line_items=line_items,
        uploaded_at=datetime.now(),
    )


def test_pass1_alone_produces_only_excel_vs_python_lines():
    lines, unmatched = run_reconciliation(_parsed_file(), _file_context())

    assert unmatched == []
    assert len(lines) == 1
    assert lines[0].check_type == "excel_vs_python"
    assert lines[0].label == "Net premium reserves"
    assert lines[0].source_value == 1250.0
    assert lines[0].target_value == 1250.0
    assert lines[0].verdict == "pass"


def test_both_passes_with_clean_matching_labels_use_correct_check_type_and_threshold():
    reference = _reference_figures(**{"Net premium reserves": 1250.0})

    lines, unmatched = run_reconciliation(
        _parsed_file(),
        _file_context(),
        reference_figures=reference,
        internal_threshold=0.02,
        external_threshold=0.05,
    )

    assert unmatched == []
    internal = [line for line in lines if line.check_type == "excel_vs_python"]
    external = [line for line in lines if line.check_type == "python_vs_accounts"]

    assert len(internal) == 1
    assert len(external) == 1
    assert internal[0].materiality_threshold == 0.02
    assert external[0].materiality_threshold == 0.05
    assert internal[0].verdict == "pass"
    assert external[0].verdict == "pass"
    assert external[0].label == "Net premium reserves"
    assert external[0].source_value == internal[0].target_value
    assert external[0].target_value == 1250.0


def test_messy_acronym_label_still_finds_a_match():
    reference = _reference_figures(**{"NPR Total": 1250.0})

    lines, unmatched = run_reconciliation(_parsed_file(), _file_context(), reference_figures=reference)

    assert "NPR Total" not in unmatched
    external = [line for line in lines if line.check_type == "python_vs_accounts"]
    assert len(external) == 1
    assert external[0].label == "Net premium reserves"
    # Whether this specific pairing lands "confident" or "ambiguous" depends on
    # the matcher's exact scoring -- either is an acceptable outcome, but if
    # it's ambiguous it must be correctly flagged as such, not silently passed.
    if external[0].match_note is not None:
        assert external[0].verdict == "warn"


def test_ambiguous_match_forces_warn_verdict_regardless_of_numeric_delta():
    # "Gross premium reserves" is a plausible-but-wrong label match for
    # "Net premium reserves" (same structure, different qualifier) -- similar
    # enough to surface as a candidate, not similar enough to be confident.
    reference = _reference_figures(**{"Gross premium reserves": 1250.0})

    lines, unmatched = run_reconciliation(_parsed_file(), _file_context(), reference_figures=reference)

    external = [line for line in lines if line.check_type == "python_vs_accounts"]
    assert len(external) == 1
    assert external[0].delta == 0.0  # numerically a perfect match
    assert external[0].verdict == "warn"  # forced warn: the pairing itself is uncertain
    assert external[0].match_note is not None
    assert "Gross premium reserves" not in unmatched


def test_unmatched_reference_item_is_not_fabricated_into_a_comparison():
    reference = _reference_figures(**{"Completely unrelated currency adjustment": 999.0})

    lines, unmatched = run_reconciliation(_parsed_file(), _file_context(), reference_figures=reference)

    assert unmatched == ["Completely unrelated currency adjustment"]
    assert not any(line.check_type == "python_vs_accounts" for line in lines)


def _line(**overrides) -> ReconciliationLine:
    defaults = dict(
        check_type="excel_vs_python",
        label="Net premium reserves",
        source_value=1000.0,
        target_value=1004.0,
        delta=4.0,
        delta_pct=0.004,
        verdict="pass",
        materiality_threshold=0.01,
    )
    defaults.update(overrides)
    return ReconciliationLine(**defaults)


def test_apply_thresholds_reclassifies_verdict_using_new_threshold():
    # delta_pct=0.004 (0.4%): "pass" under a 1% threshold (0.4% < 0.1%? no --
    # 0.4% >= 0.1% and < 1%, so this was already "warn"). Use a much tighter
    # threshold so the same line reclassifies down to "block".
    line = _line(check_type="excel_vs_python", verdict="warn", materiality_threshold=0.01)

    reclassified = apply_thresholds([line], internal_threshold=0.001, external_threshold=0.01)

    assert reclassified[0].verdict == "block"
    assert reclassified[0].materiality_threshold == 0.001
    # The original line object is untouched -- reclassification returns new lines.
    assert line.verdict == "warn"


def test_apply_thresholds_keeps_ambiguous_match_forced_to_warn():
    line = _line(
        check_type="python_vs_accounts",
        delta=0.0,
        delta_pct=0.0,
        verdict="warn",
        match_note="Ambiguous match (72% similarity)...",
    )

    # A very loose threshold would normally reclassify a zero-delta line to "pass".
    reclassified = apply_thresholds([line], internal_threshold=0.01, external_threshold=0.5)

    assert reclassified[0].verdict == "warn"
    assert reclassified[0].match_note == line.match_note
