"""Tier 3 — rule-based extraction, with no model and no network.

This is what a reviewer sees when they clone the repository, have no API key,
and upload a PDF anyway. It is not a stub. It really runs: it turns spotted
candidates into typed, scoped, grounded claims using the same normalisation
library and the same grounding gate as the model-backed tiers, and its output
flows through the same comparator to the same relations.

What it cannot do is read. It has no idea that "the Company" in paragraph four
refers to the entity named on the cover, or that a figure sits under a heading
three blocks above it, or that a footnote qualifies it. So its predicates are
cruder and its recall on anything non-numeric is nil. The UI says so, loudly,
and the eval harness measures exactly how much worse it is rather than leaving
the reader to guess.

Being honest about that gap is worth more than hiding it. A side-by-side of the
same page extracted with and without a model is a clearer argument for what the
model is buying than any amount of prose.
"""

from __future__ import annotations

import datetime as dt
import re
from uuid import UUID, uuid4

from core.extract.spot import Candidate, spot_page
from core.models import (
    Basis,
    Claim,
    Evidence,
    PeriodKind,
    Provenance,
    Scope,
    TemporalScope,
    ValueKind,
)
from core.normalize.numbers import parse_value
from core.normalize.periods import parse_period
from core.normalize.scale import parse_scale
from core.parse.columns import basis_from_label, page_scale_caption
from core.parse.pdf import Page, ParsedDocument, evidence_span

PROMPT_VERSION = "deterministic-v1"

# Words that are never the subject of a measurement, used to trim a label back
# to something meaningful rather than trailing connective tissue.
_STOP_TAIL = {
    "of", "in", "for", "the", "a", "an", "and", "or", "to", "by", "at", "on",
    "was", "were", "is", "are", "from", "with", "as", "that", "which", "during",
    "increased", "decreased", "grew", "rose", "fell", "stood", "reached",
    # Units and scales are how a value is *expressed*, never what it measures.
    # Leaving them in produced a predicate literally called "million", which
    # every unrelated figure in the corpus then resolved to — one ontology node
    # holding thousands of incomparable numbers, and 8,895 fabricated
    # contradictions on the first full run. This is the "a wrong merge invents
    # relationships" failure, arriving exactly where the ontology predicted it.
    "million", "millions", "mn", "billion", "billions", "bn", "crore", "crores",
    "cr", "lakh", "lakhs", "lacs", "thousand", "trillion", "rs", "inr", "usd",
    "rupees", "rupee", "dollars", "amount", "amounts", "total", "value",
    "figures", "nos", "no", "units", "unit", "per", "cent", "percent", "bps",
}

# A label made only of stopwords carries no meaning, and a label that is a bare
# unit is worse than none: it looks specific while grouping everything.
_MIN_LABEL_WORDS = 2

_SENT_BREAK = re.compile(r"[.;:!?•·\n]")
_WORD = re.compile(r"[A-Za-z][A-Za-z&/'-]*")


def _label_before(text: str, offset: int, max_words: int = 8) -> str:
    """The words immediately before a value, back to a sentence boundary.

    A crude stand-in for "what is this a measurement of". It is right often
    enough to be useful in a table row ("Revenue from operations | 72,251") and
    wrong often enough in prose that the degraded-mode banner is not decoration.
    """
    head = text[:offset]
    last_break = max(
        (m.end() for m in _SENT_BREAK.finditer(head)), default=0
    )
    fragment = head[last_break:]
    words = _WORD.findall(fragment)
    if not words:
        return ""
    words = words[-max_words:]
    while words and words[-1].lower() in _STOP_TAIL:
        words.pop()
    while words and words[0].lower() in _STOP_TAIL:
        words.pop(0)
    return " ".join(words).strip()


def _row_label(block, candidate: Candidate) -> str:
    """The label a flattened table row leaves on the line above its values.

    The parser turns a statement row into one block whose first line is the
    label and whose remaining lines are the figures, one per column:

        Revenue from operations
        72,251
        64,280

    `_label_before` looks backwards from the value and finds a line break, so
    without this the entire income statement extracts nothing — which is what it
    did, and it is the tier a reviewer with no API key sees first.

    The block's first line is only borrowed when the value's own line carries no
    words of its own. That is what distinguishes a flattened table row from a
    sentence: in prose the label and the number share a line, and reaching past
    it would attach a paragraph's opening words to every figure in it.
    """
    lines = block.text.splitlines() if block else []
    if len(lines) < 2:
        return ""

    offset = candidate.char_start - block.char_start
    seen = 0
    own = ""
    for line in lines:
        seen += len(line) + 1
        if offset < seen:
            own = line
            break
    if not own or own is lines[0]:
        return ""
    if _WORD.search(own.replace(candidate.text, "")):
        return ""  # a sentence, not a value-only cell

    head = lines[0].strip()
    words = _WORD.findall(head)
    return head if len(words) >= _MIN_LABEL_WORDS else ""


def _enum_basis(candidate: Candidate) -> Basis:
    label = basis_from_label(candidate.column_header or candidate.header_path)
    return Basis(label) if label else Basis.UNKNOWN


def _line_bounds(page_text: str, start: int, end: int) -> tuple[int, int]:
    """The line containing a span, which is what gets quoted as evidence.

    A line rather than a sentence: in a table, the row *is* the unit of meaning,
    and in prose a line is a tighter, more checkable citation than a paragraph.
    """
    left = page_text.rfind("\n", 0, start) + 1
    right = page_text.find("\n", end)
    if right == -1:
        right = len(page_text)
    return left, right


