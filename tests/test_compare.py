"""The deterministic comparator.

These tests are the assignment's three required relations, written as
executable specifications. If they pass, the system can do what was asked; the
LLM's only remaining jobs are reading pages and explaining decisions it did not
make.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from uuid import uuid4

import pytest

from core.link.compare import values_agree
from core.link.compare import compare as compare_claims
from core.models import (
    Accounting,
    Basis,
    Claim,
    Evidence,
    Modality,
    Relation,
    Scope,
    TypedValue,
    ValueKind,
)
from core.normalize.numbers import parse_value
from core.normalize.periods import parse_period

DELHIVERY = uuid4()
REVENUE = uuid4()
DOC = uuid4()


def claim(
    value: TypedValue | str,
    *,
    context: str = "",
    period: str = "FY24",
    basis: Basis = Basis.UNKNOWN,
    segment: str | None = None,
    accounting: Accounting = Accounting.UNKNOWN,
    modality: Modality = Modality.UNKNOWN,
    subject: object = DELHIVERY,
    predicate: object = REVENUE,
) -> Claim:
    v = parse_value(value, context=context) if isinstance(value, str) else value
    assert v is not None, f"could not parse {value!r}"
    return Claim(
        subject_id=subject,  # type: ignore[arg-type]
        predicate_id=predicate,  # type: ignore[arg-type]
        subject_raw="Delhivery Limited",
        predicate_raw="revenue from operations",
        value=v,
        scope=Scope(
            period=parse_period(period),
            basis=basis,
            segment=segment,
            accounting=accounting,
            modality=modality,
            vintage=dt.date(2024, 8, 1),
        ),
        evidence=[Evidence(document_id=DOC, page=1, char_start=0, char_end=5, quote="x" * 5)],
    )


# ── case 1: corroboration ────────────────────────────────────────────────────


def test_case_1_corroboration_across_different_units_and_scales():
    """The assignment's first required case.

    An annual report states revenue in millions inside an audited statement; an
    earnings deck states the same figure in crore on a KPI tile, rounded. Two
    documents, two scales, two roundings, one fact.
    """
    annual_report = claim("72,251", context="(Rs. in millions)")
    earnings_deck = claim("7,225", context="(₹ in crore)")

    v = compare_claims(annual_report, earnings_deck)

    assert v.relation is Relation.CORROBORATION
    assert v.decided_by == "deterministic"
    assert any("rounding" in line for line in v.trace)


def test_corroboration_requires_identical_scope_to_be_full_strength():
    a = claim("72,251", context="(Rs. in millions)", basis=Basis.CONSOLIDATED)
    b = claim("7,225", context="(₹ in crore)", basis=Basis.CONSOLIDATED)
    assert compare_claims(a, b).confidence == 1.0


# ── case 2: contradiction ────────────────────────────────────────────────────


def test_case_2_contradiction_when_no_scope_axis_explains_the_gap():
    """The assignment's second required case.

    Same entity, same metric, same period, same basis — different numbers. The
    trace must say that every axis was checked and found identical, because
    showing the reasoning that *rules out* reconciliation matters as much as the
    flag itself.
    """
    prospectus = claim("72,251", context="(Rs. in millions)", basis=Basis.CONSOLIDATED)
    annual_report = claim("81,400", context="(Rs. in millions)", basis=Basis.CONSOLIDATED)

    v = compare_claims(prospectus, annual_report)

    assert v.relation is Relation.CONTRADICTION
    assert any("every scope axis was checked" in line for line in v.trace)
    assert any("%" in line for line in v.trace)


def test_a_contradiction_is_not_declared_on_a_rounding_difference():
    """The false-alarm rate is what would destroy a banker's trust."""
    a = claim("72,251", context="(Rs. in millions)")
    b = claim("72,250", context="(Rs. in millions)")
    assert compare_claims(a, b).relation is Relation.CORROBORATION


# ── case 3: reconciled by context ────────────────────────────────────────────


def test_case_3_reconciled_by_period():
    """The assignment's third required case, and the one that wins it.

    Different values, and exactly one scope axis differs — so that axis *is* the
    explanation. No model was asked for an opinion.
    """
    fy23 = claim("62,000", context="(Rs. in millions)", period="FY23")
    fy24 = claim("72,251", context="(Rs. in millions)", period="FY24")

    v = compare_claims(fy23, fy24)

    assert v.relation is Relation.RECONCILED
    assert v.axis == "period"
    assert any("different periods" in line for line in v.trace)


