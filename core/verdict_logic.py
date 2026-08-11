"""Pure verdict computation, shared by Agent 3's preview and Gate 3's final recompute.

There is exactly one implementation of "what counts as a pass" in this codebase,
and it lives here. Two implementations would be two places for the preview and
the final verdict to quietly disagree — and the disagreement would surface as a
report saying "pass" over numbers a human had already been shown as "block".
"""

from typing import Literal, Optional

_ORDER = {"pass": 0, "warn": 1, "block": 2}


def compute_verdict(
    delta: Optional[float],
    delta_pct: Optional[float],
    pct_threshold: float,
    absolute_threshold: float,
    completeness: Literal["complete", "partial"],
    is_ambiguous_match: bool = False,
) -> Literal["pass", "warn", "block", "incomplete"]:
    """The verdict for one reconciliation line.

    Both thresholds are evaluated and the WORSE outcome wins. A tiny percentage
    gap on a huge number can still be a material absolute amount, and a small
    absolute gap on a tiny number can still be a material percentage — checking
    only one of them misses one of those cases every time.

    Each threshold has an implicit inner band at one tenth of its value: under
    that is "pass", between it and the threshold is "warn", above is "block".

    "incomplete" is returned for a partial reconstruction, or when either delta
    is missing. It is a distinct state, never a flavour of "pass".

    An ambiguous match is capped at "warn": if nobody is certain these two
    figures are the same figure, agreement between them is not evidence.

    PRECONDITION: `delta` is non-negative. Agent 3 computes it as
    abs(source - target), so this holds by construction. A signed delta passed
    in here would compare as smaller than the threshold and read as "pass".
    """
    if completeness == "partial":
        return "incomplete"
    if delta is None or delta_pct is None:
        return "incomplete"

    pct_verdict = (
        "pass"
        if delta_pct < pct_threshold / 10
        else ("warn" if delta_pct < pct_threshold else "block")
    )
    abs_verdict = (
        "pass"
        if delta < absolute_threshold / 10
        else ("warn" if delta < absolute_threshold else "block")
    )

    worse = pct_verdict if _ORDER[pct_verdict] >= _ORDER[abs_verdict] else abs_verdict

    if is_ambiguous_match and worse == "pass":
        return "warn"
    return worse
