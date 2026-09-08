"""Facts that are not numbers.

The brief asks for "meaningful numerical **or semantic** facts", and its own
examples are pointedly not numeric: a director active in one document and
resigned in a later one; two differently written addresses that refer to the
same place. Until now every claim in this system came from the numeric spot
sweep — 44.8% money, 32.4% quantity, 22.8% ratio, and nothing else at all.

The hard part is not prompting for them. It is keeping the guarantee that makes
the numeric path trustworthy: **the model never writes the value.** A candidate
numeral is located by regex first and the model only labels it, so a fabricated
figure is impossible by construction rather than filtered afterwards.

There is no regex that finds "resigned with effect from 12 May 2023" the way
there is one that finds 7,225. So the guarantee is preserved differently: the
model must return the value as a **verbatim substring of the block it was shown**,
and this module then *locates that substring itself*. If the returned text is
not present in the block, character for character, the fact is discarded before
it becomes a claim. The model is still not trusted to write a value — it is
trusted only to point at one, and the pointing is checked.

That check does real work. Models paraphrase constantly: asked for a status they
return "resigned" where the page says "ceased to be a Director". Paraphrase is
the semantic equivalent of a hallucinated digit, and it is rejected the same way.

Blocks are filtered before the model sees them. A table of numbers has no
semantic facts worth the tokens, and a page of financial statements would burn
the whole budget returning nothing.
"""

from __future__ import annotations

import datetime as dt
import re
from uuid import UUID

import structlog

from core.extract.gateway import Gateway, LLMUnavailable, RateLimited
from core.models import (
    Claim,
    Evidence,
    Modality,
    Provenance,
    Scope,
    TypedValue,
    ValueKind,
)
from core.normalize.periods import parse_period
from core.parse.pdf import Block, Page

log = structlog.get_logger(__name__)

SEMANTIC_PROMPT_VERSION = "semantic-v1"

# Blocks shorter than this rarely carry a statement; blocks longer than this are
# usually a whole page of prose flattened together, where a single claim's
# evidence span stops being useful to a reader.
MIN_BLOCK_CHARS = 60
MAX_BLOCK_CHARS = 2000
MAX_BLOCKS_PER_PAGE = 8
MAX_FACTS_PER_BLOCK = 4

# Generous, because a reasoning model spends part of this budget thinking before
# it writes anything. Sized at 60 tokens per fact left nothing for the reasoning
# and the model returned empty content, which Groq then rejects as invalid JSON
# — a starved call that looks exactly like an outage. Four facts of prose is
# perhaps 250 visible tokens; the rest is headroom deliberately.
MAX_OUTPUT_TOKENS = 900

# A block this dense in digits is a table. Semantic facts live in sentences.
MAX_DIGIT_RATIO = 0.16

_KINDS = {"text", "entity", "bool", "date"}

SEMANTIC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["facts"],
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["s", "p", "v", "k"],
                "properties": {
                    # Subject and predicate are labels: the model may phrase
                    # these, because canonicalisation reconciles them later and
                    # neither is presented as a quotation from the page.
                    "s": {"type": "string"},
                    "p": {"type": "string"},
                    # The value is a quotation. It is checked.
                    "v": {"type": "string"},
                    "k": {"type": "string", "enum": sorted(_KINDS)},
                },
            },
        }
    },
}

SEMANTIC_SYSTEM = """\
You extract non-numeric facts from a passage of a financial or official document.

A fact is something the passage asserts about a named thing: a person's role or
status, an entity's name or address, an auditor, a registrar, a stock exchange
listing, an approval, a resignation, an appointment, a policy or a standard the
document says it follows.

Ignore anything whose value is a number, a percentage or an amount. Those are
handled elsewhere. A date is allowed only when the date itself is the fact,
as in a date of appointment or incorporation.

For each fact return four fields:

  s  the subject — who or what the fact is about
  p  the predicate — which property of it, in lowercase words
  v  the value, COPIED EXACTLY from the passage, character for character
  k  one of: text, entity, bool, date

The single rule that matters: **v must be a verbatim substring of the passage.**
Do not paraphrase it, do not tidy it, do not expand an abbreviation, do not
change its capitalisation. If the passage says "ceased to be a Director", then v
is "ceased to be a Director" and not "resigned". A value that is not present in
the passage exactly as written will be discarded, and the fact lost with it.

Keep v short — the shortest span that carries the fact, usually two to eight
words. Return an empty list if the passage asserts nothing of this kind, which
is the common case and is a perfectly good answer.
"""


