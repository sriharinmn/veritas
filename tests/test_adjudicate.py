"""The adjudicator and the explainer.

Everything worth testing here is a guard. The prompt is not the contract — the
refusals are. A model that answers confidently and wrongly is the expected input
to this module, not an anomaly, so each test asks: when the model is wrong in
this particular way, does the system still refuse to publish it?
"""

from __future__ import annotations

import datetime as dt
import itertools
from uuid import uuid4

import pytest

from core.extract.gateway import LLMResponse, LLMUnavailable, RateLimited
from core.link.adjudicate import adjudicate, adjudicate_all
from core.link.compare import compare
from core.link.explain import _contradicts_verdict, explain, fallback_explanation
from core.models import (
    Accounting,
    Basis,
    Claim,
    Evidence,
    Modality,
    Relation,
    Scope,
    Verdict,
)
from core.normalize.numbers import parse_value
from core.normalize.periods import parse_period

_page_counter = itertools.count(1)
DELHIVERY = uuid4()
REVENUE = uuid4()
DOC_A = uuid4()
DOC_B = uuid4()
NAMES = {DOC_A: "annual-report-fy24.pdf", DOC_B: "prospectus-2022.pdf"}


def claim(
    value: str,
    *,
    period: str = "FY24",
    basis: Basis = Basis.UNKNOWN,
    segment: str | None = None,
    geography: str | None = None,
    accounting: Accounting = Accounting.UNKNOWN,
    modality: Modality = Modality.UNKNOWN,
    document: object = DOC_A,
    # A distinct page per claim unless a test asks otherwise. Two figures for one
    # metric on one page of one document is the signature of a two-column
    # statement, and the comparator declines to call that a contradiction — so a
    # shared default page would silently change what these fixtures mean.
    page: int | None = None,
    quote: str = "Revenue from operations 7,225 crore",
) -> Claim:
    v = parse_value(value, context=quote)
    assert v is not None, f"could not parse {value!r}"
    return Claim(
        subject_id=DELHIVERY,
        predicate_id=REVENUE,
        subject_raw="Delhivery Limited",
        predicate_raw="revenue from operations",
        value=v,
        scope=Scope(
            period=parse_period(period),
            basis=basis,
            segment=segment,
            geography=geography,
            accounting=accounting,
            modality=modality,
            vintage=dt.date(2024, 8, 1),
        ),
        evidence=[
            Evidence(
                document_id=document,  # type: ignore[arg-type]
                page=next(_page_counter) if page is None else page,
                char_start=0,
                char_end=len(quote),
                quote=quote,
            )
        ],
    )


class FakeGateway:
    """Answers whatever the test tells it to, including badly."""

    tier = "fake"
    model = "fake-1"

    def __init__(self, answer: dict | None = None, raises: Exception | None = None) -> None:
        self.answer = answer
        self.raises = raises
        self.calls = 0
        self.last_user = ""

    async def complete_json(self, *, system, user, schema, max_tokens=2048) -> LLMResponse:
        self.calls += 1
        self.last_user = user
        if self.raises is not None:
            raise self.raises
        return LLMResponse(
            text="{}", parsed=self.answer, provider="fake", model=self.model
        )


def ambiguous_pair() -> tuple[Claim, Claim, Verdict]:
    """Two claims the comparator genuinely cannot decide: three axes differ."""
    a = claim("7,225 crore", segment="Express Parcel", basis=Basis.STANDALONE,
              modality=Modality.REPORTED)
    b = claim("8,142 crore", segment="Partial Truckload", basis=Basis.CONSOLIDATED,
              modality=Modality.PROJECTED, document=DOC_B, page=44)
    verdict = compare(a, b)
    assert verdict.relation is Relation.AMBIGUOUS, verdict.trace
    return a, b, verdict


