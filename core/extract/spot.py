"""The spot sweep — free, deterministic, exhaustive candidate detection.

This runs before any model does. A regex pass finds every numeral, percentage,
currency amount and date on a page, together with the window of text around it
and the table header it sits under. The LLM is then asked only to *type and
scope* a candidate it has been handed, never to "find the facts on this page".

Two things follow, and the second is the more important one.

**Cost.** Pages with no candidates are skipped entirely, and the rest are sent
as narrow windows rather than whole blocks. Measured on the starter corpus this
is a 2-3x reduction in tokens, which is what makes the Groq tier viable for
documents two to three times larger than it otherwise would be.

**A denominator.** Recall is normally unmeasurable — you cannot count the facts
a model failed to notice. But a regex sweep is exhaustive over numerals *by
construction*, so every number in the document is accounted for exactly once:

    1,847 spotted
      → 1,203 became grounded claims
      →   312 correctly rejected as non-facts
      →   198 quarantined by the grounding verifier
      →   134 dropped silently          ← these are the bugs

That last bucket is measurable recall loss on any document, including one a
grader uploads, without a single hand-written label. With a golden set of only
60-80 items it is worth more than the labels are.

The honest cost of this design is that it biases the system toward numeric
facts. That is why a separate block-level semantic pass exists for
directorships, addresses and status changes, with its own budget and its own
line in the eval table rather than being averaged in.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid4

from core.normalize.numbers import NUMBER_RE
from core.parse.columns import infer_column_headers
from core.parse.pdf import Block, Page, ParsedDocument

CandidateKind = Literal["money", "percent", "quantity", "date", "duration"]

WINDOW = 220  # characters either side; enough for a sentence or a table row

# A unit, scale or currency marker binds tightly to its number — "₹72,251
# million", "8.2 per cent", "35 bps". Searching the whole window for one is a
# serious bug rather than a loose heuristic: on the first full corpus run a
# share count of 9,324,309 picked up "million" from 200 characters away and
# became 9.3 trillion, and another picked up a stray "%" and was divided by a
# hundred. Both then produced confident contradictions.
#
# Document-level scale ("all figures in ₹ millions") is a real and necessary
# inheritance, but it is a *separate, explicit* path with its own evidence row —
# not something absorbed by accident from whatever happens to be nearby.
TIGHT = 28

# Only genuine currency markers imply money. Scale words must NOT: "2.8 Bn
# shipments" and "18,793 pin codes" are quantities, and calling them money makes
# the system compare a parcel count against a revenue figure. Scale is a
# separate property from currency and is resolved during normalisation.
_CURRENCY_NEAR = re.compile(
    r"[₹$€£¥]|\b(?:rs\.?|inr|usd|eur|gbp|jpy|rupees?|dollars?|revenue|ebitda|profit|loss|"
    r"income|expense|cost|turnover|capex|opex|cash|debt|equity|assets?|liabilit(?:y|ies))\b",
    re.IGNORECASE,
)
_PERCENT_NEAR = re.compile(r"%|\bper\s*cent|\bbps\b|\bbasis\s+points?\b", re.IGNORECASE)

# Any other numeral. Used to cut the tight window short, because distance alone
# cannot decide which number a marker belongs to.
#
# "Rs. 7,225 crore, an increase of 12.4% over the prior year" puts a percent
# sign 22 characters after 7,225 — comfortably inside TIGHT — and the value
# became 72.25, a ratio, which stopped being comparable to the same figure
# printed as 72,251 in the statements. No window size separates these: the sign
# really is nearby.
#
# What settles it is that 12.4 sits between them. A unit, scale or currency
# marker binds to the number it is *adjacent* to, so the window stops at the
# nearest other numeral on each side, and anything beyond that numeral is its
# business rather than ours.
_OTHER_NUMERAL = re.compile(r"\d(?:[\d,.  ]*\d)?")

# The same, plus whatever unit is attached to it. Needed only when trimming
# *backwards*: cutting at the end of the previous numeral leaves that numeral's
# own suffix behind, and the suffix is the part that does the damage.
#
# A prospectus prints revenue and its share of the total down one flattened
# column -- 16,538.97 / 100.00% / 27,748.25 / 99.79% -- so every figure but the
# first inherited a percent sign from the line above it, and 29 revenue lines on
# a single page were stored as percentages. Cutting backwards at "100.00%"
# rather than at "100.00" is the whole of the fix.
_NUMERAL_WITH_UNIT = re.compile(
    r"\d(?:[\d,.  ]*\d)?"
    + r"\s*(?:%|per\s*cent(?:age)?|bps|basis\s+points?|"
    r"crores?|lakhs?|lacs?|millions?|billions?|trillions?|thousands?|mn|bn|cr)?",
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4})\b",
    re.IGNORECASE,
)

# See core/normalize/periods.py for why the leading boundary is a two-letter
# negative lookbehind rather than \b: text runs in slide decks arrive glued
# together ("aFY24"), and a failed period match leaks the year out as a bare
# number that then gets reported as a fact.
_PERIOD_RE = re.compile(
    r"(?<![A-Za-z]{2})(?:Q[1-4]\s*[-/ ]?\s*FY\s*'?\d{2,4}"
    r"|(?:3|6|9|12)\s*M\s*[-/ ]?\s*FY\s*'?\d{2,4}"
    r"|H[12]\s*[-/ ]?\s*(?:FY|CY)\s*'?\d{2,4}"
    r"|FY\s*'?\d{2,4}"
    r"|CY\s*'?\d{2,4}"
    r"|\d{4}\s*[-–]\s*\d{2,4})\b",
    re.IGNORECASE,
)

# Tokens that are almost never facts. These are still spotted and counted —
# suppressing them here would corrupt the denominator — but they carry a hint so
# the extraction pass can order its budget sensibly and so the "correctly
# rejected" bucket can be distinguished from the "silently dropped" one.
_NOISE_CONTEXT = re.compile(
    r"\b(?:page|pg\.?|note|notes|clause|section|para(?:graph)?|annexure|schedule|"
    r"regulation|rule|chapter|table|figure|fig\.?|exhibit|item|sr\.?\s*no|s\.?\s*no)\s*$",
    re.IGNORECASE,
)


# A folio marker: the one number on a page that means nothing.
#
# Page 247 of the prospectus is a table of share-capital amendments with "247"
# printed alone at the foot of it, and that became the claim "share capital =
# 247" — perfectly grounded, since the number really is on the page, and
# completely false. `_NOISE_CONTEXT` cannot catch it, because it looks for an
# introducing word ("page 12", "note 4") and a folio marker has no words at all.
#
# What identifies it is where it sits: a block holding nothing but a bare
# integer, alone in the top or bottom margin. Both halves are needed — the same
# block in the body of a page is a table cell holding a real value.
_FOLIO_MARGIN = 0.06  # fraction of page height at each edge
_FOLIO_RE = re.compile(r"^[ivxlcdm]*\s*\d{1,4}\s*[ivxlcdm]*$", re.IGNORECASE)


def is_folio(block: Block) -> bool:
    """Is this block a page number in a margin, rather than a value on the page?"""
    if not _FOLIO_RE.match(block.text.strip()):
        return False
    if not block.rects:
        return False
    top = min(r.y0 for r in block.rects)
    bottom = max(r.y1 for r in block.rects)
    return bottom <= _FOLIO_MARGIN or top >= 1 - _FOLIO_MARGIN


@dataclass
class Candidate:
    """One spotted value, with everything the extractor needs to interpret it."""

    id: UUID
    page: int
    block_id: UUID
    block_kind: str
    # A routing hint, not a determination. The authoritative type is settled by
    # normalisation and the extraction pass; this only decides which prompt and
    # which parser a candidate is sent to first, so an imperfect guess costs a
    # little precision in routing and nothing in correctness.
    kind: CandidateKind
    text: str
    char_start: int
    char_end: int
    window: str
    window_start: int
    # The immediate neighbourhood, for unit/scale/currency detection only.
    tight: str = ""
    header_path: str | None = None
    # Recovered from a header the table detector found while flattening the body
    # into plain text. Carries the period and often the reporting basis, and is
    # the difference between four comparable claims and four that look identical
    # in scope while holding different values.
    column_header: str | None = None
    # The row label of a recovered table row — "Revenue from Operations". Once a
    # table body is flattened, the label sits on its own line and anything that
    # looks backwards from the number finds only a line break.
    row_label: str | None = None
    # Set when the surrounding text marks this as a reference rather than a
    # measurement ("note 12", "page 47"). Counted, never silently discarded.
    noise_hint: bool = False

    @property
    def scope_hint(self) -> str:
        """The strongest available statement of which column this value sits in.

        A recovered column header beats a detected table header, because the
        recovered one carries the period and usually the reporting basis, and
        those are what make two figures comparable rather than contradictory.
        """
        return self.column_header or self.header_path or ""

    @property
    def context(self) -> str:
        """What the extractor sees alongside the value: column, then window.

        The column comes first because a bare "72,251" in a financial table gets
        its meaning almost entirely from the column it sits under — that is
        where the period and the reporting basis live.
        """
        parts = []
        if self.row_label:
            parts.append(f"[row: {self.row_label}]")
        if self.scope_hint:
            parts.append(f"[column: {self.scope_hint}]")
        parts.append(self.window)
        return "\n".join(parts)


@dataclass
class PageSpots:
    page: int
    candidates: list[Candidate]

    @property
    def density(self) -> int:
        """Signal-bearing candidates, used to order work on large documents."""
        return sum(1 for c in self.candidates if not c.noise_hint)


@dataclass
class DocumentSpots:
    pages: list[PageSpots] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(len(p.candidates) for p in self.pages)

    @property
    def signal(self) -> int:
        return sum(p.density for p in self.pages)

    def by_kind(self) -> dict[str, int]:
        c: Counter[str] = Counter()
        for p in self.pages:
            for cand in p.candidates:
                c[cand.kind] += 1
        return dict(c)

    def pages_by_density(self) -> list[int]:
        """Page numbers, densest first.

        This is the work order for a large document. The financial statements
        and KPI tables are processed first and the signature pages last, so a
        reader sees real facts within seconds of uploading a 500-page filing
        rather than waiting for a progress bar to cross a document they do not
        care about all of.
        """
        return [p.page for p in sorted(self.pages, key=lambda p: -p.density) if p.candidates]


def _bind(before: str, after: str) -> tuple[str, str]:
    """The text on each side that belongs to this number.

    Both arguments are block-local, because a marker in a neighbouring block is
    not adjacent to anything here: the row below "Profit/(loss) after tax" is
    "EBITDA margin (%)", and letting that percent sign reach backwards turned a
    loss of 2,410 million into -24.1%.

    Within the block, each side stops at the nearest other numeral — see
    _OTHER_NUMERAL — with one addition. When a table row is flattened its label
    ends up on its own line above the figures:

        EBITDA margin (%)
        8.2
        6.1

    The label qualifies every value in the row, but 8.2 sits between the label
    and 6.1, so trimming alone leaves the second column with nothing and 6.1
    stops being a percentage. A first line carrying no numerals of its own is a
    row label rather than a column header, and is prepended for every value
    below it.
    """
    head, sep, _ = before.partition("\n")
    qualifier = head if sep and not _OTHER_NUMERAL.search(head) else ""

    lead = before[-TIGHT:]
    preceding = list(_NUMERAL_WITH_UNIT.finditer(lead))
    if preceding:
        lead = lead[preceding[-1].end() :]
    if qualifier and qualifier not in lead:
        lead = f"{qualifier}\n{lead}"

    trail = after[:TIGHT]
    following = _OTHER_NUMERAL.search(trail)
    if following:
        trail = trail[: following.start()]
    return lead, trail


def _classify(token: str, before: str, after: str) -> CandidateKind:
    lead, trail = _bind(before, after)
    near = f"{lead} {token} {trail}"
    if _PERCENT_NEAR.search(near):
        return "percent"
    if _CURRENCY_NEAR.search(near):
        return "money"
    return "quantity"


def spot_block(block: Block, page_text: str) -> list[Candidate]:
    out: list[Candidate] = []
    text = block.text
    base = block.char_start
    claimed: list[tuple[int, int]] = []

    def _overlaps(a: int, b: int) -> bool:
        return any(a < y and x < b for x, y in claimed)

    # Dates and fiscal periods first: they contain digits, and letting the
    # number pattern shred "31 March 2024" into "31" and "2024" would both
    # inflate the denominator and destroy the temporal information.
    for pattern, kind in ((_DATE_RE, "date"), (_PERIOD_RE, "duration")):
        for m in pattern.finditer(text):
            if _overlaps(m.start(), m.end()):
                continue
            claimed.append((m.start(), m.end()))
            out.append(_make(block, page_text, base, m.start(), m.end(), m.group(0), kind))  # type: ignore[arg-type]

    for m in NUMBER_RE.finditer(text):
        if _overlaps(m.start(), m.end()):
            continue
        token = m.group(0)
        # A lone digit is almost always a bullet, a footnote marker or a list
        # index. Spotting them would triple the denominator with pure noise.
        if len(token.replace(",", "").replace(".", "")) < 2:
            continue
        claimed.append((m.start(), m.end()))
        out.append(
            _make(
                block,
                page_text,
                base,
                m.start(),
                m.end(),
                token,
                _classify(token, text[: m.start()], text[m.end() :]),
            )
        )

    return out


def _make(
    block: Block,
    page_text: str,
    base: int,
    rel_start: int,
    rel_end: int,
    token: str,
    kind: CandidateKind,
) -> Candidate:
    abs_start = base + rel_start
    abs_end = base + rel_end

    # Accounting negatives are written in parentheses, and the numeral regex
    # stops at the digits. Left alone, "(1,819.95)" is spotted as "1,819.95" and
    # a negative working-capital movement enters the knowledge layer positive.
    #
    # Measured before this fix: of 9,453 claims across the corpus, **none** were
    # negative and 732 — 7.7% — were parenthesised negatives stored with the
    # sign dropped. A loss margin of (105.22)% was recorded as +105.22%. In a
    # system whose entire purpose is deciding whether two figures agree, an
    # inverted sign does not merely lose information: it manufactures both false
    # agreements and false conflicts.
    #
    # Widening the span rather than post-processing the value keeps the
    # grounding invariant intact — the parentheses really are in the page text,
    # so the claim still quotes its source verbatim.
    if (
        abs_start > 0
        and abs_end < len(page_text)
        and page_text[abs_start - 1] == "("
        and page_text[abs_end] == ")"
    ):
        abs_start -= 1
        abs_end += 1
        token = page_text[abs_start:abs_end]

    w_start = max(0, abs_start - WINDOW)
    w_end = min(len(page_text), abs_end + WINDOW)
    # Block-local, so the tight window cannot reach into the row above or below.
    inner_start = abs_start - base
    inner_end = abs_end - base
    lead, trail = _bind(block.text[:inner_start], block.text[inner_end:])
    tight = f"{lead}{page_text[abs_start:abs_end]}{trail}"
    before = block.text[:rel_start]
    return Candidate(
        id=uuid4(),
        page=block.page,
        block_id=block.id,
        block_kind=block.kind,
        kind=kind,
        text=token,
        char_start=abs_start,
        char_end=abs_end,
        window=page_text[w_start:w_end],
        window_start=w_start,
        tight=tight,
        header_path=block.header_path,
        noise_hint=bool(_NOISE_CONTEXT.search(before.rstrip()[-30:])) or is_folio(block),
    )


def spot_page(page: Page) -> PageSpots:
    cands: list[Candidate] = []
    for block in page.blocks:
        cands.extend(spot_block(block, page.text))
    cands.sort(key=lambda c: c.char_start)

    # Reattach column headers the table detector separated from their values.
    columns = infer_column_headers(page)
    if len(columns):
        for c in cands:
            cell = columns.lookup(c.char_start)
            if cell is not None:
                c.column_header = cell.column
                c.row_label = cell.row_label

    return PageSpots(page=page.number, candidates=cands)


def spot_document(doc: ParsedDocument) -> DocumentSpots:
    return DocumentSpots(pages=[spot_page(p) for p in doc.pages])