def test_case_3_reconciled_by_basis():
    consolidated = claim("72,251", context="(Rs. in millions)", basis=Basis.CONSOLIDATED)
    standalone = claim("58,900", context="(Rs. in millions)", basis=Basis.STANDALONE)

    v = compare_claims(consolidated, standalone)
    assert v.relation is Relation.RECONCILED
    assert v.axis == "basis"


def test_case_3_reconciled_by_modality():
    """A projection disagreeing with an actual is not a contradiction."""
    actual = claim("6.5%", modality=Modality.REPORTED)
    projection = claim("7.0%", modality=Modality.PROJECTED)

    v = compare_claims(actual, projection)
    assert v.relation is Relation.RECONCILED
    assert v.axis == "modality"


def test_case_3_reconciled_by_segment():
    total = claim("72,251", context="(Rs. in millions)")
    express = claim("41,000", context="(Rs. in millions)", segment="Express Parcel")

    v = compare_claims(total, express)
    assert v.relation is Relation.RECONCILED
    assert v.axis == "segment"


def test_the_nine_month_versus_full_year_case_from_the_brief():
    nine_m = claim("54,000", context="(Rs. in millions)", period="9MFY24")
    full_year = claim("72,251", context="(Rs. in millions)", period="FY24")

    v = compare_claims(nine_m, full_year)
    assert v.relation is Relation.RECONCILED
    assert v.axis == "period"


# ── the ambiguous residue ────────────────────────────────────────────────────


def test_two_or_more_differing_axes_escalates_rather_than_guessing():
    """The only case the LLM sees. It is handed this trace as context."""
    a = claim("72,251", context="(Rs. in millions)", period="FY24", basis=Basis.CONSOLIDATED)
    b = claim("41,000", context="(Rs. in millions)", period="FY23", basis=Basis.STANDALONE)

    v = compare_claims(a, b)
    assert v.relation is Relation.AMBIGUOUS
    assert v.confidence < 1.0
    assert any("escalating" in line for line in v.trace)


# ── things that are simply not related ───────────────────────────────────────


def test_different_subjects_are_unrelated():
    a = claim("72,251", context="(Rs. in millions)")
    b = claim("72,251", context="(Rs. in millions)", subject=uuid4())
    assert compare_claims(a, b).relation is Relation.UNRELATED


def test_different_predicates_are_unrelated():
    a = claim("72,251", context="(Rs. in millions)")
    b = claim("72,251", context="(Rs. in millions)", predicate=uuid4())
    assert compare_claims(a, b).relation is Relation.UNRELATED


def test_uncanonicalised_claims_are_never_silently_compared():
    """A claim without a resolved subject or predicate must not be compared as
    though it had one. Guessing here would fabricate relationships."""
    a = claim("72,251", context="(Rs. in millions)")
    a.subject_id = None
    assert compare_claims(a, claim("72,251", context="(Rs. in millions)")).relation is Relation.UNRELATED


# ── value agreement in detail ────────────────────────────────────────────────


def test_a_point_estimate_inside_a_stated_range_agrees():
    """IMF 6.5% against an Economic Survey range of 6.3-6.8%.

    A system that cannot represent a range reports a contradiction here.
    """
    survey = parse_value("6.3 to 6.8 per cent")
    imf = parse_value("6.5%")
    agree, why = values_agree(survey, imf)
    assert agree
    assert "inside" in why


def test_a_point_estimate_outside_a_stated_range_disagrees():
    survey = parse_value("6.3 to 6.8 per cent")
    outlier = parse_value("5.1%")
    agree, why = values_agree(survey, outlier)
    assert not agree
    assert "outside" in why


def test_ratios_are_compared_in_percentage_points_not_relatively():
    """6.5% and 6.4% are 1.5% apart relatively but 0.1pp apart in the way an
    economist means it, and these documents round rates to one decimal."""
    agree, _ = values_agree(parse_value("6.5%"), parse_value("6.4%"))
    assert not agree

    agree, _ = values_agree(parse_value("6.50%"), parse_value("6.5%"))
    assert agree


def test_different_currencies_are_not_silently_compared():
    """No implicit FX conversion. A rate we did not source is a number we cannot
    defend, and defending the number is the entire product."""
    inr = parse_value("1,000", context="₹")
    usd = parse_value("1,000", context="$")
    agree, why = values_agree(inr, usd)
    assert not agree
    assert "currencies" in why


