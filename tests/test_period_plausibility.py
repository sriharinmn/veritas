"""A document cannot report a period that had not finished when it was published.

A prospectus dated 25 April 2022 does not contain actuals for the year ending
31 March 2024. That is not a modelling opinion, it is arithmetic — and the
pipeline had every fact needed to notice: it stores the publication date on
every claim as `scope.vintage`, and it never once compared the two.

Measured before this check existed: 811 of 5,808 claims carrying both dates —
14.0% — had a period ending after their own document was published. One of them
was the figure the curator selected as case 3, where the reader was told two
values differed "because the basis differs" while the real explanation was that
one of the periods was impossible.

The rule is deliberately one-sided. A period ending *before* publication is
ordinary — every annual report is about a year that has finished. Only the
future direction is impossible, so only that direction is refused.

What happens on failure matters as much as the check. The value, the page and
the quote are all still correct; it is the period that is not credible. So the
period is cleared rather than the claim discarded — an unresolved period is an
honest "we do not know when", and the comparator already refuses to assert a
contradiction without one.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.normalize.periods import parse_period
from core.models import Scope, TemporalScope
from core.normalize.plausibility import period_is_possible, enforce_period_plausibility

PUBLISHED_2022 = dt.date(2022, 4, 25)
PUBLISHED_2024 = dt.date(2024, 8, 1)


def test_a_period_ending_after_publication_is_impossible():
    """The case that started this: a 2022 prospectus labelled FY24."""
    fy24 = parse_period("FY24")
    assert fy24.end == dt.date(2024, 3, 31)
    assert not period_is_possible(fy24, PUBLISHED_2022)


def test_a_finished_period_is_fine():
    """Every annual report describes a year that has already ended."""
    assert period_is_possible(parse_period("FY24"), PUBLISHED_2024)
    assert period_is_possible(parse_period("FY22"), PUBLISHED_2022)


def test_a_period_ending_the_day_before_publication_is_fine():
    """The boundary belongs on the possible side — accounts are published days
    after the period they close."""
    period = TemporalScope(start=dt.date(2024, 4, 1), end=dt.date(2025, 3, 31))
    assert period_is_possible(period, dt.date(2025, 3, 31))
    assert period_is_possible(period, dt.date(2025, 4, 1))
    assert not period_is_possible(period, dt.date(2025, 3, 30))


def test_nothing_is_refused_when_either_date_is_missing():
    """Most claims lack one or both. Absence of evidence is not impossibility,
    and a check that guessed here would destroy good periods to catch bad ones."""
    assert period_is_possible(parse_period("FY24"), None)
    assert period_is_possible(parse_period(""), PUBLISHED_2022)
    assert period_is_possible(TemporalScope(), PUBLISHED_2022)


def test_an_impossible_period_is_cleared_not_the_claim_discarded():
    """The value, page and quote are all still right. Only the date is wrong,
    so only the date is removed."""
    scope = Scope(period=parse_period("FY24"), vintage=PUBLISHED_2022)
    cleaned, changed = enforce_period_plausibility(scope)

    assert changed is True
    assert cleaned.period.start is None and cleaned.period.end is None
    assert cleaned.vintage == PUBLISHED_2022, "the publication date is still known"


def test_the_original_label_is_kept_so_the_mistake_stays_visible():
    """"FY24" is what the extractor believed. Keeping the text while dropping
    the resolved dates lets a reader see what was claimed and that it was not
    accepted — deleting it silently would hide the failure."""
    scope = Scope(period=parse_period("FY24"), vintage=PUBLISHED_2022)
    cleaned, _ = enforce_period_plausibility(scope)
    assert cleaned.period.label == "FY24"


def test_a_possible_scope_is_returned_untouched():
    scope = Scope(period=parse_period("FY24"), vintage=PUBLISHED_2024)
    cleaned, changed = enforce_period_plausibility(scope)
    assert changed is False
    assert cleaned.period.start == dt.date(2023, 4, 1)


def test_a_cleared_period_stops_the_comparator_asserting_a_contradiction():
    """The point of clearing rather than keeping: the comparator already refuses
    to call a conflict without a resolved period on both sides, so removing an
    impossible date automatically stops it drawing conclusions from one."""
    from core.link.compare import _period_established

    class _Claim:
        def __init__(self, scope):
            self.scope = scope

    good = _Claim(Scope(period=parse_period("FY24"), vintage=PUBLISHED_2024))
    cleaned, _ = enforce_period_plausibility(
        Scope(period=parse_period("FY24"), vintage=PUBLISHED_2022)
    )
    assert not _period_established(good, _Claim(cleaned))