# ── the adjudicator refuses ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_invented_scope_difference_is_rejected():
    """The central guard. The model may choose among differences the comparator
    actually found; it may not claim one that is not there. An explanation that
    is untrue of the data is worse than no explanation, because it reads as an
    explanation."""
    # Two axes genuinely differ (basis and geography), so the comparator cannot
    # decide. Segment is identical on both sides — which is exactly the axis the
    # model is about to blame.
    a = claim("7,225 crore", segment="Express Parcel", basis=Basis.STANDALONE,
              geography="India")
    b = claim("8,142 crore", segment="Express Parcel", basis=Basis.CONSOLIDATED,
              geography="International")
    verdict = compare(a, b)
    assert verdict.relation is Relation.AMBIGUOUS
    assert "segment" not in a.scope.differing_axes(b.scope)

    gateway = FakeGateway({"relation": "reconciled", "axis": "segment",
                           "reason": "one is Express Parcel and the other is not"})
    out = await adjudicate(a, b, verdict, gateway)

    assert out.relation is Relation.AMBIGUOUS
    assert out.decided_by == "deterministic"
    assert any("rejected" in line for line in out.trace)


@pytest.mark.asyncio
async def test_a_contradiction_without_a_resolved_period_is_rejected():
    """The same evidentiary burden the comparator applies. A model does not get
    to overrule it with confident prose."""
    # Identical scope on every axis, differing values, and no resolved period on
    # either side. The comparator escalates rather than calling this a conflict,
    # and the adjudicator must hold the same line.
    a = claim("7,225 crore", period="", segment="Express Parcel")
    b = claim("8,142 crore", period="", segment="Express Parcel")
    verdict = compare(a, b)
    assert verdict.relation is Relation.AMBIGUOUS
    assert a.scope.period.start is None

    gateway = FakeGateway({"relation": "contradiction", "axis": None,
                           "reason": "these plainly conflict"})
    out = await adjudicate(a, b, verdict, gateway)

    assert out.relation is Relation.AMBIGUOUS
    assert any("no resolved period" in line for line in out.trace)


@pytest.mark.asyncio
async def test_a_relation_outside_the_enum_is_rejected():
    a, b, verdict = ambiguous_pair()
    gateway = FakeGateway({"relation": "probably_fine", "axis": None, "reason": "eh"})
    out = await adjudicate(a, b, verdict, gateway)
    assert out is verdict


@pytest.mark.asyncio
async def test_no_provider_leaves_the_verdict_untouched():
    a, b, verdict = ambiguous_pair()
    for failure in (LLMUnavailable("nothing configured"), RateLimited(30.0)):
        out = await adjudicate(a, b, verdict, FakeGateway(raises=failure))
        assert out is verdict
        assert out.relation is Relation.AMBIGUOUS


@pytest.mark.asyncio
async def test_an_already_decided_verdict_is_never_sent_to_the_model():
    """Deterministic decisions are not up for review. Sending them would spend
    budget to make a proved answer less certain."""
    a = claim("7,225 crore")
    b = claim("7,225 crore")
    verdict = compare(a, b)
    assert verdict.relation is Relation.CORROBORATION

    gateway = FakeGateway({"relation": "contradiction", "axis": None, "reason": "no"})
    out = await adjudicate(a, b, verdict, gateway)
    assert out.relation is Relation.CORROBORATION
    assert gateway.calls == 0


# ── the adjudicator accepts ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reconciliation_on_a_real_axis_is_accepted():
    a, b, verdict = ambiguous_pair()
    gateway = FakeGateway({
        "relation": "reconciled",
        "axis": "segment",
        "reason": "one figure is Express Parcel and the other is Partial Truckload",
    })
    out = await adjudicate(a, b, verdict, gateway)

    assert out.relation is Relation.RECONCILED
    assert out.axis == "segment"
    assert out.decided_by == "llm"
    assert out.explanation


@pytest.mark.asyncio
async def test_an_adjudicated_verdict_is_never_as_confident_as_a_derived_one():
    """A reader sorting by confidence should see what the machine proved before
    what it was persuaded of."""
    a, b, verdict = ambiguous_pair()
    gateway = FakeGateway({"relation": "unrelated", "axis": None, "reason": "different things"})
    out = await adjudicate(a, b, verdict, gateway)
    assert out.confidence <= 0.7


