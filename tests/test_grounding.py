"""The grounding verifier.

This is the guardrail, so these tests are written adversarially: each one
describes a way a model could produce a claim that *looks* correct and must
still be refused.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from uuid import uuid4

import pytest

from core.ground.verify import (
    GroundingReport,
    QuarantineReason,
    normalise_text,
    verify_all,
    verify_claim,
)
from core.models import Claim, Evidence, Scope, TypedValue, ValueKind
from core.parse.pdf import Page

DOC = uuid4()
PAGE_TEXT = (
    "Revenue from operations for the year ended March 31, 2024 was Rs. 72,251 million, "
    "an increase of 12.5% over the prior year. Contingent liabilities were NIL."
)


def _page() -> dict[int, Page]:
    return {1: Page(number=1, text=PAGE_TEXT, blocks=[], width=595.0, height=842.0)}


def _claim(raw: str, quote: str, *, start: int | None = None, end: int | None = None, page: int = 1) -> Claim:
    if start is None:
        start = PAGE_TEXT.find(quote)
        end = start + len(quote)
    return Claim(
        subject_raw="Delhivery Limited",
        predicate_raw="revenue from operations",
        value=TypedValue(kind=ValueKind.MONEY, raw=raw, canonical_magnitude=Decimal("72251000000"), currency="INR"),
        scope=Scope(vintage=dt.date(2024, 8, 1)),
        evidence=[
            Evidence(document_id=DOC, page=page, char_start=start, char_end=end, quote=quote)
        ],
    )


# ── the happy path ───────────────────────────────────────────────────────────


def test_a_correctly_cited_claim_is_grounded():
    c = _claim("72,251", "Rs. 72,251 million")
    assert verify_claim(c, _page()).ok


def test_whitespace_and_line_breaks_do_not_fail_a_correct_citation():
    """A line break inside a quoted sentence is a rendering detail, not a
    difference in what the document says. Quarantining for it would make the
    pass rate meaningless."""
    quote = "Rs. 72,251 million"
    start = PAGE_TEXT.find(quote)
    c = _claim("72,251", "Rs.  72,251\nmillion", start=start, end=start + len(quote))
    assert verify_claim(c, _page()).ok


@pytest.mark.parametrize("raw", ["72,251", "72251", "72 251"])
def test_the_same_number_written_three_ways_all_ground(raw):
    assert verify_claim(_claim(raw, "Rs. 72,251 million"), _page()).ok


def test_nil_is_a_stated_value_and_grounds():
    c = _claim("NIL", "Contingent liabilities were NIL")
    c.value = TypedValue(kind=ValueKind.MONEY, raw="NIL", canonical_magnitude=Decimal(0), currency="INR")
    assert verify_claim(c, _page()).ok


# ── the failures that matter ─────────────────────────────────────────────────


def test_a_hallucinated_value_with_a_real_citation_is_refused():
    """The single most dangerous failure mode in this product: a plausible
    figure attached to a genuine, verifiable-looking span."""
    c = _claim("81,400", "Rs. 72,251 million")
    r = verify_claim(c, _page())
    assert not r.ok
    assert r.reason is QuarantineReason.VALUE_NOT_IN_QUOTE
    assert "81,400" in r.explanation


def test_a_paraphrased_quote_is_refused():
    """The model must quote its source, not summarise it. A paraphrase means the
    highlight would land on text the reader never sees."""
    start = PAGE_TEXT.find("Rs. 72,251 million")
    c = _claim("72,251", "revenue of 72,251 million rupees", start=start, end=start + 18)
    r = verify_claim(c, _page())
    assert not r.ok
    assert r.reason is QuarantineReason.QUOTE_MISMATCH


def test_a_span_past_the_end_of_the_page_is_refused():
    c = _claim("72,251", "Rs. 72,251 million", start=10_000, end=10_020)
    r = verify_claim(c, _page())
    assert not r.ok
    assert r.reason is QuarantineReason.SPAN_OUT_OF_RANGE


def test_a_citation_to_a_page_that_does_not_exist_is_refused():
    r = verify_claim(_claim("72,251", "Rs. 72,251 million", page=99), _page())
    assert not r.ok
    assert r.reason is QuarantineReason.PAGE_NOT_FOUND


def test_a_claim_with_no_citation_at_all_is_refused():
    c = _claim("72,251", "Rs. 72,251 million")
    c.evidence = []
    r = verify_claim(c, _page())
    assert not r.ok
    assert r.reason is QuarantineReason.NO_EVIDENCE


def test_an_empty_quote_is_refused():
    c = _claim("72,251", "Rs. 72,251 million")
    c.evidence[0].quote = "   "
    r = verify_claim(c, _page())
    assert not r.ok
    assert r.reason is QuarantineReason.EMPTY_QUOTE


def test_there_is_no_confidence_escape_hatch():
    """A high confidence score must not rescue an ungrounded claim.

    A confidence number is something a reader can talk themselves past. A hard
    gate is not, and that difference is the entire design.
    """
    c = _claim("81,400", "Rs. 72,251 million")
    c.confidence = 0.99
    assert not verify_claim(c, _page()).ok


# ── inherited context ────────────────────────────────────────────────────────


def test_inherited_context_need_not_contain_the_value():
    """The "(₹ in millions)" caption a scale came from is evidence, but the whole
    point of it is that the value appears somewhere else."""
    c = _claim("72,251", "Rs. 72,251 million")
    c.evidence.append(
        Evidence(
            document_id=DOC,
            page=1,
            char_start=0,
            char_end=7,
            quote="Revenue",
            kind="inherited_context",
        )
    )
    assert verify_claim(c, _page()).ok


def test_a_claim_grounded_only_by_inherited_context_is_refused():
    c = _claim("81,400", "Revenue")
    c.evidence[0].kind = "inherited_context"
    assert not verify_claim(c, _page()).ok


# ── reporting ────────────────────────────────────────────────────────────────


def test_the_report_counts_and_categorises():
    claims = [
        _claim("72,251", "Rs. 72,251 million"),
        _claim("12.5", "increase of 12.5%"),
        _claim("99,999", "Rs. 72,251 million"),
        _claim("72,251", "Rs. 72,251 million", page=42),
    ]
    grounded, quarantined, report = verify_all(claims, _page())

    assert len(grounded) == 2
    assert len(quarantined) == 2
    assert report.total == 4
    assert report.pass_rate == 0.5
    assert report.by_reason == {
        QuarantineReason.VALUE_NOT_IN_QUOTE.value: 1,
        QuarantineReason.PAGE_NOT_FOUND.value: 1,
    }
    assert "50.0%" in report.summary()


def test_an_empty_report_does_not_divide_by_zero():
    assert GroundingReport().pass_rate == 0.0


def test_quarantine_reasons_are_written_for_a_human():
    """These strings appear in the product's quarantine queue and in the README's
    fourth case. They have to explain the failure, not name it."""
    r = verify_claim(_claim("81,400", "Rs. 72,251 million"), _page())
    assert "hallucinated" in r.explanation.lower()
    assert len(r.explanation) > 60


def test_normalise_text_folds_pdf_extraction_artefacts():
    assert normalise_text("Rs. 72,251​million") == "Rs. 72,251 million"
    assert normalise_text("  a \n b  ") == "a b"
