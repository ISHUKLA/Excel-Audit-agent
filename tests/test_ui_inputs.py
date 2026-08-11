"""Clean and malformed input coverage for the Streamlit input boundary."""

from datetime import datetime, timezone

import pytest

from core.ui_inputs import (
    ReferenceFigureInputError,
    build_reference_figures,
    validate_reference_csv_columns,
)

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _build(rows, **overrides):
    values = dict(
        source_label="Q4 trial balance",
        entity="Acme Life SA",
        period="2025-Q4",
        currency="eur",
        basis="IFRS 17",
        control_total=1250.0,
        control_total_confirmed_by_human=False,
        rows=rows,
        require_account_number=False,
        uploaded_at=NOW,
    )
    values.update(overrides)
    return build_reference_figures(**values)


def test_structured_rows_preserve_duplicate_labels_and_account_numbers():
    report_input = _build(
        [
            {
                "account_number": "3000",
                "label": "Technical provisions",
                "debit_credit": "credit",
                "amount": 1000.0,
                "ledger_source": "SAP FI",
                "evidence_reference": "extract.csv row 2",
            },
            {
                "account_number": "3000",
                "label": "Technical provisions",
                "debit_credit": "credit",
                "amount": 250.0,
                "ledger_source": "SAP FI",
                "evidence_reference": "extract.csv row 3",
            },
        ]
    )

    assert [line.label for line in report_input.lines] == [
        "Technical provisions",
        "Technical provisions",
    ]
    assert [line.account_number for line in report_input.lines] == ["3000", "3000"]
    assert sum(line.amount for line in report_input.lines) == 1250.0
    assert report_input.currency == "EUR"


def test_csv_columns_are_rejected_instead_of_guessed():
    with pytest.raises(ReferenceFigureInputError, match="account_number, debit_credit"):
        validate_reference_csv_columns(["label", "amount"])


def test_csv_rows_require_an_account_number_even_though_manual_rows_may_omit_it():
    row = {"label": "Reserve", "amount": 10, "debit_credit": "credit"}
    assert _build([row]).lines[0].account_number is None

    with pytest.raises(ReferenceFigureInputError, match="account_number is required"):
        _build([row], require_account_number=True)


@pytest.mark.parametrize(
    "row, message",
    [
        ({"label": "", "amount": 10, "debit_credit": "credit"}, "label is required"),
        ({"label": "Reserve", "amount": -1, "debit_credit": "credit"}, "non-negative"),
        ({"label": "Reserve", "amount": "ten", "debit_credit": "credit"}, "numeric"),
        ({"label": "Reserve", "amount": 10, "debit_credit": "sideways"}, "debit or credit"),
    ],
)
def test_malformed_reference_rows_fail_with_row_specific_messages(row, message):
    with pytest.raises(ReferenceFigureInputError, match=message):
        _build([row])


def test_control_total_cannot_be_confirmed_when_no_total_was_entered():
    with pytest.raises(ReferenceFigureInputError, match="control total must be entered"):
        _build(
            [{"label": "Reserve", "amount": 10, "debit_credit": "credit"}],
            control_total=None,
            control_total_confirmed_by_human=True,
        )
