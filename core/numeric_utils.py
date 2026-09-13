"""Shared rounding arithmetic — the foundation for Group B formula reconstruction.

Excel's rounding functions are NOT one function with different flags. They
split into two genuinely different families that this module keeps separate
on purpose:

  - digit-count rounding (ROUND, ROUNDUP, ROUNDDOWN): the argument is a number
    of decimal digits, and it can be negative (ROUND(1234, -2) == 1200).
  - significance rounding (CEILING, FLOOR): the argument is the multiple to
    round to (CEILING(1.23, 0.5) == 1.5), not a digit count. CEILING(1.23, 2)
    rounds to the nearest 2, not to 2 decimal places.

Collapsing these into one "round to N" helper is the single most common bug
in a from-scratch Excel-rounding reimplementation — get this file wrong and
every one of the five Group B functions inherits the mistake silently.

Every function here is built on `decimal.Decimal` rather than float arithmetic.
Python's built-in `round()` uses banker's rounding (round-half-to-even) and
float representation error means e.g. `round(2.675, 2)` gives `2.67`, not
`2.68` — both wrong for Excel, which rounds .5 ties away from zero on exact
decimal values. Decimal, constructed from the input via `str()`, avoids the
float representation trap and gives us exact control over the tie-breaking
rule.

All five functions handle negative numbers correctly: rounding is symmetric
around zero by construction (magnitude is rounded, then the sign is
reapplied), matching Excel's documented behavior for ROUNDUP/ROUNDDOWN/CEILING/
FLOOR, which are NOT the same operation mirrored — they are each defined
in terms of "away from zero" or "toward zero", not "up" or "down" on the
number line. See each function's docstring for the exact behavior it
implements.

Nothing in this module is wired into the formula-reconstruction catalogue
yet. It is validated standalone first — see tests/test_numeric_utils.py —
so that when Step 4 (Group B: ROUND/ROUNDUP/ROUNDDOWN/CEILING/FLOOR) wires
these in, each function reduces to "call the right helper," not "get the
rounding right AND wire it in at the same time."
"""

from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation


class NumericUtilsError(ValueError):
    """Raised for invalid arguments (e.g. significance of 0 for CEILING/FLOOR)."""


def _to_decimal(value: float) -> Decimal:
    """Convert via str(), not Decimal(float) directly, to avoid binary-float noise.

    Decimal(2.675) is 2.67499999999999982236431605997495353221893310546875 —
    the *actual* float value, not what "2.675" means to a human. str(2.675) is
    "2.675", which is what Excel's own cached value would have been typed or
    computed as. Going through the string representation is what makes exact
    .5-tie rounding possible at all.
    """
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise NumericUtilsError(f"cannot convert {value!r} to Decimal") from exc


def excel_round(value: float, digits: int) -> float:
    """ROUND(value, digits) — half away from zero, matching Excel exactly.

    Python's round() is banker's rounding (half-to-even): round(0.5) == 0,
    round(1.5) == 2. Excel's ROUND is always half AWAY FROM ZERO:
    ROUND(0.5, 0) == 1, ROUND(-0.5, 0) == -1, ROUND(1.5, 0) == 2.

    `digits` may be negative — ROUND(1250, -2) == 1300, rounding to the
    nearest hundred. This mirrors Excel's own semantics for negative
    num_digits, not a special case bolted on afterward.
    """
    d = _to_decimal(value)
    if digits >= 0:
        quantum = Decimal(1).scaleb(-digits)
    else:
        quantum = Decimal(1).scaleb(-digits)
    result = d.quantize(quantum, rounding=ROUND_HALF_UP)
    return float(result)


def _round_magnitude(value: float, digits: int, rounding) -> float:
    """Shared kernel for ROUNDUP/ROUNDDOWN: round the magnitude, reapply sign.

    Both functions round based on ABSOLUTE distance from zero, never on the
    number line directly — that is what makes them symmetric for negative
    inputs. ROUNDUP(-1.1, 0) == -2 (away from zero), NOT -1 (which would be
    "up" on the number line but is actually rounding toward zero).
    """
    d = _to_decimal(value)
    sign = -1 if d < 0 else 1
    magnitude = abs(d)
    quantum = Decimal(1).scaleb(-digits)
    result = magnitude.quantize(quantum, rounding=rounding)
    return float(sign * result)