def _digit_ratio(text: str) -> float:
    if not text:
        return 1.0
    return sum(1 for ch in text if ch.isdigit()) / len(text)


def prose_blocks(page: Page) -> list[Block]:
    """The blocks on a page worth spending a semantic call on.

    Filtering here rather than in the prompt is deliberate: a financial
    statement page is a hundred table rows, and sending them costs the whole
    token budget to be told, correctly, that there is nothing to report.
    """
    out = []
    for block in page.blocks:
        text = block.text.strip()
        if not (MIN_BLOCK_CHARS <= len(text) <= MAX_BLOCK_CHARS):
            continue
        if block.kind == "table" or _digit_ratio(text) > MAX_DIGIT_RATIO:
            continue
        # A block with no lowercase run is a heading or a column of labels.
        if not re.search(r"[a-z]{3,}\s+[a-z]{3,}", text):
            continue
        out.append(block)
    return out[:MAX_BLOCKS_PER_PAGE]


def _locate(value: str, block: Block) -> tuple[int, int] | None:
    """Find the model's value inside the block, or refuse the fact.

    Tries the exact string first, then a whitespace-tolerant match — PDF text
    carries line breaks mid-sentence, so a model reading "Company Secretary and
    Compliance Officer" may return it with a single space where the page has a
    newline. That is the same characters in the same order and is allowed.
    Anything looser is paraphrase, and paraphrase is rejected.
    """
    value = value.strip()
    if not value:
        return None

    index = block.text.find(value)
    if index != -1:
        return block.char_start + index, block.char_start + index + len(value)

    pattern = r"\s+".join(re.escape(part) for part in value.split())
    match = re.search(pattern, block.text)
    if match:
        return block.char_start + match.start(), block.char_start + match.end()
    return None


# A predicate names a property. A clause is not a property name, and the model
# produces clauses freely: "is recognised in the carrying", "are stated at cost,
# less accum". Those read as facts and are not — the sentence has simply been
# cut in half, with the front calling itself a predicate and the back calling
# itself a value.
MAX_PREDICATE_WORDS = 5
MAX_VALUE_WORDS = 12

# Boilerplate that every audit report contains, asserting nothing about this
# document in particular. Matched on the predicate, never the subject, so a
# genuine fact about an auditor still survives.
_BOILERPLATE = re.compile(
    r"\b(give a true and fair|in accordance with|conducted in|responsibility for|"
    r"basis for|we believe|referred to in|in our opinion)\b",
    re.IGNORECASE,
)


def _is_a_property(predicate: str, value: str) -> bool:
    """Does this read as (property, value), or as a sentence cut in half?"""
    words = predicate.split()
    if not (1 <= len(words) <= MAX_PREDICATE_WORDS):
        return False
    if len(value.split()) > MAX_VALUE_WORDS:
        return False
    if _BOILERPLATE.search(predicate):
        return False
    # "cost | includes | the cost of replacing part of the plant" — the value
    # continuing the predicate's own sentence is the tell.
    return not predicate.rstrip().endswith((" in", " of", " to", " at", " on", " with", " the"))


def _typed(value: str, kind: str) -> TypedValue:
    if kind == "bool":
        truthy = value.strip().lower() in {"yes", "true", "active", "listed", "approved"}
        return TypedValue(kind=ValueKind.BOOL, raw=value, bool_value=truthy)
    if kind == "date":
        period = parse_period(value)
        return TypedValue(
            kind=ValueKind.DATE, raw=value, date_value=period.start, text_value=value
        )
    if kind == "entity":
        return TypedValue(kind=ValueKind.ENTITY, raw=value, text_value=value)
    return TypedValue(kind=ValueKind.TEXT, raw=value, text_value=value)


