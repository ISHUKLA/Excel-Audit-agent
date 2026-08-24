"""Deterministic accounting conventions shared by reconciliation and gates.

Reference amounts are stored as non-negative magnitudes. Their accounting sign
is applied here once: debit is positive, credit is negative. Control totals use
the same signed-net convention.
"""

from decimal import Decimal
from typing import Optional

from core.models import ControlTotalCheck, ReferenceFigureLine, ReferenceFigures


def signed_reference_amount(line: ReferenceFigureLine) -> float:
    """Return the canonical signed amount for one reference line."""
    magnitude = Decimal(str(line.amount))
    signed = magnitude if line.debit_credit == "debit" else -magnitude
    return 0.0 if signed == 0 else float(signed)


def evaluate_control_total(reference_figures: Optional[ReferenceFigures]) -> ControlTotalCheck:
    """Tie the signed reference-line population to its declared control total."""
    if reference_figures is None or reference_figures.control_total is None:
        return ControlTotalCheck()

    signed_total = sum(
        (
            Decimal(str(line.amount))
            if line.debit_credit == "debit"
            else -Decimal(str(line.amount))
            for line in reference_figures.lines
        ),
        Decimal("0"),
    )
    declared = Decimal(str(reference_figures.control_total))
    difference = signed_total - declared
    return ControlTotalCheck(
        status="match" if difference == 0 else "mismatch",
        declared_total=float(declared),
        signed_line_total=0.0 if signed_total == 0 else float(signed_total),
        difference=0.0 if difference == 0 else float(difference),
    )
