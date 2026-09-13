"""Standalone tests for agents/comparison.py — the shared criteria engine.

Nothing here touches agents/reconciliation.py. This is validated in isolation
before Step 6 (SUMIF, the reference implementation) wires it in, because a bug
in this engine cascades into seven functions at once: IF, SUMIF, SUMIFS,
COUNTIF, COUNTIFS, AVERAGEIF, AVERAGEIFS.
"""

import pytest

from agents.comparison import ComparisonError, coerce_to_boolean, evaluate_criteria


# ---------------------------------------------------------------------------
# Numeric criteria (bare number, and "=" equality)
# ---------------------------------------------------------------------------


def test_bare_numeric_criteria_matches_equal_value():
    assert evaluate_criteria(100, 100) is True
    assert evaluate_criteria(100, 100.0) is True
    assert evaluate_criteria(99, 100) is False


def test_explicit_equals_operator():
    assert evaluate_criteria(100, "=100") is True
    assert evaluate_criteria(99, "=100") is False


# ---------------------------------------------------------------------------
# Comparison operators: <, <=, >, >=, <>
# ---------------------------------------------------------------------------


def test_greater_than():
    assert evaluate_criteria(150, ">100") is True
    assert evaluate_criteria(100, ">100") is False
    assert evaluate_criteria(50, ">100") is False


def test_greater_than_or_equal():
    assert evaluate_criteria(100, ">=100") is True
    assert evaluate_criteria(99, ">=100") is False


def test_less_than():
    assert evaluate_criteria(50, "<100") is True
    assert evaluate_criteria(100, "<100") is False


def test_less_than_or_equal():
    assert evaluate_criteria(100, "<=100") is True
    assert evaluate_criteria(101, "<=100") is False


def test_not_equal_operator():
    assert evaluate_criteria(0, "<>0") is False
    assert evaluate_criteria(5, "<>0") is True


def test_operator_token_precedence_does_not_misparse_longer_tokens():
    # "<=" must not be read as "<" followed by a stray "=100" operand.
    assert evaluate_criteria(100, "<=100") is True
    assert evaluate_criteria(100, ">=100") is True
    assert evaluate_criteria(5, "<>5") is False


def test_numeric_operators_require_numeric_value_not_text_fallback():
    # < > etc. never fall back to text comparison when one side isn't numeric.
    assert evaluate_criteria("apple", ">100") is False
    assert evaluate_criteria(150, ">apple") is False  # operand not numeric


# ---------------------------------------------------------------------------
# Wildcard text matching: *, ?, ~
# ---------------------------------------------------------------------------


def test_wildcard_star_matches_any_run_at_end():
    assert evaluate_criteria("apple", "app*") is True
    assert evaluate_criteria("application", "app*") is True
    assert evaluate_criteria("banana", "app*") is False


def test_wildcard_star_matches_any_run_at_start():
    assert evaluate_criteria("pineapple", "*apple") is True
    assert evaluate_criteria("apple", "*apple") is True
    assert evaluate_criteria("appl", "*apple") is False


def test_wildcard_star_matches_any_run_in_middle():
    assert evaluate_criteria("pineapple pie", "pine*pie") is True
    assert evaluate_criteria("pine pie", "pine*pie") is True
    assert evaluate_criteria("pineapple", "pine*pie") is False


def test_wildcard_question_mark_matches_exactly_one_character():
    assert evaluate_criteria("cat", "c?t") is True
    assert evaluate_criteria("coat", "c?t") is False  # two chars, not one
    assert evaluate_criteria("ct", "c?t") is False  # zero chars, not one


def test_wildcard_question_mark_multiple():
    assert evaluate_criteria("class1", "class?") is True
    assert evaluate_criteria("class12", "class?") is False


def test_tilde_escapes_literal_asterisk():
    assert evaluate_criteria("5*3=15", "5~*3=15") is True
    assert evaluate_criteria("5x3=15", "5~*3=15") is False


def test_tilde_escapes_literal_question_mark():
    assert evaluate_criteria("what?", "what~?") is True
    assert evaluate_criteria("whats", "what~?") is False


def test_tilde_escapes_literal_tilde():
    assert evaluate_criteria("a~b", "a~~b") is True


def test_wildcard_matching_is_case_insensitive():
    assert evaluate_criteria("APPLE", "app*") is True
    assert evaluate_criteria("Application", "APP*") is True


