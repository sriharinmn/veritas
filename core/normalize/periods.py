"""Fiscal period resolution.

This is the module that earns the assignment's third required case.

An apparent contradiction between two financial documents is, more often than
anything else, a period mismatch written in two different dialects. "FY24" in an
Indian filing, "year ended March 31, 2024" in the statutory statements, and
"2023-24" in an RBI table are the same twelve months. Meanwhile the IMF reports
India on calendar years, so its "2024" is a genuinely different period from the
Economic Survey's "2024-25" — and two institutions can print different numbers
for what looks like the same year and both be correct.

Comparison therefore happens on **resolved dates**, never on labels. The label is
kept only so the UI can show the reader what the document actually said.
"""

from __future__ import annotations

import datetime as dt
import re

from core.models import FiscalConvention, PeriodKind, TemporalScope

# Indian fiscal year: 1 April → 31 March. FY24 ends 31 March 2024.
IN_FY_START_MONTH = 4

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))


def _eom(year: int, month: int) -> dt.date:
    if month == 12:
        return dt.date(year, 12, 31)
    return dt.date(year, month + 1, 1) - dt.timedelta(days=1)


def _fy_bounds(end_year: int) -> tuple[dt.date, dt.date]:
    """FY<end_year> under the Indian convention: 1 Apr (end_year-1) → 31 Mar end_year."""
    return dt.date(end_year - 1, IN_FY_START_MONTH, 1), dt.date(end_year, 3, 31)


def _expand_year(y: int) -> int:
    """"24" → 2024. Two-digit years are ubiquitous in these documents."""
    if y < 100:
        return 2000 + y
    return y


# ── patterns, ordered most specific first ────────────────────────────────────
#
# Order matters: "Q4FY24" must not be consumed by the plain "FY24" rule, and
# "9MFY25" must not be read as the number 9 followed by a fiscal year.

# Slide decks and multi-column layouts concatenate text runs out of order, so
# period labels regularly arrive with a stray character glued to the front:
# "aFY24", "1Q4FY24". A plain \b then fails and the year leaks out as a bare
# number that the extractor happily reports as a fact. `(?<![A-Za-z]{2})`
# tolerates one glued character while still refusing to match inside a real word
# such as "satisfy24".
_GLUE = r"(?<![A-Za-z]{2})"

_Q_FY = re.compile(rf"{_GLUE}Q([1-4])\s*[-/ ]?\s*FY\s*[-']?\s*(\d{{2,4}})\b", re.IGNORECASE)
_NM_FY = re.compile(rf"{_GLUE}(3|6|9|12)\s*M\s*[-/ ]?\s*FY\s*[-']?\s*(\d{{2,4}})\b", re.IGNORECASE)
_H_FY = re.compile(rf"{_GLUE}H([12])\s*[-/ ]?\s*FY\s*[-']?\s*(\d{{2,4}})\b", re.IGNORECASE)
_H_CY = re.compile(rf"{_GLUE}H([12])\s*[-/ ]?\s*CY\s*[-']?\s*(\d{{2,4}})\b", re.IGNORECASE)
_Q_CY = re.compile(r"\bQ([1-4])\s*[-/ ]?\s*(?:CY\s*)?(\d{4})\b", re.IGNORECASE)

# "FY 2023-24", "FY23-24", "2023-24", "2023–24"
_FY_SPAN = re.compile(r"\b(?:FY|fiscal)?\s*[-']?\s*(\d{4}|\d{2})\s*[-–—/]\s*(\d{2,4})\b", re.IGNORECASE)
_FY_ONE = re.compile(rf"{_GLUE}(?:FY|fiscal(?:\s+year)?)\s*[-']?\s*(\d{{2,4}})\b", re.IGNORECASE)
_CY_ONE = re.compile(rf"{_GLUE}(?:CY|calendar\s+year)\s*[-']?\s*(\d{{2,4}})\b", re.IGNORECASE)

