"""The spot sweep.

The sweep's whole value is that it is *exhaustive by construction* — that is
what turns it into a recall denominator. So the tests here care most about two
things: that offsets stay aligned with the page text, and that nothing which
looks like a value is silently skipped.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from core.extract.spot import spot_block, spot_document, spot_page
from core.parse.pdf import Block, Page, parse_pdf

SEED = Path(__file__).resolve().parents[1] / "seed"
DECK = SEED / "delhivery" / "03-delhivery-q4-fy24-earnings-presentation.pdf"


def _page(text: str, *, header: str | None = None, kind: str = "paragraph") -> Page:
    block = Block(
        id=uuid4(),
        page=1,
        kind=kind,  # type: ignore[arg-type]
        text=text,
        char_start=0,
        char_end=len(text),
        rects=[],
        header_path=header,
    )
    return Page(number=1, text=text, blocks=[block], width=595.0, height=842.0)


def _texts(page: Page) -> list[str]:
    return [c.text for c in spot_page(page).candidates]


def test_finds_numbers_in_every_grouping_convention():
    got = _texts(_page("Revenue of 1,23,456.78 and 123,456.78 and 72251 were recorded."))
    assert "1,23,456.78" in got
    assert "123,456.78" in got
    assert "72251" in got


def test_dates_are_not_shredded_into_digits():
    """"31 March 2024" must survive as one candidate.

    Letting the number pattern split it would both inflate the denominator with
    three meaningless tokens and destroy the temporal information that makes the
    claim comparable at all.
    """
    spots = spot_page(_page("The board met on 31 March 2024 to approve the accounts."))
    kinds = {c.kind for c in spots.candidates}
    assert "date" in kinds
    dates = [c.text for c in spots.candidates if c.kind == "date"]
    assert dates == ["31 March 2024"]
    assert "2024" not in [c.text for c in spots.candidates if c.kind != "date"]


@pytest.mark.parametrize(
    "label", ["FY24", "Q4FY24", "9MFY25", "H1CY25", "CY2024", "2023-24"]
)
def test_fiscal_periods_are_spotted_whole(label):
    spots = spot_page(_page(f"Results for {label} were strong."))
    durations = [c.text for c in spots.candidates if c.kind == "duration"]
    assert label in durations


def test_percentages_and_bps_are_classified_as_ratios():
    spots = spot_page(_page("Margin improved 240 bps to 8.2% during the year."))
    kinds = {c.text: c.kind for c in spots.candidates}
    assert kinds.get("8.2") == "percent"
    assert kinds.get("240") == "percent"


def test_scale_words_alone_do_not_make_a_number_money():
    """"2.8 Bn shipments" is a quantity.

    Treating a scale word as a currency marker is how a system ends up comparing
    a parcel count against a revenue figure and reporting a contradiction.
    """
    spots = spot_page(_page("Delhivery delivered >2.8 Bn express parcel shipments since inception."))
    kinds = {c.text: c.kind for c in spots.candidates}
    assert kinds.get("2.8") != "money"


def test_currency_markers_do_make_a_number_money():
    spots = spot_page(_page("Revenue was Rs. 72,251 million for the year."))
    kinds = {c.text: c.kind for c in spots.candidates}
    assert kinds.get("72,251") == "money"


def test_lone_digits_are_not_candidates():
    """Bullets, footnote markers and list indices would triple the denominator
    with pure noise, and a denominator you do not trust is worse than none."""
    got = _texts(_page("1 Overview\n2 Strategy\n3 Outlook"))
    assert got == []


def test_reference_numbers_are_flagged_but_still_counted():
    """"note 12" is not a fact, but suppressing it here would corrupt the
    denominator. It is spotted, hinted as noise, and counted as correctly
    rejected later — which is what separates a rejection from a silent drop."""
    spots = spot_page(_page("Refer to note 12 and page 47 for details."))
    hinted = [c for c in spots.candidates if c.noise_hint]
    assert {c.text for c in hinted} >= {"12", "47"}
    assert spots.density < len(spots.candidates)


def test_table_header_travels_with_the_candidate():
    """A bare "72,251" in a table cell gets its meaning from its column."""
    page = _page(
        "| Revenue from operations | 72,251 |",
        header="Particulars | Year ended March 31, 2024",
        kind="table",
    )
    c = next(c for c in spot_page(page).candidates if c.text == "72,251")
    assert c.header_path is not None
    assert c.scope_hint == "Particulars | Year ended March 31, 2024"
    assert c.context.startswith("[column:")
    assert "March 31, 2024" in c.context


def test_the_window_actually_contains_the_value():
    page = _page("x" * 500 + " Revenue was Rs. 72,251 million. " + "y" * 500)
    c = next(c for c in spot_page(page).candidates if c.text == "72,251")
    assert "72,251" in c.window
    assert "Revenue" in c.window


# ── against the real corpus ──────────────────────────────────────────────────


@pytest.mark.skipif(not DECK.exists(), reason="starter corpus not present")
def test_offsets_stay_aligned_with_page_text_on_a_real_document():
    """The invariant that makes a candidate citable.

    Every downstream claim inherits these offsets, so drift here would put every
    highlight in the product slightly in the wrong place.
    """
    doc = parse_pdf(DECK)
    checked = 0
    for page in doc.pages:
        for c in spot_page(page).candidates:
            assert page.text[c.char_start : c.char_end] == c.text
            checked += 1
    assert checked > 500


@pytest.mark.skipif(not DECK.exists(), reason="starter corpus not present")
def test_density_ordering_puts_fact_dense_pages_first():
    """The work order for a large document. A reader should see real facts
    seconds after uploading, not after a progress bar crosses the whole file."""
    doc = parse_pdf(DECK)
    spots = spot_document(doc)
    order = spots.pages_by_density()

    assert len(order) > 5
    densities = {p.page: p.density for p in spots.pages}
    assert densities[order[0]] >= densities[order[-1]]
    assert densities[order[0]] > 0


@pytest.mark.skipif(not DECK.exists(), reason="starter corpus not present")
def test_the_sweep_is_cheap_relative_to_parsing():
    """The two-phase strategy only works if spotting is nearly free."""
    import time

    doc = parse_pdf(DECK, detect_tables=False)
    t = time.perf_counter()
    spots = spot_document(doc)
    elapsed = time.perf_counter() - t

    assert spots.total > 500
    assert elapsed < 1.0, f"spot sweep took {elapsed:.2f}s; it must stay nearly free"


def test_empty_and_textless_pages_do_not_crash():
    assert spot_page(_page("")).candidates == []
    assert spot_block(
        Block(
            id=uuid4(), page=1, kind="paragraph", text="", char_start=0, char_end=0, rects=[]
        ),
        "",
    ) == []


def test_unit_markers_do_not_leak_from_far_away():
    """A unit binds tightly to its number.

    Found on the first full corpus run: a share count of 9,324,309 picked up
    "million" from ~200 characters away and became 9.3 trillion, and another
    picked up a stray "%" and was divided by a hundred. Both then produced
    confident contradictions against correctly-parsed figures — the worst kind
    of failure, because the output looks precise.
    """
    far = "revenue of 52,350 million rupees" + ("x" * 150) + " 9,324,309 equity shares"
    page = _page(far)
    c = next(c for c in spot_page(page).candidates if c.text == "9,324,309")

    assert "million" not in c.tight
    assert "9,324,309" in c.tight
    # The wide window still carries it, because a model reading the sentence
    # should see the context even though the unit parser must not.
    assert "million" in c.window


def test_a_distant_percent_sign_does_not_make_a_count_a_ratio():
    page = _page("margin improved to 8.2%" + ("y" * 120) + " 46,131,800 equity shares")
    c = next(c for c in spot_page(page).candidates if c.text == "46,131,800")
    assert "%" not in c.tight


def test_a_nearby_unit_is_still_picked_up():
    """The fix must not go so far that real units stop being seen."""
    page = _page("Revenue from operations was Rs. 72,251 million for the year.")
    c = next(c for c in spot_page(page).candidates if c.text == "72,251")
    assert "million" in c.tight
    assert "Rs." in c.tight


# ── accounting negatives ─────────────────────────────────────────────────────


def test_a_parenthesised_negative_keeps_its_sign():
    """Financial statements write negatives in parentheses, and the numeral
    regex stops at the digits.

    Measured before this was fixed: of 9,453 claims across the corpus, *none*
    were negative and 724 — 7.7% — were parenthesised negatives with the sign
    dropped. A profit-after-tax margin of (105.22)% was stored as +105.22%. For
    a system that exists to decide whether two figures agree, an inverted sign
    does not lose information, it invents agreements and conflicts.
    """
    page = _page("Movements in working capital (1,819.95) for the year")
    assert "(1,819.95)" in _texts(page)


def test_the_widened_span_still_quotes_the_page_verbatim():
    """Widening rather than post-processing the value is what keeps grounding
    honest: the parentheses really are in the page text, so the claim still
    reproduces its source exactly."""
    text = "Loss before tax (452) million"
    page = _page(text)
    for c in spot_page(page).candidates:
        assert text[c.char_start : c.char_end] == c.text


def test_an_unmatched_parenthesis_is_left_alone():
    """Only a value wrapped on both sides is a negative. A bracketed note
    reference or a half-open parenthesis is not."""
    assert "(452" not in _texts(_page("See note (452 onwards for detail"))
    assert "452)" not in _texts(_page("Refer clause 452) of the agreement"))


def test_a_parenthesised_value_normalises_negative():
    from core.normalize.numbers import parse_value

    page = _page("Movements in working capital (1,819.95) million")
    candidate = next(c for c in spot_page(page).candidates if c.text == "(1,819.95)")
    value = parse_value(candidate.text, context=candidate.tight)
    assert value is not None
    assert value.canonical_magnitude < 0