@pytest.mark.asyncio
async def test_the_model_receives_the_deterministic_trace():
    """It is adjudicating a decision, not making one from scratch. Withholding
    the comparator's reasoning would throw away the work already done."""
    a, b, verdict = ambiguous_pair()
    gateway = FakeGateway({"relation": "unrelated", "axis": None, "reason": "x"})
    await adjudicate(a, b, verdict, gateway, NAMES)

    assert "The comparator's reasoning:" in gateway.last_user
    assert "Axes it found genuinely differing:" in gateway.last_user
    assert "annual-report-fy24.pdf p1" in gateway.last_user
    assert "prospectus-2022.pdf p44" in gateway.last_user


@pytest.mark.asyncio
async def test_adjudicate_all_is_bounded_and_spends_on_the_best_pairs_first():
    """Thirty thousand ambiguous pairs against 200,000 tokens a day is about six
    days of allowance, so the bound is not timidity — it is the only question
    worth asking."""
    a, b, verdict = ambiguous_pair()
    pairs = [
        (a, b, verdict.model_copy(update={"confidence": c}))
        for c in (0.1, 0.9, 0.5, 0.8)
    ]
    gateway = FakeGateway({"relation": "unrelated", "axis": None, "reason": "x"})
    out = await adjudicate_all(pairs, gateway, limit=2)

    assert len(out) == 2
    assert gateway.calls == 2


# ── the explainer ────────────────────────────────────────────────────────────


def test_prose_that_asserts_a_different_relation_is_caught():
    assert _contradicts_verdict(
        "The two figures agree once the periods are aligned.", Relation.CONTRADICTION
    )
    assert _contradicts_verdict(
        "These statements are inconsistent and cannot both hold.", Relation.CORROBORATION
    )


def test_prose_that_matches_its_verdict_passes():
    assert (
        _contradicts_verdict(
            "The two documents report the same figure, so they corroborate.",
            Relation.CORROBORATION,
        )
        is None
    )
    assert (
        _contradicts_verdict(
            "They contradict: every scope axis is identical and the values differ.",
            Relation.CONTRADICTION,
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_paragraph_that_disagrees_with_the_verdict_is_discarded():
    """The paragraph is the part people read. One that disagrees with the
    decision it is attached to is worse than none at all."""
    a = claim("7,225 crore")
    b = claim("8,142 crore")
    verdict = compare(a, b)
    assert verdict.relation is Relation.CONTRADICTION

    gateway = FakeGateway({"explanation": "These two figures agree perfectly."})
    out = await explain(a, b, verdict, gateway)

    assert out.explanation is None
    assert any("discarded" in line for line in out.trace)


@pytest.mark.asyncio
async def test_good_prose_is_attached():
    a = claim("7,225 crore")
    b = claim("8,142 crore")
    verdict = compare(a, b)
    gateway = FakeGateway({
        "explanation": "The two filings report different revenue for FY24 with every "
                       "scope axis identical, so they contradict."
    })
    out = await explain(a, b, verdict, gateway)
    assert out.explanation and "contradict" in out.explanation
    assert out.relation is Relation.CONTRADICTION


@pytest.mark.asyncio
async def test_explanation_falls_back_silently_with_no_provider():
    a, b, verdict = ambiguous_pair()
    out = await explain(a, b, verdict, FakeGateway(raises=LLMUnavailable("none")))
    assert out is verdict


def test_the_zero_key_path_still_produces_a_sentence():
    """The no-key demo has to show explained relationships, not empty cards."""
    a = claim("7,225 crore")
    b = claim("8,142 crore")
    for relation in Relation:
        verdict = Verdict(relation=relation, confidence=1.0, axis="segment")
        text = fallback_explanation(a, b, verdict, NAMES)
        assert len(text) > 40
        assert "annual-report-fy24.pdf p1" in text or "not measuring the same" in text
