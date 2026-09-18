"""Standalone tests for core/excel_dates.py — the shared date-arithmetic
kernels behind Group J (DATE, EDATE, NETWORKDAYS, YEARFRAC).

Nothing here touches agents/reconciliation.py, matching the pattern
tests/test_numeric_utils.py established for Group B.
"""

from datetime import date

import pytest

from core.excel_dates import (
    add_months,
    date_from_excel_serial,
    excel_serial_from_date,
    networkdays,
    yearfrac,
)


# ---------------------------------------------------------------------------
# excel_serial_from_date / date_from_excel_serial
# ---------------------------------------------------------------------------


def test_serial_known_reference_dates():
    # 1900-03-01 is serial 61 in Excel's own numbering (serial 60 is the
    # fictitious 1900-02-29 the Lotus 1-2-3 leap-year bug produces).
    assert excel_serial_from_date(1900, 3, 1) == 61.0
    # A well-known, easily hand-checked modern date.
    assert excel_serial_from_date(2024, 1, 15) == 45306.0


def test_serial_round_trips():
    d = date(2024, 1, 15)
    serial = excel_serial_from_date(d.year, d.month, d.day)
    assert date_from_excel_serial(serial) == d


def test_date_rolls_over_out_of_range_month_and_day():
    # DATE(2024, 13, 1) == DATE(2025, 1, 1)
    assert excel_serial_from_date(2024, 13, 1) == excel_serial_from_date(2025, 1, 1)
    # DATE(2024, 1, 32) == DATE(2024, 2, 1)
    assert excel_serial_from_date(2024, 1, 32) == excel_serial_from_date(2024, 2, 1)


# ---------------------------------------------------------------------------
# add_months — EDATE's kernel
# ---------------------------------------------------------------------------


def test_add_months_clamps_to_shorter_month():
    # Jan 31 + 1 month must clamp to Feb 29 (2024 is a leap year), not spill
    # into March.
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2023, 1, 31), 1) == date(2023, 2, 28)


def test_add_months_negative_crosses_year_boundary():
    assert add_months(date(2024, 1, 15), -1) == date(2023, 12, 15)


def test_add_months_zero_is_identity():
    assert add_months(date(2024, 6, 10), 0) == date(2024, 6, 10)


# ---------------------------------------------------------------------------
# networkdays
# ---------------------------------------------------------------------------


def test_networkdays_excludes_weekends():
    # 2024-01-01 (Mon) through 2024-01-05 (Fri): 5 working days, no weekend.
    assert networkdays(date(2024, 1, 1), date(2024, 1, 5), frozenset()) == 5
    # Extend through the following Sunday: still 5 working days.
    assert networkdays(date(2024, 1, 1), date(2024, 1, 7), frozenset()) == 5


def test_networkdays_excludes_holidays_falling_on_weekdays():
    holidays = frozenset({date(2024, 1, 2)})
    assert networkdays(date(2024, 1, 1), date(2024, 1, 5), holidays) == 4


def test_networkdays_holiday_on_weekend_has_no_effect():
    holidays = frozenset({date(2024, 1, 6)})  # a Saturday
    assert networkdays(date(2024, 1, 1), date(2024, 1, 7), holidays) == 5


def test_networkdays_reversed_range_is_negative():
    assert networkdays(date(2024, 1, 5), date(2024, 1, 1), frozenset()) == -5


# ---------------------------------------------------------------------------
# yearfrac
# ---------------------------------------------------------------------------


def test_yearfrac_same_date_is_zero():
    assert yearfrac(date(2024, 1, 1), date(2024, 1, 1), basis=0) == 0.0


def test_yearfrac_basis_0_us_30_360_full_year():
    assert yearfrac(date(2024, 1, 1), date(2025, 1, 1), basis=0) == 1.0


def test_yearfrac_basis_0_day_31_adjustment():
    # US 30/360: day 31 is treated as day 30 on both legs.
    assert yearfrac(date(2024, 1, 31), date(2024, 2, 29), basis=0) == 29 / 360


def test_yearfrac_basis_2_actual_360():
    assert yearfrac(date(2024, 1, 1), date(2024, 1, 31), basis=2) == 30 / 360


def test_yearfrac_basis_3_actual_365():
    assert yearfrac(date(2024, 1, 1), date(2024, 1, 31), basis=3) == 30 / 365


def test_yearfrac_reversed_dates_same_as_forward():
    forward = yearfrac(date(2024, 1, 1), date(2024, 6, 1), basis=3)
    backward = yearfrac(date(2024, 6, 1), date(2024, 1, 1), basis=3)
    assert forward == backward


def test_yearfrac_unsupported_basis_raises():
    with pytest.raises(ValueError):
        yearfrac(date(2024, 1, 1), date(2024, 6, 1), basis=5)
