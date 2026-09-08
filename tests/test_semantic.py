"""Non-numeric facts, and the guarantee that keeps them honest.

The numeric path cannot hallucinate a figure: a numeral is located by regex
first and the model only labels it, so the value in a claim is a substring of
the page by construction. There is no regex for "ceased to be a Director", so
the semantic path keeps the same guarantee a different way — the model must
return a verbatim substring, and we locate it ourselves.

Everything worth testing here is that refusal. A model that paraphrases is the
expected input, not an anomaly.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from core.extract.gateway import LLMResponse, LLMUnavailable
from core.extract.semantic import (
    _is_a_property,
    _locate,
    extract_semantic_page,
    prose_blocks,
)
from core.models import ValueKind
from core.parse.pdf import Block, Page

PROSE = (
    "Delhivery Limited (formerly known as Delhivery Private Limited) was "
    "incorporated on June 25, 2011. The Scheme was approved by the NCLT vide "
    "order dated November 27, 2019. The statutory auditors are S.R. Batliboi & "
    "Associates LLP, Chartered Accountants."
)


def _page(text: str = PROSE, kind: str = "paragraph") -> Page:
    block = Block(
        id=uuid4(),
        page=1,
        kind=kind,  # type: ignore[arg-type]
        text=text,
        char_start=0,
        char_end=len(text),
        rects=[],
    )
    return Page(number=1, text=text, blocks=[block], width=595.0, height=842.0)


class FakeGateway:
    tier = "fake"
    model = "fake-1"

    def __init__(self, facts: list[dict] | None = None, raises: Exception | None = None):
        self.facts = facts or []
        self.raises = raises
        self.calls = 0

    async def complete_json(self, *, system, user, schema, max_tokens=2048):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return LLMResponse(
            text="{}", parsed={"facts": self.facts}, provider="fake", model=self.model
        )


# ── the verbatim guarantee ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_paraphrased_value_is_refused():
    """The central guard. The page says "formerly known as Delhivery Private
    Limited"; a model that returns "renamed from Delhivery Pvt Ltd" has written
    a value rather than pointed at one, and that is the semantic equivalent of
    a hallucinated digit."""
    gateway = FakeGateway([
        {"s": "Delhivery Limited", "p": "former name", "v": "Delhivery Pvt Ltd", "k": "entity"}
    ])
    claims, refused = await extract_semantic_page(
        _page(), gateway, document_id=uuid4(), run_id=uuid4()
    )
    assert claims == []
    assert refused == 1


@pytest.mark.asyncio
async def test_a_verbatim_value_is_kept_and_really_is_on_the_page():
    gateway = FakeGateway([
        {"s": "Delhivery Limited", "p": "former name",
         "v": "Delhivery Private Limited", "k": "entity"}
    ])
    page = _page()
    claims, refused = await extract_semantic_page(
        page, gateway, document_id=uuid4(), run_id=uuid4()
    )
    assert refused == 0
    assert len(claims) == 1
    claim = claims[0]
    assert claim.value.kind is ValueKind.ENTITY
    assert claim.value.raw in page.text, "the stored value is not on the page"


def test_a_line_break_inside_the_value_is_still_verbatim():
    """PDF text breaks lines mid-sentence. A model reading the passage returns a
    single space where the page has a newline — the same characters in the same
    order, and rejecting it would refuse correct facts for a typographic reason."""
    block = _page("The Company Secretary and\nCompliance Officer signs here.").blocks[0]
    assert _locate("Company Secretary and Compliance Officer", block) is not None


def test_a_value_reordered_or_reworded_is_not_verbatim():
    block = _page().blocks[0]
    assert _locate("Private Delhivery Limited", block) is None
    assert _locate("approved by the tribunal", block) is None


# ── a predicate is a property, not half a sentence ───────────────────────────


@pytest.mark.parametrize(
    "predicate",
    ["former name", "auditor", "cin", "approved by", "date of incorporation"],
)
def test_real_properties_are_accepted(predicate):
    assert _is_a_property(predicate, "S.R. Batliboi & Associates LLP")


@pytest.mark.parametrize(
    ("predicate", "value"),
    [
        # The sentence cut in half: the front calls itself a predicate and the
        # back calls itself a value, and together they are just prose.
        ("is recognised in the carrying", "amount of the plant and equipment"),
        ("are stated at cost, less accumulated depreciation and any", "impairment"),
        ("give a true and fair view", "of the state of affairs"),
        ("in accordance with", "the Standards on Auditing"),
        ("includes the", "cost of replacing part"),
    ],
)
def test_clauses_masquerading_as_properties_are_rejected(predicate, value):
    assert not _is_a_property(predicate, value)


def test_a_value_of_sentence_length_is_rejected():
    assert not _is_a_property(
        "note", "the Group depreciates them separately based on their specific useful lives "
        "as determined by management"
    )


# ── which blocks are worth a call ────────────────────────────────────────────


def test_a_table_of_numbers_is_not_sent_to_the_model():
    """A financial statement page has no semantic facts and would burn the whole
    token budget being told so, correctly."""
    numbers = "Revenue 72,251 68,882 Expenses 88,249 85,968 Total 9,980 6,124 " * 4
    assert prose_blocks(_page(numbers)) == []


def test_a_table_block_is_skipped_by_kind():
    assert prose_blocks(_page(PROSE, kind="table")) == []


def test_prose_is_sent():
    assert len(prose_blocks(_page())) == 1


def test_a_very_short_block_is_skipped():
    assert prose_blocks(_page("Notes to accounts.")) == []


# ── failure handling ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_starved_call_does_not_abandon_the_page():
    """A too-small token budget makes a reasoning model return empty content,
    which surfaces as LLMUnavailable. Treating that as "the provider is down"
    silently discarded every remaining block on the page — two pages reported
    zero facts that way while a third, reached first, found one."""
    gateway = FakeGateway(raises=LLMUnavailable("no content"))
    page = _page()
    # Three blocks, all failing: it should try, then give up, not hang.
    page.blocks = page.blocks * 3
    claims, _ = await extract_semantic_page(
        page, gateway, document_id=uuid4(), run_id=uuid4()
    )
    assert claims == []
    assert gateway.calls == 3, "should attempt each block before giving up"


@pytest.mark.asyncio
async def test_the_same_fact_twice_on_one_page_is_one_fact():
    gateway = FakeGateway([
        {"s": "Delhivery Limited", "p": "former name",
         "v": "Delhivery Private Limited", "k": "entity"},
        {"s": "Delhivery Limited", "p": "former name",
         "v": "Delhivery Private Limited", "k": "entity"},
    ])
    claims, _ = await extract_semantic_page(
        _page(), gateway, document_id=uuid4(), run_id=uuid4()
    )
    assert len(claims) == 1
