"""Excel serial-date arithmetic — the foundation for Group J formula
reconstruction (DATE, EDATE, NETWORKDAYS, YEARFRAC).

Excel stores every date as a float: whole days since a fixed epoch, with a
fractional part for time-of-day. This module only handles the whole-day
value — none of Group J's functions take or return a time-of-day component.

The epoch Excel actually uses is 1899-12-30, not 1900-01-01, because Excel
carries forward a decades-old Lotus 1-2-3 bug that treats 1900 as a leap
year (it wasn't). Using `date(1899, 12, 30)` as the Python-side epoch
reproduces that bug for free for every date on or after 1900-03-01 — this
module does not special-case it. Only dates in January/February 1900 would
be off by one day from real Excel, a gap no actuarial workbook in practice
will ever hit, so it is left undocumented in code rather than special-cased.

Nothing in this module is wired into the formula-reconstruction catalogue
yet — see tests/test_excel_dates.py for standalone validation, matching the
pattern core/numeric_utils.py established for Group B.
"""

import calendar
from datetime import date, timedelta

_EPOCH = date(1899, 12, 30)


def excel_serial_from_date(year: int, month: int, day: int) -> float:
    """DATE(year, month, day) — out-of-range month/day roll over into the
    adjacent year/month, matching Excel's own documented DATE behavior
    (DATE(2024, 13, 1) == DATE(2025, 1, 1); DATE(2024, 1, 32) == DATE(2024, 2, 1)).
    """
    base = add_months(date(year, 1, 1), month - 1)
    base = base + timedelta(days=day - 1)
    return float((base - _EPOCH).days)


def date_from_excel_serial(serial: float) -> date:
    return _EPOCH + timedelta(days=int(serial))


def add_months(start: date, months: int) -> date:
    """EDATE's month arithmetic. A day that doesn't exist in the target month
    (e.g. Jan 31 + 1 month) clamps to that month's last day, matching Excel
    exactly — it does not spill into the following month.
    """
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    last_day_of_month = calendar.monthrange(year, month)[1]
    day = min(start.day, last_day_of_month)
    return date(year, month, day)


def networkdays(start: date, end: date, holidays: frozenset) -> int:
    """NETWORKDAYS(start, end, [holidays]) — count of Mon-Fri days between
    start and end inclusive, excluding any date present in `holidays`.

    Matches Excel: if start is after end, the result is negative (the count
    for the reversed range, sign-flipped), not an error.
    """
    sign = 1
    if start > end:
        start, end = end, start
        sign = -1
    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in holidays:
            count += 1
        current += timedelta(days=1)
    return sign * count


def _days360_us(d1: date, d2: date) -> int:
    day1 = 30 if d1.day == 31 else d1.day
    day2 = 30 if (d2.day == 31 and day1 == 30) else d2.day
    return (d2.year - d1.year) * 360 + (d2.month - d1.month) * 30 + (day2 - day1)


def _days360_eu(d1: date, d2: date) -> int:
    day1 = 30 if d1.day == 31 else d1.day
    day2 = 30 if d2.day == 31 else d2.day
    return (d2.year - d1.year) * 360 + (d2.month - d1.month) * 30 + (day2 - day1)


def _average_year_length(y1: int, y2: int) -> float:
    years = range(y1, y2 + 1)
    total_days = sum(366 if calendar.isleap(y) else 365 for y in years)
    return total_days / len(years)


def yearfrac(start: date, end: date, basis: int) -> float:
    """YEARFRAC(start, end, [basis]) — fraction of a year between two dates.

    basis 0: US (NASD) 30/360 — Excel's default when basis is omitted.
    basis 1: actual/actual — actual days divided by the average calendar-year
        length (365 or 366) across the years spanned, the standard
        approximation for this basis (Excel's own actual/actual has
        documented edge-case idiosyncrasies around leap years this
        approximation does not reproduce exactly).
    basis 2: actual/360.
    basis 3: actual/365.
    basis 4: European 30/360.
    """
    if start > end:
        start, end = end, start
    if start == end:
        return 0.0
    if basis == 0:
        return _days360_us(start, end) / 360
    if basis == 1:
        return (end - start).days / _average_year_length(start.year, end.year)
    if basis == 2:
        return (end - start).days / 360
    if basis == 3:
        return (end - start).days / 365
    if basis == 4:
        return _days360_eu(start, end) / 360
    raise ValueError(f"unsupported YEARFRAC basis: {basis}")
