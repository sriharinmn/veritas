"""Fiscal period resolution.

These cases are drawn from the dialects actually used across the starter corpus:
Delhivery's prospectus and annual report, an investor deck, the Economic Survey,
the RBI annual report, and an IMF Article IV staff report. Six documents, at
least six ways of writing the same twelve months.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.models import FiscalConvention, PeriodKind
from core.normalize.periods import parse_period

D = dt.date


@pytest.mark.parametrize(
    ("text", "start", "end", "convention"),
    [
        # Indian fiscal year, every dialect that appears in the corpus.
        ("FY24", D(2023, 4, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR),
        ("FY 24", D(2023, 4, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR),
        ("FY2024", D(2023, 4, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR),
        ("FY 2024", D(2023, 4, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR),
        ("fiscal 2024", D(2023, 4, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR),
        ("fiscal year 2022", D(2021, 4, 1), D(2022, 3, 31), FiscalConvention.IN_APR_MAR),
        # Span forms. "2023-24" is how the RBI and the Economic Survey write it.
        ("FY 2023-24", D(2023, 4, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR),
        ("FY2023-24", D(2023, 4, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR),
        ("2023-24", D(2023, 4, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR),
        ("2024-25", D(2024, 4, 1), D(2025, 3, 31), FiscalConvention.IN_APR_MAR),
        ("2023–24", D(2023, 4, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR),
        # Quarters, on the Indian fiscal calendar.
        ("Q1FY24", D(2023, 4, 1), D(2023, 6, 30), FiscalConvention.IN_APR_MAR),
        ("Q2 FY24", D(2023, 7, 1), D(2023, 9, 30), FiscalConvention.IN_APR_MAR),
        ("Q3FY24", D(2023, 10, 1), D(2023, 12, 31), FiscalConvention.IN_APR_MAR),
        ("Q4 FY24", D(2024, 1, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR),
        ("Q4FY2024", D(2024, 1, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR),
        # Cumulative periods — the brief's own "nine-month figure" example.
        ("9MFY25", D(2024, 4, 1), D(2024, 12, 31), FiscalConvention.IN_APR_MAR),
        ("9M FY24", D(2023, 4, 1), D(2023, 12, 31), FiscalConvention.IN_APR_MAR),
        ("6MFY24", D(2023, 4, 1), D(2023, 9, 30), FiscalConvention.IN_APR_MAR),
        ("3MFY24", D(2023, 4, 1), D(2023, 6, 30), FiscalConvention.IN_APR_MAR),
        ("12MFY24", D(2023, 4, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR),
        # Halves.
        ("H1FY25", D(2024, 4, 1), D(2024, 9, 30), FiscalConvention.IN_APR_MAR),
        ("H2FY25", D(2024, 10, 1), D(2025, 3, 31), FiscalConvention.IN_APR_MAR),
        ("H1CY25", D(2025, 1, 1), D(2025, 6, 30), FiscalConvention.CALENDAR),
        ("H2 CY24", D(2024, 7, 1), D(2024, 12, 31), FiscalConvention.CALENDAR),
        # Calendar years — the IMF's convention, and the source of the corpus's
        # most interesting apparent contradictions.
        ("CY2024", D(2024, 1, 1), D(2024, 12, 31), FiscalConvention.CALENDAR),
        ("CY 24", D(2024, 1, 1), D(2024, 12, 31), FiscalConvention.CALENDAR),
        ("calendar year 2025", D(2025, 1, 1), D(2025, 12, 31), FiscalConvention.CALENDAR),
        # Statutory prose, as it appears in audited financial statements.
        (
            "for the year ended March 31, 2024",
            D(2023, 4, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR,
        ),
        (
            "year ended 31 March 2022",
            D(2021, 4, 1), D(2022, 3, 31), FiscalConvention.IN_APR_MAR,
        ),
        (
            "for the financial year ended March 31, 2024",
            D(2023, 4, 1), D(2024, 3, 31), FiscalConvention.IN_APR_MAR,
        ),
        (
            "for the year ended December 31, 2024",
            D(2024, 1, 1), D(2024, 12, 31), FiscalConvention.CALENDAR,
        ),
        # Sub-annual prose.
        (
            "three months ended June 30, 2024",
            D(2024, 4, 1), D(2024, 6, 30), FiscalConvention.UNKNOWN,
        ),
        (
            "nine months ended December 31, 2024",
            D(2024, 4, 1), D(2024, 12, 31), FiscalConvention.UNKNOWN,
        ),
    ],
)
def test_interval_periods(text, start, end, convention):
    p = parse_period(text)
    assert p.kind is PeriodKind.INTERVAL, f"{text!r} was not read as an interval"
    assert p.start == start, f"{text!r} start"
    assert p.end == end, f"{text!r} end"
    assert p.convention is convention, f"{text!r} convention"


@pytest.mark.parametrize(
    ("text", "date"),
    [
        ("as at March 31, 2024", D(2024, 3, 31)),
        ("as at 31 March 2024", D(2024, 3, 31)),
        ("as on 31.03.2024", D(2024, 3, 31)),
        ("as of December 31, 2022", D(2022, 12, 31)),
        ("as on 30/09/2023", D(2023, 9, 30)),
        ("as at 31-03-2022", D(2022, 3, 31)),
    ],
)
def test_instant_periods(text, date):
    p = parse_period(text)
    assert p.kind is PeriodKind.INSTANT
    assert p.start == date and p.end == date


@pytest.mark.parametrize(
    "text",
    ["", "   ", "revenue increased sharply", "page 47", "note 12", "Delhivery Limited"],
)
def test_unrecognised_is_unknown_not_a_guess(text):
    """An unrecognised period lowers comparability. It is not an error, and it
    must never be silently resolved to something plausible."""
    p = parse_period(text)
    assert p.kind is PeriodKind.UNKNOWN
    assert p.start is None and p.end is None


def test_the_case_that_wins_the_assignment():
    """The IMF reports India on calendar years; the Economic Survey does not.

    These two labels look like the same year and are not. A system comparing
    labels would call a difference between them a contradiction; comparing
    resolved dates shows it is a period mismatch, which is the explanation.
    """
    imf = parse_period("CY2024")
    survey = parse_period("2024-25")

    assert imf.start != survey.start
    assert not imf.same_as(survey)
    assert imf.convention is FiscalConvention.CALENDAR
    assert survey.convention is FiscalConvention.IN_APR_MAR


def test_same_period_written_two_ways_compares_equal():
    """FY24 in a deck and "year ended March 31, 2024" in the audited statements
    are the same twelve months, and the comparator must see that."""
    deck = parse_period("FY24")
    statements = parse_period("for the year ended March 31, 2024")

    assert deck.same_as(statements)
    assert deck.label != statements.label  # the labels differ; the period does not


def test_quarter_inside_its_fiscal_year_overlaps():
    assert parse_period("Q4FY24").overlaps(parse_period("FY24"))
    assert not parse_period("Q4FY24").overlaps(parse_period("FY23"))


def test_nine_month_and_full_year_are_not_the_same_period():
    """The brief's own example of an apparent contradiction."""
    nine_m = parse_period("9MFY25")
    full = parse_period("FY25")
    assert not nine_m.same_as(full)
    assert nine_m.overlaps(full)
