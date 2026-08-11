"""Tests for core/verdict_logic.py — the single shared definition of "pass".

Both thresholds are always evaluated and the worse outcome wins. Most of these
tests exist to pin that down, because a version that checked only one threshold
would pass a great many of the obvious cases.
"""

import pytest

from core.verdict_logic import compute_verdict

# Bands under these: pct pass < 0.001, warn < 0.01, block above.
#                    abs pass < 10.0,  warn < 100.0, block above.
PCT = 0.01
ABS = 100.0


def verdict(delta, delta_pct, completeness="complete", ambiguous=False):
    return compute_verdict(delta, delta_pct, PCT, ABS, completeness, ambiguous)


# ---------------------------------------------------------------------------
# incomplete beats everything
# ---------------------------------------------------------------------------


def test_partial_reconstruction_is_incomplete_however_close_the_numbers():
    """Two figures agreeing exactly proves nothing if only part of the
    calculation was reconstructed."""
    assert verdict(0.0, 0.0, completeness="partial") == "incomplete"


def test_missing_delta_is_incomplete_not_pass():
    assert verdict(None, None) == "incomplete"
    assert verdict(5.0, None) == "incomplete"
    assert verdict(None, 0.001) == "incomplete"


# ---------------------------------------------------------------------------
# the two thresholds, and the worse of them
# ---------------------------------------------------------------------------


def test_both_within_the_inner_band_is_a_pass():
    assert verdict(5.0, 0.0005) == "pass"


def test_percentage_in_the_warn_band_produces_warn():
    assert verdict(5.0, 0.005) == "warn"


def test_absolute_in_the_warn_band_produces_warn():
    assert verdict(50.0, 0.0005) == "warn"


def test_percentage_over_threshold_blocks():
    assert verdict(5.0, 0.02) == "block"


def test_absolute_over_threshold_blocks():
    assert verdict(500.0, 0.0005) == "block"


def test_a_tiny_percentage_of_a_huge_number_still_blocks_on_absolute():
    """0.05% of 10,000,000 is 5,000 — immaterial as a ratio, very material as
    money. Checking percentage alone would call this a pass."""
    assert verdict(5000.0, 0.0005) == "block"


def test_a_small_absolute_gap_on_a_tiny_number_still_blocks_on_percentage():
    """9 against a base of 100 is under the absolute floor but 9% out.
    Checking absolute alone would call this a pass."""
    assert verdict(9.0, 0.09) == "block"


def test_the_worse_of_the_two_always_wins():
    assert verdict(5.0, 0.02) == "block"  # pct block, abs pass
    assert verdict(500.0, 0.0001) == "block"  # abs block, pct pass
    assert verdict(50.0, 0.005) == "warn"  # both warn


def test_exactly_on_a_boundary_takes_the_worse_side():
    """Bands are strict less-than, so landing exactly on a threshold is the
    worse outcome. A boundary case should not resolve in the report's favour."""
    assert verdict(5.0, PCT) == "block"
    assert verdict(ABS, 0.0005) == "block"
    assert verdict(5.0, PCT / 10) == "warn"
    assert verdict(ABS / 10, 0.0005) == "warn"


# ---------------------------------------------------------------------------
# ambiguous matches
# ---------------------------------------------------------------------------


def test_an_ambiguous_match_cannot_be_a_pass():
    """If nobody is sure these are the same figure, their agreement is not
    evidence of anything."""
    assert verdict(0.0, 0.0) == "pass"
    assert verdict(0.0, 0.0, ambiguous=True) == "warn"


def test_an_ambiguous_match_does_not_soften_a_worse_verdict():
    assert verdict(5.0, 0.02, ambiguous=True) == "block"
    assert verdict(50.0, 0.005, ambiguous=True) == "warn"


def test_an_ambiguous_partial_is_still_incomplete():
    assert verdict(0.0, 0.0, completeness="partial", ambiguous=True) == "incomplete"


# ---------------------------------------------------------------------------
# thresholds are parameters, never baked in
# ---------------------------------------------------------------------------


def test_a_looser_threshold_changes_the_verdict():
    assert compute_verdict(5.0, 0.02, 0.01, 100.0, "complete") == "block"
    assert compute_verdict(5.0, 0.02, 0.05, 100.0, "complete") == "warn"


def test_a_zero_delta_passes_under_any_threshold():
    assert compute_verdict(0.0, 0.0, 0.0001, 1.0, "complete") == "pass"


@pytest.mark.parametrize("delta_pct", [0.0, 0.0009, 0.001, 0.009, 0.01, 0.5])
def test_every_verdict_is_one_of_the_four_states(delta_pct):
    assert verdict(1.0, delta_pct) in {"pass", "warn", "block", "incomplete"}