def test_plain_text_equality_is_case_insensitive():
    assert evaluate_criteria("Motor", "motor") is True
    assert evaluate_criteria("MOTOR", "Motor") is True
    assert evaluate_criteria("Property", "motor") is False


def test_not_equal_with_wildcard():
    assert evaluate_criteria("apple", "<>app*") is False
    assert evaluate_criteria("banana", "<>app*") is True


# ---------------------------------------------------------------------------
# Mixed-type coercion
# ---------------------------------------------------------------------------


def test_text_numeral_criteria_matches_numeric_value():
    # criteria "10" (text) matching numeric value 10
    assert evaluate_criteria(10, "10") is True
    assert evaluate_criteria(10.0, "10") is True


def test_numeric_criteria_matches_text_numeral_value():
    # numeric criteria 10 matching a text cell "10"
    assert evaluate_criteria("10", 10) is True
    assert evaluate_criteria("10", "=10") is True


def test_comparison_operator_coerces_text_numeral_value():
    assert evaluate_criteria("150", ">100") is True
    assert evaluate_criteria("50", ">100") is False


# ---------------------------------------------------------------------------
# Boundary cases: 0 vs empty, text "0" vs numeric 0
# ---------------------------------------------------------------------------


def test_zero_does_not_match_empty_criteria():
    assert evaluate_criteria(0, "") is False


def test_empty_value_does_not_match_zero_criteria():
    assert evaluate_criteria("", 0) is False
    assert evaluate_criteria(None, 0) is False


def test_empty_criteria_matches_blank_value():
    assert evaluate_criteria(None, "") is True
    assert evaluate_criteria("", "") is True


def test_text_zero_matches_numeric_zero():
    assert evaluate_criteria("0", 0) is True
    assert evaluate_criteria(0, "0") is True


def test_not_equal_zero_excludes_only_zero_not_blank():
    # <>0 is a common "non-zero rows" criteria; blank should not match it as
    # zero, since blank isn't numerically zero under our numeric-operator rule
    # (it takes the equality/text path since "<>" here has a numeric operand).
    assert evaluate_criteria(0, "<>0") is False
    assert evaluate_criteria(5, "<>0") is True
    assert evaluate_criteria(None, "<>0") is True  # blank != 0 numerically


# ---------------------------------------------------------------------------
# Boolean criteria and coerce_to_boolean
# ---------------------------------------------------------------------------


def test_boolean_criteria_matches_boolean_value():
    assert evaluate_criteria(True, True) is True
    assert evaluate_criteria(False, True) is False


def test_boolean_criteria_coerces_numeric_value():
    assert evaluate_criteria(1, True) is True
    assert evaluate_criteria(0, True) is False
    assert evaluate_criteria(0, False) is True


def test_coerce_to_boolean_numeric():
    assert coerce_to_boolean(0) is False
    assert coerce_to_boolean(0.0) is False
    assert coerce_to_boolean(5) is True
    assert coerce_to_boolean(-1) is True


def test_coerce_to_boolean_text_true_false_case_insensitive():
    assert coerce_to_boolean("TRUE") is True
    assert coerce_to_boolean("true") is True
    assert coerce_to_boolean("False") is False
    assert coerce_to_boolean("FALSE") is False


def test_coerce_to_boolean_empty_string_and_none_are_false():
    assert coerce_to_boolean("") is False
    assert coerce_to_boolean(None) is False


def test_coerce_to_boolean_numeric_text():
    assert coerce_to_boolean("0") is False
    assert coerce_to_boolean("3") is True


def test_coerce_to_boolean_arbitrary_text_raises():
    with pytest.raises(ComparisonError):
        coerce_to_boolean("banana")


# ---------------------------------------------------------------------------
# Cell-reference-built criteria (already-concatenated strings by the time
# they reach this engine — the shape SUMIF(A:A, ">"&B1, C:C) produces)
# ---------------------------------------------------------------------------


def test_criteria_built_from_concatenation_with_comparison_operator():
    threshold = 150  # stands in for a resolved B1 value
    criteria = ">" + str(threshold)
    assert evaluate_criteria(200, criteria) is True
    assert evaluate_criteria(100, criteria) is False


def test_criteria_built_from_concatenation_with_wildcard():
    prefix = "Motor"
    criteria = prefix + "*"
    assert evaluate_criteria("Motor Comprehensive", criteria) is True
    assert evaluate_criteria("Property", criteria) is False
