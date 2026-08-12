"""Tests for the single signed-accounting convention and control-total tie-out."""

from datetime import datetime, timezone

from core.accounting import evaluate_control_total, signed_reference_amount
from core.models import ReferenceFigureLine, ReferenceFigures

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


def _line(line_id: str, side: str, amount: float) -> ReferenceFigureLine:
    return ReferenceFigureLine(
        line_id=line_id,
        label=line_id,
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        ledger_source="Trial balance",
        debit_credit=side,
        amount=amount,
    )


def _references(lines, control_total=None) -> ReferenceFigures:
    return ReferenceFigures(
        source_label="Q4 trial balance",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="EUR",
        control_total=control_total,
        lines=lines,
        uploaded_at=NOW,
    )


def test_debit_is_positive_and_credit_is_negative():
    assert signed_reference_amount(_line("D", "debit", 125.0)) == 125.0
    assert signed_reference_amount(_line("C", "credit", 125.0)) == -125.0


def test_signed_reference_lines_tie_exactly_to_the_control_total():
    check = evaluate_control_total(
        _references(
            [_line("D", "debit", 150.0), _line("C", "credit", 50.0)],
            control_total=100.0,
        )
    )

    assert check.status == "match"
    assert check.signed_line_total == 100.0
    assert check.difference == 0.0


def test_mixed_decimal_lines_use_decimal_arithmetic_and_expose_a_mismatch():
    check = evaluate_control_total(
        _references(
            [
                _line("D1", "debit", 0.1),
                _line("D2", "debit", 0.2),
                _line("C", "credit", 0.05),
            ],
            control_total=0.24,
        )
    )

    assert check.status == "mismatch"
    assert check.signed_line_total == 0.25
    assert check.difference == 0.01


def test_absent_control_total_is_explicitly_not_checked():
    check = evaluate_control_total(_references([_line("D", "debit", 10.0)]))
    assert check.status == "not_checked"
    assert check.declared_total is None
    assert check.difference is None
