"""Standalone tests for core/numeric_utils.py — the shared rounding kernels.

Nothing here touches agents/reconciliation.py. This module is validated in
isolation before Step 4 wires it into ROUND/ROUNDUP/ROUNDDOWN/CEILING/FLOOR,
so that a rounding bug is caught here, not diagnosed later through five
formula-level test failures at once.
"""

from core.numeric_utils import (
    ceiling_to_significance,
    excel_round,
    floor_to_significance,
    rounddown_to_significance,
    roundup_to_significance,
)


# ---------------------------------------------------------------------------
# excel_round — half AWAY FROM ZERO, never banker's rounding
# ---------------------------------------------------------------------------


def test_round_positive_half_tie_rounds_up():
    assert excel_round(0.5, 0) == 1.0
    assert excel_round(1.5, 0) == 2.0
    assert excel_round(2.5, 0) == 3.0  # NOT 2 — this is where banker's rounding differs


def test_round_negative_half_tie_rounds_away_from_zero():
    assert excel_round(-0.5, 0) == -1.0
    assert excel_round(-1.5, 0) == -2.0
    assert excel_round(-2.5, 0) == -3.0


def test_round_non_tie_values():
    assert excel_round(1.234, 2) == 1.23
    assert excel_round(1.235, 2) == 1.24
    assert excel_round(-1.234, 2) == -1.23
    assert excel_round(-1.235, 2) == -1.24


def test_round_negative_digits_rounds_to_tens_hundreds():
    assert excel_round(1250, -2) == 1300.0
    assert excel_round(1240, -2) == 1200.0
    assert excel_round(-1250, -2) == -1300.0


def test_round_zero_and_tiny_values():
    assert excel_round(0.0, 2) == 0.0
    assert excel_round(0.00001, 2) == 0.0
    assert excel_round(-0.00001, 2) == 0.0


# ---------------------------------------------------------------------------
# ROUNDUP — always away from zero
# ---------------------------------------------------------------------------


def test_roundup_positive_away_from_zero():
    assert roundup_to_significance(1.1, 0) == 2.0
    assert roundup_to_significance(1.9, 0) == 2.0
    assert roundup_to_significance(1.234, 2) == 1.24


def test_roundup_negative_away_from_zero():
    # The asymmetry-clarifying case: -1.1 rounds to -2, not -1.
    assert roundup_to_significance(-1.1, 0) == -2.0
    assert roundup_to_significance(-1.234, 2) == -1.24


def test_roundup_half_tie_rounds_away_like_round():
    assert roundup_to_significance(0.5, 0) == 1.0
    assert roundup_to_significance(-0.5, 0) == -1.0


def test_roundup_boundary_zero_and_tiny():
    assert roundup_to_significance(0.0, 2) == 0.0
    assert roundup_to_significance(0.0001, 2) == 0.01
    assert roundup_to_significance(-0.0001, 2) == -0.01


# ---------------------------------------------------------------------------
# ROUNDDOWN — always toward zero (truncation)
# ---------------------------------------------------------------------------


def test_rounddown_positive_toward_zero():
    assert rounddown_to_significance(1.9, 0) == 1.0
    assert rounddown_to_significance(1.234, 2) == 1.23


def test_rounddown_negative_toward_zero():
    # The asymmetry-clarifying case: -1.9 truncates to -1, not -2.
    assert rounddown_to_significance(-1.9, 0) == -1.0
    assert rounddown_to_significance(-1.234, 2) == -1.23


def test_rounddown_half_tie_still_truncates():
    assert rounddown_to_significance(0.5, 0) == 0.0
    assert rounddown_to_significance(-0.5, 0) == 0.0


def test_rounddown_boundary_zero_and_tiny():
    assert rounddown_to_significance(0.0, 2) == 0.0
    assert rounddown_to_significance(0.0099, 2) == 0.0
    assert rounddown_to_significance(-0.0099, 2) == 0.0


def test_roundup_rounddown_mixed_sign_asymmetry_in_one_fixture():
    """The single fixture that makes the up/down-vs-away/toward distinction explicit."""
    assert roundup_to_significance(1.5, 0) == 2.0
    assert rounddown_to_significance(1.5, 0) == 1.0
    assert roundup_to_significance(-1.5, 0) == -2.0
    assert rounddown_to_significance(-1.5, 0) == -1.0


# ---------------------------------------------------------------------------
# CEILING — away from zero, to the nearest MULTIPLE of significance
# ---------------------------------------------------------------------------


def test_ceiling_significance_not_digit_count():
    # The load-bearing case: CEILING(1.23, 0.5) == 1.5, NOT 2 (which a
    # digit-count misreading of "0.5" would wrongly suggest).
    assert ceiling_to_significance(1.23, 0.5) == 1.5


def test_ceiling_significance_of_ten():
    assert ceiling_to_significance(12.0, 10) == 20.0
    assert ceiling_to_significance(10.0, 10) == 10.0  # already a multiple


def test_ceiling_negative_value_rounds_away_from_zero():
    assert ceiling_to_significance(-1.23, 0.5) == -1.5


def test_ceiling_significance_zero_returns_zero():
    # Documented Excel behavior, not a domain error: CEILING(x, 0) == 0.
    assert ceiling_to_significance(1.23, 0) == 0.0
    assert ceiling_to_significance(-1.23, 0) == 0.0


def test_ceiling_negative_value_with_positive_significance_matches_excel():
    # The Step 14 hand-checked value: CEILING(-0.5, 1) == -1, not a #NUM!
    # error and not +1 — significance's own sign doesn't have to match
    # value's sign for plain CEILING/FLOOR.
    assert ceiling_to_significance(-0.5, 1) == -1.0


def test_ceiling_boundary_zero_value():
    assert ceiling_to_significance(0.0, 0.5) == 0.0


def test_ceiling_tiny_value_rounds_up_to_first_multiple():
    assert ceiling_to_significance(0.0001, 0.5) == 0.5


# ---------------------------------------------------------------------------
# FLOOR — toward zero, to the nearest MULTIPLE of significance
# ---------------------------------------------------------------------------


def test_floor_significance_not_digit_count():
    assert floor_to_significance(1.23, 0.5) == 1.0


def test_floor_significance_of_ten():
    assert floor_to_significance(12.0, 10) == 10.0
    assert floor_to_significance(10.0, 10) == 10.0


def test_floor_negative_value_rounds_toward_zero():
    assert floor_to_significance(-1.23, 0.5) == -1.0


def test_floor_significance_zero_returns_zero():
    assert floor_to_significance(1.23, 0) == 0.0
    assert floor_to_significance(-1.23, 0) == 0.0


def test_floor_boundary_zero_value():
    assert floor_to_significance(0.0, 0.5) == 0.0


def test_floor_tiny_value_rounds_down_to_zero():
    assert floor_to_significance(0.0001, 0.5) == 0.0


def test_ceiling_floor_mixed_sign_asymmetry_in_one_fixture():
    """The single fixture that makes CEILING/FLOOR's sign symmetry explicit."""
    assert ceiling_to_significance(1.23, 0.5) == 1.5
    assert floor_to_significance(1.23, 0.5) == 1.0
    assert ceiling_to_significance(-1.23, 0.5) == -1.5
    assert floor_to_significance(-1.23, 0.5) == -1.0
