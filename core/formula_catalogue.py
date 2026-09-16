"""Single source of truth for which Excel functions Agent 3's reconstruction
engine understands, and what role each argument plays.

Two different consumers read this catalogue and must never drift apart from
each other or from the catalogue itself:

  - `agents/reconciliation.py`'s evaluator dispatch table (`_EVALUATORS`) —
    actually computes each function. A test in `tests/test_reconciliation.py`
    asserts `set(_EVALUATORS) == SUPPORTED_FUNCTIONS` so a function can never
    be declared supported here without something able to compute it, or vice
    versa.
  - `agents/anomaly_detector.py`'s hardcoded-literal detector — needs to know
    which numeric arguments are structural (a rounding digit count, a lookup
    column index, a criteria value) rather than an arbitrary business number,
    so it doesn't flag ROUND(x, 2)'s "2" as a suspicious hardcoded assumption
    the way it correctly would flag a bare "2" used as a growth rate.

`SUPPORTED_FUNCTIONS` is derived from this dict's keys, not maintained as a
separate set — a function is "supported" exactly when it has an arg-role
entry here. That closes the drift risk between "the catalogue says it's
supported" and "the evaluator actually knows how to compute it": there is
only one place to add a function's name, not two that could disagree.

Argument roles (used by the anomaly detector, not by the evaluator):
  - "value": an arbitrary business number — a hardcoded literal here is
    exactly the kind of finding Agent 2 exists to surface.
  - "index": a structural position (a column/row index) — never a business
    figure. Not currently used by any function in this catalogue; reserved
    for lookup functions (VLOOKUP's col_index_num, INDEX's row/col numbers).
  - "digit_count" / "significance": a rounding function's second argument —
    structural, not a business number. Reserved for the Group B rounding
    family.
  - "criteria" / "condition": a comparison string or cell — structural to the
    formula's logic, not itself a hardcoded assumption. Reserved for Group C.
  - "range": one or more cells being aggregated — not a literal at all.
  - "flag": a mode switch (VLOOKUP's range_lookup, MATCH's match_type) —
    structural to how the lookup behaves, not a business number.
  - "any": a role too generic to classify further (used sparingly).
"""

from typing import Literal

ArgRole = Literal[
    "value",
    "index",
    "criteria",
    "condition",
    "flag",
    "digit_count",
    "significance",
    "range",
    "any",
]