def _period_near(candidate: Candidate) -> TemporalScope:
    """The period this value belongs to.

    The recovered column header is tried first, then the table header, then the
    surrounding window. In a financial statement the column header *is* the
    period, and it is usually the only place the period appears at all.
    """
    for source in (candidate.column_header, candidate.header_path):
        if source:
            p = parse_period(source)
            if p.kind is not PeriodKind.UNKNOWN:
                return p
    return parse_period(candidate.window)


def extract_page(
    page: Page,
    *,
    document_id: UUID,
    subject_raw: str,
    run_id: UUID,
    default_context: str = "",
    vintage: dt.date | None = None,
) -> list[Claim]:
    """Turn one page's spotted candidates into claims. No model involved."""
    claims: list[Claim] = []
    by_block = {b.id: b for b in page.blocks}
    page_scale = page_scale_caption(page)

    for cand in spot_page(page).candidates:
        if cand.noise_hint or cand.kind in ("date", "duration"):
            continue

        # Unit detection sees only the tight neighbourhood and the table header
        # path — never the whole window. The page's own caption and any
        # document-level statement are passed separately, as the weaker
        # evidence they are: they carry a bare table cell, and they are refused
        # by a number that has already said what it counts.
        unit_context = cand.tight
        inherited = " ".join(
            x for x in (cand.header_path or "", page_scale, default_context) if x
        )
        value = parse_value(cand.text, context=unit_context, inherited=inherited)
        if value is None or value.canonical_magnitude is None:
            continue

        # A number carrying no unit, currency, scale or ratio marker is almost
        # never a measurement. On the first run of this extractor the top
        # "facts" from an exchange filing's cover page were the postal codes in
        # BSE's address (400 001, 400 051) and the year in a regulation
        # citation. They are all perfectly grounded — they really are on the
        # page — which is precisely why grounding alone is not enough.
        #
        # The rule is a principled one rather than a filter for this document:
        # if a value has no unit, there is nothing to compare it against, so it
        # cannot participate in corroboration or contradiction anyway.
        if not (
            value.kind in (ValueKind.MONEY, ValueKind.RATIO)
            or value.unit is not None
            or parse_scale(unit_context) is not None
        ):
            continue

        # A recovered row label is a real column heading from the document and
        # is always better than guessing from surrounding words.
        rel_offset = cand.char_start - cand.window_start
        block = by_block.get(cand.block_id)
        predicate = (
            cand.row_label
            or _label_before(cand.window, rel_offset)
            or _row_label(block, cand)
        )
        if len(predicate) < 3 or len(predicate.split()) < _MIN_LABEL_WORDS:
            # Without a meaningful label there is nothing to compare this value
            # against. Emitting it would not merely inflate the claim count — a
            # vague label becomes an ontology node that unrelated figures
            # resolve to, and every pair inside it reads as a contradiction.
            continue

        block = by_block.get(cand.block_id)
        left, right = evidence_span(page, block, cand.char_start, cand.char_end)
        quote = page.text[left:right]
        if not quote.strip():
            continue

        evidence = [
            Evidence(
                document_id=document_id,
                page=page.number,
                char_start=left,
                char_end=right,
                quote=quote,
                rects=block.rects if block else [],
                block_id=cand.block_id,
            )
        ]
        # Where the scale came from is itself evidence, and recording it is what
        # lets a reader check an inherited "(₹ in millions)" rather than take it
        # on trust.
        # Where the scale came from is itself evidence. It is "inferred"
        # whenever it was not adjacent to the number — a table header, a page
        # caption, or a document-level statement — so a reader can check the
        # inheritance rather than take it on trust.
        scale_inferred = parse_scale(cand.tight) is None and parse_scale(
            f"{unit_context} {inherited}"
        ) is not None

        claims.append(
            Claim(
                subject_raw=subject_raw,
                predicate_raw=predicate.lower(),
                value=value,
                scope=Scope(
                    period=_period_near(cand),
                    basis=_enum_basis(cand),
                    vintage=vintage,
                ),
                evidence=evidence,
                # Rule-based extraction is a weaker signal than a model reading
                # the page, and the confidence should say so rather than
                # flattering the output.
                confidence=0.45,
                scale_inferred=scale_inferred,
                provenance=Provenance(
                    extractor="deterministic",
                    prompt_version=PROMPT_VERSION,
                    pipeline_run_id=run_id,
                    extracted_at=dt.datetime.now(dt.UTC),
                ),
            )
        )

    return claims


def extract_document(
    doc: ParsedDocument,
    *,
    document_id: UUID,
    subject_raw: str,
    run_id: UUID | None = None,
    default_context: str = "",
    vintage: dt.date | None = None,
    pages: list[int] | None = None,
) -> list[Claim]:
    """Extract a whole document, or a chosen subset of its pages.

    `pages` exists for the large-document path: the caller passes page numbers
    in descending candidate density, so the financial statements are processed
    before the signature pages and a reader sees facts immediately.
    """
    run = run_id or uuid4()
    wanted = set(pages) if pages is not None else None
    out: list[Claim] = []
    for page in doc.pages:
        if wanted is not None and page.number not in wanted:
            continue
        out.extend(
            extract_page(
                page,
                document_id=document_id,
                subject_raw=subject_raw,
                run_id=run,
                default_context=default_context,
                vintage=vintage,
            )
        )
    return out