# "year ended March 31, 2024" / "for the year ended 31 March 2024"
_YEAR_ENDED = re.compile(
    rf"\b(?:for\s+the\s+)?(?:financial\s+|fiscal\s+)?year\s+ende[dr]\s+"
    rf"(?:(\d{{1,2}})\s+({_MONTH_ALT})|({_MONTH_ALT})\s+(\d{{1,2}}))[,\s]+(\d{{4}})",
    re.IGNORECASE,
)

# "three months ended June 30, 2024"
_N_MONTHS_ENDED = re.compile(
    rf"\b(three|six|nine|twelve|3|6|9|12)\s+months?\s+ende[dr]\s+"
    rf"(?:(\d{{1,2}})\s+({_MONTH_ALT})|({_MONTH_ALT})\s+(\d{{1,2}}))[,\s]+(\d{{4}})",
    re.IGNORECASE,
)

# "as at 31 March 2024" / "as on 31.03.2024" / "as of March 31, 2024"
_AS_AT = re.compile(
    rf"\bas\s+(?:at|on|of)\s+"
    rf"(?:(\d{{1,2}})\s+({_MONTH_ALT})[,\s]+(\d{{4}})"
    rf"|({_MONTH_ALT})\s+(\d{{1,2}})[,\s]+(\d{{4}})"
    rf"|(\d{{1,2}})[./-](\d{{1,2}})[./-](\d{{4}}))",
    re.IGNORECASE,
)

_WORD_N = {"three": 3, "six": 6, "nine": 9, "twelve": 12, "3": 3, "6": 6, "9": 9, "12": 12}