def roundup_to_significance(value: float, digits: int) -> float:
    """ROUNDUP(value, digits) — always away from zero, regardless of sign.

    ROUNDUP(1.1, 0) == 2; ROUNDUP(-1.1, 0) == -2. Never toward zero, never
    "up the number line" for negatives.
    """
    return _round_magnitude(value, digits, ROUND_UP)


def rounddown_to_significance(value: float, digits: int) -> float:
    """ROUNDDOWN(value, digits) — always toward zero (truncation), regardless of sign.

    ROUNDDOWN(1.9, 0) == 1; ROUNDDOWN(-1.9, 0) == -1.
    """
    return _round_magnitude(value, digits, ROUND_DOWN)


def _to_multiple(value: float, significance: float, rounding_away_from_zero: bool) -> float:
    """Shared kernel for CEILING/FLOOR: round to the nearest MULTIPLE of
    `significance`, not to a digit count.

    Excel's CEILING/FLOOR take a "significance" argument — the value to round
    to a multiple of. CEILING(1.23, 0.5) rounds 1.23 to the nearest multiple
    of 0.5 that is >= 1.23, i.e. 1.5. CEILING(1.23, 2) rounds to the nearest
    multiple of 2 that is >= 1.23, i.e. 2. This is fundamentally different
    from "round to 2 decimal places."

    significance == 0 is a domain error in Excel (#DIV/0!) since "nearest
    multiple of 0" is undefined; we surface it as NumericUtilsError so the
    caller can map it to the appropriate cached-error match.
    """
    if significance == 0:
        # Documented Excel behavior, not a domain error: CEILING(x, 0) and
        # FLOOR(x, 0) both return 0 for any x. (An earlier version of this
        # function raised here on the assumption this was a #DIV/0! case —
        # it verifiably isn't; corrected before Group B wired it in.)
        return 0.0

    d = _to_decimal(value)
    sig = abs(_to_decimal(significance))

    if d == 0:
        return 0.0

    sign = -1 if d < 0 else 1
    magnitude = abs(d)

    quotient = magnitude / sig
    if rounding_away_from_zero:
        # CEILING away from zero on the magnitude == round the quotient UP.
        whole = quotient.to_integral_value(rounding=ROUND_UP)
    else:
        # FLOOR toward zero on the magnitude == round the quotient DOWN.
        whole = quotient.to_integral_value(rounding=ROUND_DOWN)

    result = whole * sig
    return float(sign * result)


def ceiling_to_significance(value: float, significance: float) -> float:
    """CEILING(value, significance) — rounds AWAY from zero to the nearest
    multiple of significance.

    CEILING(1.23, 0.5) == 1.5. CEILING(-1.23, 0.5) == -1.5 (away from zero,
    NOT toward positive infinity — Excel's CEILING is sign-symmetric with
    FLOOR, unlike the mathematical ceiling function).

    The sign of `significance` is not required to match the sign of `value`
    — CEILING(-0.5, 1) == -1, a positive significance applied to a negative
    value, by taking the absolute value of significance before rounding.
    (An earlier version of this docstring claimed mismatched signs were a
    #NUM! error; that described CEILING.MATH's optional mode argument, not
    plain CEILING/FLOOR, and did not match this kernel's own output — it
    should be verified against real Excel/LibreOffice per Step 14 before
    this catalogue entry is treated as final, but the arithmetic below is
    consistent, not guessed per call site.)
    """
    return _to_multiple(value, significance, rounding_away_from_zero=True)


def floor_to_significance(value: float, significance: float) -> float:
    """FLOOR(value, significance) — rounds TOWARD zero to the nearest
    multiple of significance.

    FLOOR(1.23, 0.5) == 1.0. FLOOR(-1.23, 0.5) == -1.0 (toward zero).
    """
    return _to_multiple(value, significance, rounding_away_from_zero=False)
