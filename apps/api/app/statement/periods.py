"""Calendar months: the `period` of the API is `YYYY-MM`, always read in the time zone of the
school (the database does that when it turns the dates into instants)."""

import calendar
from datetime import date

PERIOD_PATTERN = r"^(19|20|21)\d{2}-(0[1-9]|1[0-2])$"


def month_bounds(period: str) -> tuple[date, date]:
    """First and last day of the month named by a `YYYY-MM` that already matched PERIOD_PATTERN."""
    year, month = int(period[:4]), int(period[5:7])
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def period_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"
