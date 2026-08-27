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
    evidence_status: Literal["fresh", "stale", "unknown", "not_applicable"] = "fresh",
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

    Order of operations, in this order and no other: (1) the numeric
    threshold verdict; (2) the ambiguity cap; (3) the freshness fail-closed
    cap. Step 3 runs last and overrides both of the others: whenever
    evidence_status is not "fresh", a verdict that would otherwise read
    "pass" or "warn" is forced to "incomplete" — a zero delta, a zero-width
    threshold, and a generous threshold all reach step 3 exactly the same way
    everything else does, so none of them can buy back a pass. A "block"
    verdict is left as "block": a genuine large numeric disagreement on top
    of stale evidence is worth flagging as a block, not softened.

    PRECONDITION: `delta` is non-negative. Agent 3 computes it as
    abs(source - target), so this holds by construction. A signed delta passed
    in here would compare as smaller than the threshold and read as "pass".
    """
    if completeness == "partial":
        return "incomplete"
    if delta is None or delta_pct is None:
        return "incomplete"
    # Zero is inside even a zero-width tolerance: exact equality is a pass.
    # Keep the independent ambiguity cap because matching the wrong figures is
    # not made reliable by their values happening to agree.
    if delta == 0 and delta_pct == 0:
        raw = "warn" if is_ambiguous_match else "pass"
    else:
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
        raw = "warn" if (is_ambiguous_match and worse == "pass") else worse

    if evidence_status != "fresh" and raw in ("pass", "warn"):
        return "incomplete"
    return raw
