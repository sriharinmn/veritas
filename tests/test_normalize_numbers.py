"""Numeric parsing and canonicalisation.

Everything downstream compares `canonical_magnitude`, so a bug here does not
produce a small error — it produces a contradiction that is wrong by a factor of
ten, a hundred, or ten million, and it will look like a real finding.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.models import RatioBasis, ValueKind
from core.normalize.numbers import parse_number, parse_range, parse_value
from core.normalize.scale import parse_scale, scale_from_header


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Indian digit grouping. This is the one most naive parsers get wrong.
        ("1,23,456.78", Decimal("123456.78")),
        ("12,34,567", Decimal("1234567")),
        ("1,00,000", Decimal("100000")),
        ("99,99,999", Decimal("9999999")),
        # International grouping.
        ("123,456.78", Decimal("123456.78")),
        ("1,234,567", Decimal("1234567")),
        ("72,251", Decimal("72251")),
        # Plain.
        ("7225", Decimal("7225")),
        ("0.082", Decimal("0.082")),
        (".5", Decimal("0.5")),
        # Negatives in parentheses — the financial-statement convention.
        ("(2,345)", Decimal("-2345")),
        ("(1,23,456)", Decimal("-123456")),
        ("(0.5)", Decimal("-0.5")),
        # Explicit and trailing minus.
        ("-2,345", Decimal("-2345")),
        ("2,345-", Decimal("-2345")),
        # Nil markers are a stated value, not a missing one.
        ("NIL", Decimal(0)),
        ("Nil", Decimal(0)),
        ("—", Decimal(0)),
        ("–", Decimal(0)),
        ("N/A", Decimal(0)),
    ],
)
def test_parse_number(text, expected):
    p = parse_number(text)
    assert p is not None, f"{text!r} did not parse"
    assert p.signed == expected


@pytest.mark.parametrize("text", ["", "   ", "revenue", "abc", None])
def test_parse_number_rejects_non_numbers(text):
    assert parse_number(text) is None


@pytest.mark.parametrize(
    ("text", "multiplier"),
    [
        ("crore", Decimal(10) ** 7),
        ("crores", Decimal(10) ** 7),
        ("Cr", Decimal(10) ** 7),
        ("lakh", Decimal(10) ** 5),
        ("lakhs", Decimal(10) ** 5),
        ("lacs", Decimal(10) ** 5),
        ("million", Decimal(10) ** 6),
        ("mn", Decimal(10) ** 6),
        ("billion", Decimal(10) ** 9),
        ("bn", Decimal(10) ** 9),
        ("thousand", Decimal(10) ** 3),
        ("trillion", Decimal(10) ** 12),
        ("lakh crore", Decimal(10) ** 12),
    ],
)
def test_parse_scale(text, multiplier):
    assert parse_scale(text) == multiplier


@pytest.mark.parametrize(
    ("header", "multiplier"),
    [
        ("(₹ in millions)", Decimal(10) ** 6),
        ("(Rs. in crores)", Decimal(10) ** 7),
        ("all amounts in lakhs", Decimal(10) ** 5),
        ("figures are in millions", Decimal(10) ** 6),
        ("(₹ in crore)", Decimal(10) ** 7),
        ("Amounts in ₹ mn", Decimal(10) ** 6),
    ],
)
def test_scale_from_header(header, multiplier):
    """Financial PDFs state the scale once, in a caption, and then never again.

    A claim extracted fifty pages later has to inherit it — which is why this
    function exists and why claims that inherit across a page break are flagged.
    """
    assert scale_from_header(header) == multiplier


def test_the_case_that_makes_corroboration_work():
    """"Rs. 72,251 mn" and "₹7,225 crore" are the same fact.

    Different currency notation, different scale, different rounding. If the
    system cannot see through that, it cannot corroborate anything across
    documents, because no two documents write a number the same way.
    """
    a = parse_value("72,251", context="(Rs. in millions)")
    b = parse_value("7,225", context="(₹ in crore)")

    assert a.kind is ValueKind.MONEY and b.kind is ValueKind.MONEY
    assert a.currency == "INR" and b.currency == "INR"

    assert a.canonical_magnitude == Decimal("72251000000")
    assert b.canonical_magnitude == Decimal("72250000000")

    # Within a rounding tolerance the two agree — 0.0014% apart.
    rel = abs(a.canonical_magnitude - b.canonical_magnitude) / a.canonical_magnitude
    assert rel < Decimal("0.001")


@pytest.mark.parametrize(
    ("text", "canonical"),
    [
        ("8.2%", Decimal("0.082")),
        ("8.2 per cent", Decimal("0.082")),
        ("8.2 percent", Decimal("0.082")),
        ("6.5%", Decimal("0.065")),
        ("35 bps", Decimal("0.0035")),
        ("35 basis points", Decimal("0.0035")),
        ("100 bps", Decimal("0.01")),
    ],
)
def test_ratios_canonicalise_to_a_plain_fraction(text, canonical):
    """8.2%, 820 bps and 0.082 are one value written three ways.

    The comparator should never have to know which form it was handed.
    """
    v = parse_value(text)
    assert v.kind is ValueKind.RATIO
    assert v.canonical_magnitude == canonical


def test_percent_and_bps_are_directly_comparable():
    a = parse_value("0.35%")
    b = parse_value("35 bps")
    assert a.canonical_magnitude == b.canonical_magnitude


def test_multiple_is_not_a_percentage():
    v = parse_value("1.4x")
    assert v.ratio_basis is RatioBasis.MULTIPLE
    assert v.canonical_magnitude == Decimal("1.4")


@pytest.mark.parametrize(
    ("text", "low", "high"),
    [
        ("6.3 to 6.8", Decimal("6.3"), Decimal("6.8")),
        ("between 6.3 and 6.8", Decimal("6.3"), Decimal("6.8")),
        ("6.3-6.8", Decimal("6.3"), Decimal("6.8")),
        ("6.3 – 6.8", Decimal("6.3"), Decimal("6.8")),
    ],
)
def test_parse_range(text, low, high):
    r = parse_range(text)
    assert r == (low, high)


def test_a_hyphen_between_distant_numbers_is_not_a_range():
    """"pages 26-278" and "2016-2024" are not numeric ranges.

    Requiring the two ends to sit within an order of magnitude is a cheap guard
    against a common false positive.
    """
    assert parse_range("26-278") is None


def test_a_projection_inside_a_range_is_agreement_not_conflict():
    """The IMF projects 6.5%; the Economic Survey projects 6.3-6.8%.

    These corroborate. A system that cannot represent a range will report a
    contradiction here, which is exactly the kind of false alarm that would
    destroy a banker's trust in the output.
    """
    survey = parse_value("6.3 to 6.8 per cent")
    imf = parse_value("6.5%")

    assert survey.is_range
    assert survey.range_low <= imf.canonical_magnitude <= survey.range_high


def test_negative_money_keeps_its_sign_through_scaling():
    v = parse_value("(2,345)", context="(₹ in crore)")
    assert v.canonical_magnitude == Decimal("-23450000000")


def test_scale_in_the_value_beats_scale_in_the_context():
    """A value that states its own scale must not be re-scaled by a stale header.

    This is the failure that silently multiplies a figure by ten million.
    """
    v = parse_value("5 crore", context="(₹ in millions)")
    assert v.canonical_magnitude == Decimal("50000000")


def test_bare_number_with_no_context_is_a_quantity_not_money():
    """Never invent a currency. An unlabelled number is a quantity."""
    v = parse_value("1,234")
    assert v.kind is ValueKind.QUANTITY
    assert v.currency is None


# ── an inheritance is weaker evidence than what is next to the number ────────


def test_a_number_with_its_own_unit_is_not_money():
    """A document-level reporting currency must not swallow a headcount.

    Every extractor passes the document's "(in INR million)" statement down, and
    that is what lets a bare table cell inherit its scale fifty pages from the
    caption. But currency was tested before the unit, so "and employed 58,400
    people" came out as ₹58.4 billion — a fact that reads as precise, compares
    against revenue, and is entirely invented.

    The number names what it counts, right beside it. Adjacent evidence beats an
    inheritance from the front matter, and the inherited scale is refused along
    with the currency: 58,400 people are not 58.4 billion people.
    """
    v = parse_value("58,400", context="and employed 58,400 people.", inherited="INR million")
    assert v is not None
    assert v.kind is ValueKind.QUANTITY
    assert v.unit == "count"
    assert v.canonical_magnitude == Decimal(58400)


def test_a_scale_beside_a_count_is_still_its_own():
    """Refusing the inheritance must not refuse a scale the number really has."""
    v = parse_value("2.8", context="2.8 Bn shipments", inherited="INR million")
    assert v is not None
    assert v.kind is ValueKind.QUANTITY
    assert v.canonical_magnitude == Decimal("2800000000")


def test_an_inherited_currency_still_reaches_a_bare_table_cell():
    """The guard narrows the inheritance; it must not undo it."""
    v = parse_value("72,251", context="Revenue from operations", inherited="(Rs. in millions)")
    assert v is not None
    assert v.kind is ValueKind.MONEY
    assert v.canonical_magnitude == Decimal("72251000000")


def test_a_grouped_number_is_not_a_percentage():
    """A column header can name a currency and a percent at the same time.

    The earnings deck's Adjusted EBITDA bridge is headed

        ₹ Cr | Q4 FY23 | Q3 FY24 | Q4 FY24 | QoQ% | YoY% | FY23 | FY24 | YoY%

    which is a perfectly ordinary header row: some of its columns are rupees and
    some are percentages. It is short, so no length rule rejects it, and it is
    adjacent, so no proximity rule rejects it. Read as evidence about a rupee
    cell, it turned ₹1,860 crore of revenue into 18.6%.

    What separates them is how the number is written. Financial documents do not
    put a thousands separator in a percentage — 5.4%, 11.6%, 99.79% — so a
    grouped numeral whose only percent evidence comes from context is a
    quantity that happened to sit under a mixed header.

    The cost is a genuine percentage above a thousand written with a comma,
    which is refused. That is a rarer document than a mixed table header, and a
    much milder error than a revenue line stored as a rate.
    """
    v = parse_value("1,860", context="Revenue for services (A) ₹ Cr | QoQ% | YoY%")
    assert v is not None
    assert v.kind is ValueKind.MONEY
    assert v.canonical_magnitude == Decimal("18600000000")

    ungrouped = parse_value("99.79", context="Revenue from Services 99.79%")
    assert ungrouped is not None
    assert ungrouped.kind is ValueKind.RATIO


def test_a_row_label_beats_a_percent_from_elsewhere_in_the_table():
    """One table, several sections, several units.

    The IMF's macroeconomic framework flattens into a single header path naming
    "Growth (percent change)" near the top and "Gross reserves (in billions of
    U.S. dollars)" further down. Reading that header as evidence turns $607.3
    billion of reserves into 6.073; ignoring it turns real GDP growth of 9.7%
    into a unitless 9.7. Both were tried and both are wrong.

    What settles it is the row label, which sits right beside the number. An
    adjacent currency or scale beats a percent sign from a section eight rows
    above; where the adjacent text says nothing, the header is all there is and
    is used.
    """
    header = (
        "Table 6. India: Macroeconomic Framework Growth (percent change) Real GDP "
        "Prices (percent change, period average) Gross reserves"
    )

    reserves = parse_value(
        "607.3", context="(in billions of U.S. dollars, end-period) 607.3", inherited=header
    )
    assert reserves is not None
    assert reserves.kind is ValueKind.MONEY

    growth = parse_value("9.7", context="Real GDP (at market prices) 9.7", inherited=header)
    assert growth is not None
    assert growth.kind is ValueKind.RATIO
    assert growth.canonical_magnitude == Decimal("0.097")
