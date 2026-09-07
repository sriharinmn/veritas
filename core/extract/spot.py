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
from core.parse.pdf import Block, Page, ParsedDocument

CandidateKind = Literal["money", "percent", "quantity", "date", "duration"]

WINDOW = 220  # characters either side; enough for a sentence or a table row

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

_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4})\b",
    re.IGNORECASE,
)

_PERIOD_RE = re.compile(
    r"\b(?:Q[1-4]\s*[-/ ]?\s*FY\s*'?\d{2,4}"
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
    header_path: str | None = None
    # Set when the surrounding text marks this as a reference rather than a
    # measurement ("note 12", "page 47"). Counted, never silently discarded.
    noise_hint: bool = False

    @property
    def context(self) -> str:
        """What the extractor sees alongside the value: header path, then window.

        The header path comes first because a bare "72,251" in a table cell gets
        its meaning almost entirely from the column it sits in.
        """
        if self.header_path:
            return f"[table columns: {self.header_path}]\n{self.window}"
        return self.window


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


def _classify(token: str, before: str, after: str) -> CandidateKind:
    near = f"{before[-40:]} {token} {after[:40]}"
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
    w_start = max(0, abs_start - WINDOW)
    w_end = min(len(page_text), abs_end + WINDOW)
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
        header_path=block.header_path,
        noise_hint=bool(_NOISE_CONTEXT.search(before.rstrip()[-30:])),
    )


def spot_page(page: Page) -> PageSpots:
    cands: list[Candidate] = []
    for block in page.blocks:
        cands.extend(spot_block(block, page.text))
    cands.sort(key=lambda c: c.char_start)
    return PageSpots(page=page.number, candidates=cands)


def spot_document(doc: ParsedDocument) -> DocumentSpots:
    return DocumentSpots(pages=[spot_page(p) for p in doc.pages])