# Deliberately explicit per function, not derived from a smaller set of
# templates — a typo'd role for one function should never silently borrow
# another function's argument shape.
FUNCTION_ARG_SPECS: dict[str, list[ArgRole]] = {
    "SUM": ["range"],
    "ABS": ["value"],
    "INT": ["value"],
    # Group B — rounding family. ROUND/ROUNDUP/ROUNDDOWN's second argument is
    # a digit count (can be negative); CEILING/FLOOR's second argument is a
    # significance — the multiple to round to, not a digit count at all.
    # Deliberately two different roles, not one shared "index" role, even
    # though both are structural rather than business numbers: collapsing
    # them would let a real bug (treating a significance as a digit count,
    # or vice versa) hide behind a role name that doesn't distinguish them.
    "ROUND": ["value", "digit_count"],
    "ROUNDUP": ["value", "digit_count"],
    "ROUNDDOWN": ["value", "digit_count"],
    "CEILING": ["value", "significance"],
    "FLOOR": ["value", "significance"],
    # Group C — criteria consumers. SUMIF's optional third argument
    # (sum_range) is a range like the first; when omitted, the range itself
    # is summed. Not marked optional here — this catalogue records argument
    # SHAPE, not arity; the evaluator decides what a missing trailing
    # argument defaults to.
    "SUMIF": ["range", "criteria", "range"],
    # SUMIFS/COUNTIFS take a REPEATING (criteria_range, criteria) pair after
    # their fixed prefix — this list only names the first two pairs' roles;
    # a third+ pair's positions fall past the end of this list and get no
    # role classification at all (see anomaly_detector.py's `spec[index] if
    # index < len(spec) else None`). That's a real limitation of the
    # literal-list shape (Option A, chosen for now over a repeat-group
    # marker) — but "range" and "criteria" are both already EXCLUDED from
    # _STRUCTURAL_ARG_ROLES, meaning neither role currently changes
    # anomaly-detector behavior anyway (a criteria literal is deliberately
    # still flagged as a possible hardcoded business assumption — see
    # tests/test_anomaly.py's SUMIF criteria-threshold regression test).
    # Revisit this if a future repeating-group argument ever needs a
    # structural role.
    "SUMIFS": ["range", "range", "criteria", "range", "criteria"],
    "COUNTIF": ["range", "criteria"],
    "COUNTIFS": ["range", "criteria", "range", "criteria"],
    "AVERAGEIF": ["range", "criteria", "range"],
    "AVERAGEIFS": ["range", "range", "criteria", "range", "criteria"],
    # Group D — MINIFS/MAXIFS are the lookup-family min/max. Shape same as
    # SUMIFS: value_range first, then criteria pairs.
    "MINIFS": ["range", "range", "criteria", "range", "criteria"],
    "MAXIFS": ["range", "range", "criteria", "range", "criteria"],
    # Group C final — IF(condition, value_if_true, value_if_false). Condition
    # is a boolean expression (comparison or cell ref), evaluated to bool; the
    # other two are the values to return. Only the CHOSEN branch is ever
    # resolved, matching Excel's own lazy evaluation (see
    # agents/reconciliation.py's _if_evaluator) — the untaken branch's text is
    # never touched, so IF(B1=0, 0, A1/B1) does not fail when B1 is 0.
    "IF": ["condition", "value", "value"],
    # Group E — lookup functions. Exact match only: VLOOKUP's range_lookup
    # (default TRUE) and MATCH's match_type (default 1, and -1) all assume
    # the lookup column/array is sorted ascending/descending, which this
    # tool does not verify — those modes surface as unsupported at
    # evaluation time rather than being silently trusted (see
    # agents/reconciliation.py's _vlookup_evaluator and _match_evaluator).
    # Only a numeric matched value is reconstructed: every evaluator in this
    # catalogue feeds its result back into further arithmetic, which cannot
    # carry a text value through. Array formulas (e.g. INDEX/MATCH inside a
    # CSE array context) remain explicitly out of scope.
    "VLOOKUP": ["value", "range", "index", "flag"],
    "MATCH": ["value", "range", "flag"],
    "INDEX": ["range", "index", "index"],
    # XLOOKUP — exact match only (match_mode 0); +/-1 (sorted-data
    # assumption) and 2 (wildcard) are unsupported. search_mode +/-1 are
    # both supported (no sortedness assumption); +/-2 (binary search) are
    # not, for consistency with match_mode's sortedness posture. if_not_found
    # (4th arg) is parsed but not used — a miss is unsupported, matching
    # VLOOKUP/MATCH's own "no match" behavior.
    "XLOOKUP": ["value", "range", "range", "value", "flag", "flag"],
    # Group F — logical. AND/OR take 1-255 arguments, all the same role
    # ("condition") — this list names the first two only; the evaluator
    # itself, not this catalogue, is what actually enforces argument count.
    # No short-circuiting: every argument is evaluated, matching Excel.
    "AND": ["condition", "condition"],
    "OR": ["condition", "condition"],
    # Group G — array product aggregation. Every array argument must expand
    # to identical dimensions; text/blank cells coerce to 0, TRUE/FALSE to
    # 1/0 (SUMPRODUCT's own numeric coercion, not shared with the criteria
    # engine's text-equality treatment of booleans elsewhere in this file).
    "SUMPRODUCT": ["range", "range"],
    # Group H — time-value-of-money. `rate` is a single value; the remaining
    # arguments are periodic cash flows (scalars or ranges) in period order.
    # Matches Excel's own NPV exactly: the first cash flow is discounted at
    # period 1, never period 0 — an initial time-0 outflow is the caller's
    # own responsibility to add outside the call, same as in Excel.
    "NPV": ["value", "range"],
    # Group I — index-based selection. index_num is structural (which
    # argument to return), not a business number; a fractional index_num
    # truncates toward the integer below (Excel's own documented behavior).
    # The selected value resolving to text is unsupported, the same
    # architectural wall as VLOOKUP/INDEX/XLOOKUP.
    "CHOOSE": ["index", "value", "value"],
}

SUPPORTED_FUNCTIONS = frozenset(FUNCTION_ARG_SPECS)
