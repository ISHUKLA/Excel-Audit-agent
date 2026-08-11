"""Validate and normalize structured user input before model construction.

This module contains no Streamlit calls. Keeping the rules here makes malformed
CSV and table rows testable without coupling accounting input validation to a
particular rendering framework.
"""

import math
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional

from core.models import ReferenceFigureLine, ReferenceFigures

CSV_REQUIRED_COLUMNS = frozenset(
    {"account_number", "label", "amount", "debit_credit"}
)


class ReferenceFigureInputError(ValueError):
    """Raised when a reference-figure table cannot be represented safely."""


def validate_reference_csv_columns(columns: Iterable[object]) -> None:
    present = {str(column) for column in columns}
    missing = sorted(CSV_REQUIRED_COLUMNS - present)
    if missing:
        raise ReferenceFigureInputError(
            "CSV is missing required columns: " + ", ".join(missing)
        )


def build_reference_figures(
    *,
    source_label: str,
    entity: str,
    period: str,
    currency: str,
    basis: Optional[str],
    control_total: Optional[float],
    control_total_confirmed_by_human: bool,
    rows: Iterable[Mapping[str, object]],
    require_account_number: bool,
    uploaded_at: Optional[datetime] = None,
) -> ReferenceFigures:
    required_context = {
        "source label": source_label,
        "entity": entity,
        "period": period,
        "currency": currency,
    }
    missing_context = [label for label, value in required_context.items() if not _text(value)]
    if missing_context:
        raise ReferenceFigureInputError(
            "Reference figures require: " + ", ".join(missing_context)
        )
    if control_total_confirmed_by_human and control_total is None:
        raise ReferenceFigureInputError(
            "A control total must be entered before its tie-out can be confirmed"
        )

    lines: list[ReferenceFigureLine] = []
    for row_number, row in enumerate(rows, start=1):
        if _row_is_blank(row):
            continue
        label = _text(row.get("label"))
        account_number = _text(row.get("account_number")) or None
        debit_credit = _text(row.get("debit_credit")).lower()
        ledger_source = _text(row.get("ledger_source")) or "Not supplied"
        evidence_ref = _text(row.get("evidence_reference") or row.get("evidence_ref")) or None

        if not label:
            raise ReferenceFigureInputError(f"Row {row_number}: label is required")
        if require_account_number and not account_number:
            raise ReferenceFigureInputError(
                f"Row {row_number}: account_number is required for CSV input"
            )
        if debit_credit not in {"debit", "credit"}:
            raise ReferenceFigureInputError(
                f"Row {row_number}: debit_credit must be debit or credit"
            )
        amount = _amount(row.get("amount"), row_number)

        lines.append(
            ReferenceFigureLine(
                line_id=f"REF-{row_number:04d}",
                account_number=account_number,
                label=label,
                entity=_text(entity),
                period=_text(period),
                currency=_text(currency).upper(),
                ledger_source=ledger_source,
                debit_credit=debit_credit,
                amount=amount,
                evidence_ref=evidence_ref,
            )
        )

    if not lines:
        raise ReferenceFigureInputError(
            "Add at least one complete reference-figure row or leave CFO reconciliation off"
        )

    return ReferenceFigures(
        source_label=_text(source_label),
        entity=_text(entity),
        period=_text(period),
        currency=_text(currency).upper(),
        basis=_text(basis) or None,
        control_total=control_total,
        control_total_confirmed_by_human=control_total_confirmed_by_human,
        lines=lines,
        uploaded_at=uploaded_at or datetime.now(timezone.utc),
    )


def _row_is_blank(row: Mapping[str, object]) -> bool:
    fields = (
        "account_number",
        "label",
        "amount",
        "debit_credit",
        "ledger_source",
        "evidence_reference",
        "evidence_ref",
    )
    return all(not _text(row.get(field)) for field in fields)


def _amount(value: object, row_number: int) -> float:
    if isinstance(value, bool) or value is None or not _text(value):
        raise ReferenceFigureInputError(f"Row {row_number}: amount is required")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ReferenceFigureInputError(
            f"Row {row_number}: amount must be numeric"
        ) from exc
    if not math.isfinite(amount) or amount < 0:
        raise ReferenceFigureInputError(
            f"Row {row_number}: amount must be a non-negative finite number"
        )
    return amount


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
