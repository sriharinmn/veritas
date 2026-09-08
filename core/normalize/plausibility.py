"""Checks that a fact must survive to be believable, regardless of the document.

Extraction produces claims that are individually well-formed and jointly absurd.
This module holds the small number of tests that catch that — rules which are
true of any financial document, in any language, from any issuer, so applying
them cannot be tuning to this corpus.

There is exactly one such rule today, and it earns its place: a document cannot
report a period that had not finished when it was published.
"""

from __future__ import annotations

import datetime as dt

import structlog

from core.models import Scope, TemporalScope

log = structlog.get_logger(__name__)


def period_is_possible(period: TemporalScope, published_on: dt.date | None) -> bool:
    """Could a document published on this date describe this period?

    A prospectus dated 25 April 2022 does not contain the year ending 31 March
    2024. Nothing about that is a matter of judgement, and the pipeline already
    knew both dates — it simply never compared them, so 14% of the claims that
    carried both asserted a period their own document could not have reported.

    Deliberately one-sided. A period that ended *before* publication is the
    normal case: every annual report describes a year that has finished. Only a
    period running past the publication date is impossible, so only that is
    refused.

    Missing dates return True. Most claims carry no vintage, no resolved period,
    or neither, and a check that guessed in those cases would destroy far more
    good periods than it caught bad ones.
    """
    if published_on is None or period.end is None:
        return True
    return period.end <= published_on


def enforce_period_plausibility(scope: Scope) -> tuple[Scope, bool]:
    """Clear a period the document could not have reported. Returns (scope, changed).

    The period is removed rather than the claim, because the claim is mostly
    right: the value is on the page, the quote is verbatim, the span is correct.
    Only the date is not credible.

    The written label survives the resolved dates. "FY24" is what the extractor
    believed, and keeping the text while dropping the dates lets a reader see
    both what was claimed and that it was not accepted. Deleting it outright
    would tidy the failure out of sight, which is the opposite of what this
    system is for.

    An unresolved period is also the state the comparator already handles
    correctly — it refuses to assert a contradiction without a resolved period
    on both sides — so this feeds a decision the rest of the pipeline already
    makes properly.
    """
    if period_is_possible(scope.period, scope.vintage):
        return scope, False

    log.debug(
        "period.impossible",
        label=scope.period.label,
        period_end=str(scope.period.end),
        published_on=str(scope.vintage),
    )
    return (
        scope.model_copy(
            update={
                "period": TemporalScope(
                    label=scope.period.label,
                    kind=scope.period.kind,
                    convention=scope.period.convention,
                )
            }
        ),
        True,
    )
