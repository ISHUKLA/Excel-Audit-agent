"""Shared comparison / criteria engine — the load-bearing correctness point for
seven formula-reconstruction functions: IF, SUMIF, SUMIFS, COUNTIF, COUNTIFS,
AVERAGEIF, AVERAGEIFS.

One parser here means one place these functions can disagree with each other
about what "matches" means. Reimplementing criteria parsing independently in
each function is how SUMIF and COUNTIF quietly drift apart on the same range.

Excel criteria come in three shapes, and this module treats them as three
genuinely different cases rather than one fuzzy string check:

  1. A bare value (number, bool, or already-resolved cell value) — matched by
     equality, with numeric/text coercion (Section 3 below).
  2. A comparison-operator string ("<", "<=", ">", ">=", "<>", "=" followed by
     an operand) — matched numerically for the four inequality operators, by
     coercion-aware equality for "=" and "<>".
  3. A text pattern with Excel wildcards (`*` any run of characters, `?`
     exactly one character, `~` escapes the following character literally) —
     matched case-insensitively. Wildcards only apply to equality/inequality
     ("=", "<>", or no operator at all), never to <, <=, >, >=.

Nothing in this module is wired into agents/reconciliation.py yet. It is
validated standalone first (tests/test_comparison.py) because a bug here
cascades silently into seven functions at once — see the Step 2B note in the
module's own test file for what "done" means before that wiring happens.
"""

import operator
import re
from typing import Optional, Union

CriteriaValue = Union[str, float, int, bool, None]


class ComparisonError(ValueError):
    """Raised when a value cannot be coerced the way Excel's boolean context
    requires (Excel's own behavior here is #VALUE!, not a silent False)."""


_OPERATOR_TOKENS = ("<=", ">=", "<>", "<", ">", "=")

_NUMERIC_OPS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


# ---------------------------------------------------------------------------
# Boolean coercion — Excel's TRUE/FALSE rules, shared with IF's condition path
# ---------------------------------------------------------------------------


def coerce_to_boolean(value: CriteriaValue) -> bool:
    """Excel's truthy/falsy coercion.

    0 is FALSE; any other number is TRUE. Text "TRUE"/"FALSE" (any case)
    coerces to the matching bool. Empty string (and None, standing in for a
    blank cell) is FALSE. A numeric-looking string coerces through its
    numeric value (e.g. "0" -> FALSE, "3" -> TRUE) rather than being treated
    as non-empty text. Any other, non-numeric, non-TRUE/FALSE text has no
    boolean meaning in Excel — a real formula there raises #VALUE!, so this
    raises ComparisonError rather than guessing True or False.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return False
        if text.upper() == "TRUE":
            return True
        if text.upper() == "FALSE":
            return False
        try:
            return float(text) != 0
        except ValueError:
            raise ComparisonError(
                f"{value!r} has no boolean meaning in Excel (would be #VALUE!)"
            )
    raise ComparisonError(f"{value!r} has no boolean meaning in Excel")


# ---------------------------------------------------------------------------
# Numeric coercion
# ---------------------------------------------------------------------------


def _try_number(value: CriteriaValue) -> Optional[float]:
    """Best-effort numeric coercion. None means "not a number", not zero.

    Deliberately excludes bool: TRUE/FALSE take the boolean-equality path in
    _matches_equality rather than silently becoming 1.0/0.0 here. Excel does
    coerce booleans to 1/0 in some numeric contexts, but conflating that with
    plain numeric criteria matching is not required by any function in this
    catalogue and would blur a distinction (true boolean equality vs. numeric
    comparison) worth keeping explicit.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _to_comparable_text(value: CriteriaValue) -> str:
    """Blank cells and None both compare as empty text, matching Excel's
    treatment of a blank cell as equivalent to criteria ""."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ---------------------------------------------------------------------------
# Wildcard matching — *, ?, ~ escape
# ---------------------------------------------------------------------------


def _wildcard_to_regex(pattern: str) -> "re.Pattern[str]":
    """Compile an Excel wildcard pattern to a case-insensitive, anchored regex.

    * -> any run of characters (including none). ? -> exactly one character.
    ~ escapes the next character literally, so ~* is a literal asterisk, ~?
    a literal question mark, and ~~ a literal tilde.
    """
    parts = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "~" and i + 1 < n:
            parts.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if ch == "*":
            parts.append(".*")
        elif ch == "?":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(parts) + "$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Criteria parsing
# ---------------------------------------------------------------------------


def _split_operator(criteria_text: str) -> tuple[str, str]:
    """Split a leading comparison operator off a criteria string.

    Longer tokens are checked first so "<=" is never misread as "<" followed
    by a stray "=". No operator present means the implicit operator is "="
    over the whole string (SUMIF(range, "apple", ...) has no explicit "=").
    """
    for token in _OPERATOR_TOKENS:
        if criteria_text.startswith(token):
            return token, criteria_text[len(token):]
    return "=", criteria_text


def _parse_criteria(criteria: CriteriaValue) -> tuple[str, Union[str, float, bool], bool]:
    """Return (operator, operand, operand_is_number).

    `operand` is a bool, a float, or the remaining text — never the original
    unparsed string once a numeric or boolean reading is available, so every
    downstream branch works with an already-typed value.
    """
    if isinstance(criteria, bool):
        return "=", criteria, False
    if isinstance(criteria, (int, float)):
        return "=", float(criteria), True

    criteria_text = "" if criteria is None else str(criteria)
    op, operand_text = _split_operator(criteria_text)
    operand_number = _try_number(operand_text)
    if operand_number is not None:
        return op, operand_number, True
    return op, operand_text, False


def _matches_equality(value: CriteriaValue, operand: Union[str, float, bool], operand_is_number: bool) -> bool:
    if isinstance(operand, bool):
        return coerce_to_boolean(value) == operand
    if operand_is_number:
        value_number = _try_number(value)
        return value_number is not None and value_number == operand
    text_value = _to_comparable_text(value)
    if "~" in operand or "*" in operand or "?" in operand:
        # Route through the regex translator even when every * or ? turns out
        # to be escaped (e.g. "5~*3=15" with no live wildcard) — the operand
        # still needs unescaping (~* -> literal *) before comparison, which
        # plain casefold-equality on the raw operand text would skip.
        return bool(_wildcard_to_regex(operand).match(text_value))
    return text_value.casefold() == operand.casefold()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_criteria(value: CriteriaValue, criteria: CriteriaValue) -> bool:
    """True if `value` satisfies `criteria`, per Excel's *IF-family rules.

    `criteria` is whatever a formula's criteria argument resolved to — a
    number, a bool, or a string (which may itself have been built by
    concatenating a comparison operator onto a cell reference, e.g.
    `">"&B1`; by the time it reaches this function that concatenation has
    already happened and `criteria` is a plain string like ">150").

    Numeric comparison operators (<, <=, >, >=) require BOTH sides to be
    coercible to a number; if either side isn't, the comparison is False —
    Excel does not fall back to a text comparison for these four operators.
    Wildcards are only meaningful for "=" and "<>" and have no effect if the
    operand parses as a number (a numeral has no wildcard characters to
    begin with).
    """
    op, operand, operand_is_number = _parse_criteria(criteria)

    if op in _NUMERIC_OPS:
        value_number = _try_number(value)
        if value_number is None or not operand_is_number:
            return False
        return _NUMERIC_OPS[op](value_number, operand)

    matched = _matches_equality(value, operand, operand_is_number)
    return not matched if op == "<>" else matched