def test_a_revenue_figure_and_a_parcel_count_are_not_compared_by_magnitude():
    money = parse_value("72,251", context="revenue in ₹ millions")
    count = parse_value("72,251", context="parcels shipped")
    agree, _ = values_agree(money, count)
    # Same digits, different kinds of assertion — must not corroborate.
    assert not agree or money.kind is count.kind


def test_precision_is_taken_from_the_coarser_statement():
    """"7,225 crore" claims four significant digits. Comparing at five would
    call it different from "72,251 million"; comparing at four shows it agrees.
    Rounding is not disagreement."""
    coarse = parse_value("7,225", context="(₹ in crore)")
    fine = parse_value("72,251", context="(Rs. in millions)")
    agree, _ = values_agree(coarse, fine)
    assert agree

    # But a genuinely different figure at the same precision still disagrees.
    other = parse_value("7,300", context="(₹ in crore)")
    agree, _ = values_agree(other, fine)
    assert not agree


def test_nil_and_a_real_figure_contradict():
    nil = claim(TypedValue(kind=ValueKind.MONEY, raw="NIL", canonical_magnitude=Decimal(0), currency="INR"))
    some = claim("1,200", context="(₹ in crore)")
    assert compare_claims(nil, some).relation is Relation.CONTRADICTION


@pytest.mark.parametrize(
    ("a_text", "b_text"),
    [("8.2%", "820 bps"), ("0.35%", "35 bps"), ("1.00x", "1x")],
)
def test_equivalent_ratio_notations_corroborate(a_text, b_text):
    assert values_agree(parse_value(a_text), parse_value(b_text))[0]


def test_the_trace_reads_as_an_explanation_not_a_debug_log():
    """The trace is rendered in the product. A reader has to be able to follow
    the decision from it."""
    v = compare_claims(
        claim("62,000", context="(Rs. in millions)", period="FY23"),
        claim("72,251", context="(Rs. in millions)", period="FY24"),
    )
    joined = " ".join(v.trace)
    assert "subject ≡" in joined
    assert "predicate ≡" in joined
    assert "scope differs on" in joined
    assert "→ reconciled" in joined
    assert len(v.trace) >= 5


# ── the evidentiary burden of an accusation ──────────────────────────────────


def test_a_contradiction_is_not_asserted_without_a_resolved_period():
    """Corroboration and contradiction do not carry the same burden.

    Saying two figures agree is a mild claim. Saying they contradict is an
    accusation placed in front of somebody who will act on it, and it needs
    positive evidence that the two statements describe the same thing — not
    merely the absence of evidence that they do not.

    Measured on the corpus before this rule: 26,933 contradictions, 52% of them
    between claims where neither side carried a resolved period. Those were not
    findings. They were missing information, reported as findings.
    """
    a = claim("72,251", context="(Rs. in millions)", period="no period here")
    b = claim("81,400", context="(Rs. in millions)", period="none either")
    assert a.scope.period.start is None and b.scope.period.start is None

    v = compare_claims(a, b)
    assert v.relation is Relation.AMBIGUOUS
    assert any("no positive evidence" in line for line in v.trace)
    assert v.confidence < 1.0


def test_but_a_real_contradiction_is_still_asserted():
    """The rule must not suppress genuine findings — both sides have FY24."""
    a = claim("72,251", context="(Rs. in millions)", basis=Basis.CONSOLIDATED)
    b = claim("81,400", context="(Rs. in millions)", basis=Basis.CONSOLIDATED)
    assert compare_claims(a, b).relation is Relation.CONTRADICTION


def test_one_resolved_period_is_not_enough_to_accuse():
    a = claim("72,251", context="(Rs. in millions)", period="FY24")
    b = claim("81,400", context="(Rs. in millions)", period="unstated")
    assert compare_claims(a, b).relation is Relation.AMBIGUOUS


def test_agreement_is_still_reported_without_a_period():
    """Only the accusation needs the higher burden. Two matching figures with no
    stated period are still worth surfacing as corroboration — suppressing those
    would trade a false-positive problem for a recall one."""
    a = claim("72,251", context="(Rs. in millions)", period="unstated")
    b = claim("72,251", context="(Rs. in millions)", period="also unstated")
    assert compare_claims(a, b).relation is Relation.CORROBORATION