async def extract_semantic_page(
    page: Page,
    gateway: Gateway,
    *,
    document_id: UUID,
    run_id: UUID,
    entity_hint: str | None = None,
    vintage: dt.date | None = None,
) -> tuple[list[Claim], int]:
    """Non-numeric claims from one page. Returns (claims, facts_rejected).

    The rejection count is returned rather than logged away because it is the
    honest measure of how often the model paraphrased instead of quoting, and
    the evals should be able to report it.
    """
    blocks = prose_blocks(page)
    if not blocks:
        return [], 0

    claims: list[Claim] = []
    rejected = 0
    consecutive_failures = 0
    seen: set[tuple[str, str, str]] = set()

    for block in blocks:
        try:
            resp = await gateway.complete_json(
                system=SEMANTIC_SYSTEM,
                user=(
                    (f"Document is about: {entity_hint}\n\n" if entity_hint else "")
                    + f"Passage (page {page.number}):\n\n{block.text}"
                ),
                schema=SEMANTIC_SCHEMA,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
            consecutive_failures = 0
        except (LLMUnavailable, RateLimited) as e:
            # One block failing must not abandon the page. The first version
            # broke out of the loop here, and because a too-small token budget
            # makes a reasoning model return empty content — which surfaces as
            # LLMUnavailable — a single starved call silently discarded every
            # remaining block on the page. Two pages reported zero facts that
            # way while the third, reached first, found one.
            consecutive_failures += 1
            log.info("semantic.call_failed", page=page.number, error=str(e)[:120])
            if consecutive_failures >= 3:
                log.info("semantic.giving_up", page=page.number)
                break
            continue

        for item in (resp.parsed or {}).get("facts", [])[:MAX_FACTS_PER_BLOCK]:
            subject = (item.get("s") or "").strip()
            predicate = (item.get("p") or "").strip().lower()
            value = (item.get("v") or "").strip()
            kind = (item.get("k") or "text").strip().lower()

            if not (subject and predicate and value) or kind not in _KINDS:
                rejected += 1
                continue

            if not _is_a_property(predicate, value):
                rejected += 1
                continue

            # The same fact stated in two blocks of one page is one fact.
            fingerprint = (subject.lower(), predicate.lower(), " ".join(value.split()).lower())
            if fingerprint in seen:
                continue
            seen.add(fingerprint)

            span = _locate(value, block)
            if span is None:
                # The value is not on the page as written. This is the semantic
                # equivalent of a hallucinated figure and is treated the same.
                rejected += 1
                log.debug("semantic.not_verbatim", value=value[:60], page=page.number)
                continue

            start, end = span
            claims.append(
                Claim(
                    subject_raw=subject[:200],
                    predicate_raw=predicate[:200],
                    value=_typed(page.text[start:end], kind),
                    scope=Scope(
                        period=parse_period(""),
                        modality=Modality.REPORTED,
                        vintage=vintage,
                    ),
                    evidence=[
                        Evidence(
                            document_id=document_id,
                            page=page.number,
                            char_start=block.char_start,
                            char_end=block.char_end,
                            quote=block.text,
                            rects=block.rects,
                            block_id=block.id,
                        )
                    ],
                    provenance=Provenance(
                        extractor=f"{getattr(gateway, 'tier', '?')}:{gateway.model}",
                        prompt_version=SEMANTIC_PROMPT_VERSION,
                        pipeline_run_id=run_id,
                        extracted_at=dt.datetime.now(dt.UTC),
                    ),
                    confidence=0.9,
                )
            )

    if claims or rejected:
        log.info(
            "semantic.extracted",
            page=page.number,
            facts=len(claims),
            rejected=rejected,
            blocks=len(blocks),
        )
    return claims, rejected