def parse_period(text: str) -> TemporalScope:
    """Resolve a period expression to real dates.

    Returns a TemporalScope with `kind=UNKNOWN` when nothing is recognised,
    rather than raising or guessing. An unrecognised period is a legitimate and
    common state — it lowers a claim's comparability, it is not an error.
    """
    t = (text or "").strip()
    if not t:
        return TemporalScope()

    if m := _Q_FY.search(t):
        q, y = int(m.group(1)), _expand_year(int(m.group(2)))
        fy_start, _ = _fy_bounds(y)
        start_month = IN_FY_START_MONTH + 3 * (q - 1)
        year = fy_start.year + (start_month - 1) // 12
        month = (start_month - 1) % 12 + 1
        start = dt.date(year, month, 1)
        end_month_abs = start_month + 2
        end_year = fy_start.year + (end_month_abs - 1) // 12
        end = _eom(end_year, (end_month_abs - 1) % 12 + 1)
        return TemporalScope(
            kind=PeriodKind.INTERVAL, label=m.group(0).strip(), start=start, end=end,
            convention=FiscalConvention.IN_APR_MAR,
        )

    if m := _NM_FY.search(t):
        n, y = int(m.group(1)), _expand_year(int(m.group(2)))
        start, _ = _fy_bounds(y)
        end_abs = IN_FY_START_MONTH + n - 1
        end = _eom(start.year + (end_abs - 1) // 12, (end_abs - 1) % 12 + 1)
        return TemporalScope(
            kind=PeriodKind.INTERVAL, label=m.group(0).strip(), start=start, end=end,
            convention=FiscalConvention.IN_APR_MAR,
        )

    if m := _H_FY.search(t):
        h, y = int(m.group(1)), _expand_year(int(m.group(2)))
        fy_start, fy_end = _fy_bounds(y)
        if h == 1:
            return TemporalScope(
                kind=PeriodKind.INTERVAL, label=m.group(0).strip(),
                start=fy_start, end=_eom(fy_start.year, 9),
                convention=FiscalConvention.IN_APR_MAR,
            )
        return TemporalScope(
            kind=PeriodKind.INTERVAL, label=m.group(0).strip(),
            start=dt.date(fy_start.year, 10, 1), end=fy_end,
            convention=FiscalConvention.IN_APR_MAR,
        )

    if m := _H_CY.search(t):
        h, y = int(m.group(1)), _expand_year(int(m.group(2)))
        if h == 1:
            return TemporalScope(
                kind=PeriodKind.INTERVAL, label=m.group(0).strip(),
                start=dt.date(y, 1, 1), end=dt.date(y, 6, 30),
                convention=FiscalConvention.CALENDAR,
            )
        return TemporalScope(
            kind=PeriodKind.INTERVAL, label=m.group(0).strip(),
            start=dt.date(y, 7, 1), end=dt.date(y, 12, 31),
            convention=FiscalConvention.CALENDAR,
        )

    if m := _N_MONTHS_ENDED.search(t):
        n = _WORD_N[m.group(1).lower()]
        day = int(m.group(2) or m.group(5))
        month = _MONTHS[(m.group(3) or m.group(4)).lower()]
        year = int(m.group(6))
        end = dt.date(year, month, day)
        start_abs = month - n + 1
        start = dt.date(year + (start_abs - 1) // 12, (start_abs - 1) % 12 + 1, 1)
        return TemporalScope(
            kind=PeriodKind.INTERVAL, label=m.group(0).strip(), start=start, end=end,
            convention=FiscalConvention.IN_APR_MAR if month == 3 else FiscalConvention.UNKNOWN,
        )

    if m := _YEAR_ENDED.search(t):
        day = int(m.group(1) or m.group(4))
        month = _MONTHS[(m.group(2) or m.group(3)).lower()]
        year = int(m.group(5))
        end = dt.date(year, month, day)
        start = dt.date(year - 1, month, day) + dt.timedelta(days=1)
        return TemporalScope(
            kind=PeriodKind.INTERVAL, label=m.group(0).strip(), start=start, end=end,
            convention=(
                FiscalConvention.IN_APR_MAR if month == 3
                else FiscalConvention.CALENDAR if month == 12
                else FiscalConvention.UNKNOWN
            ),
        )

    if m := _AS_AT.search(t):
        if m.group(1):
            day, month, year = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
        elif m.group(4):
            month, day, year = _MONTHS[m.group(4).lower()], int(m.group(5)), int(m.group(6))
        else:
            day, month, year = int(m.group(7)), int(m.group(8)), int(m.group(9))
        d = dt.date(year, month, day)
        return TemporalScope(
            kind=PeriodKind.INSTANT, label=m.group(0).strip(), start=d, end=d,
            convention=FiscalConvention.IN_APR_MAR if (month, day) == (3, 31) else FiscalConvention.UNKNOWN,
        )

    if m := _CY_ONE.search(t):
        y = _expand_year(int(m.group(1)))
        return TemporalScope(
            kind=PeriodKind.INTERVAL, label=m.group(0).strip(),
            start=dt.date(y, 1, 1), end=dt.date(y, 12, 31),
            convention=FiscalConvention.CALENDAR,
        )

    if m := _FY_SPAN.search(t):
        first, second = m.group(1), m.group(2)
        end_year = _expand_year(int(second))
        if len(second) == 2 and len(first) == 4:
            # "2023-24" → the span ends in 2024, taking the century from the first half.
            end_year = int(first[:2] + second)
        start, end = _fy_bounds(end_year)
        return TemporalScope(
            kind=PeriodKind.INTERVAL, label=m.group(0).strip(), start=start, end=end,
            convention=FiscalConvention.IN_APR_MAR,
        )

    if m := _FY_ONE.search(t):
        y = _expand_year(int(m.group(1)))
        start, end = _fy_bounds(y)
        return TemporalScope(
            kind=PeriodKind.INTERVAL, label=m.group(0).strip(), start=start, end=end,
            convention=FiscalConvention.IN_APR_MAR,
        )

    if m := _Q_CY.search(t):
        q, y = int(m.group(1)), int(m.group(2))
        start = dt.date(y, 3 * (q - 1) + 1, 1)
        end = _eom(y, 3 * q)
        return TemporalScope(
            kind=PeriodKind.INTERVAL, label=m.group(0).strip(), start=start, end=end,
            convention=FiscalConvention.CALENDAR,
        )

    return TemporalScope()
