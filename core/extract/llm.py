"""Model-backed extraction.

The single most important property of this module is what the model is *not*
allowed to produce.

It never writes a number. It never writes a quote. It never chooses a page or a
character offset. The spot sweep has already located every candidate exactly,
with its span and its bounding box, and the model's entire job is to say what
that candidate *means*: what it measures, what it is about, and over what
period, basis and modality. The value and the citation come from the document,
mechanically, and are stapled on afterwards.

That is not a stylistic preference. A model that emits values can hallucinate a
figure and attach a plausible citation, which is the failure the grounding
verifier exists to catch. A model that only labels candidates cannot produce
that failure at all — the number in a claim is, by construction, a substring of
the page it cites. The verifier stays as a hard gate regardless, because
defence in depth is cheap here, but the architecture removes the whole class
rather than filtering it afterwards.

**Throughput, measured rather than assumed.** A sweep on the RTX 4060 showed
where the time actually goes, and it was not where I expected:

    batch=12 window=600   31.5s   prefill 1,893 tok/s   generation 40.5 tok/s
    batch=12 window=200   19.1s   prefill 2,618 tok/s   generation 42.8 tok/s
    batch=24 window=0     42.7s   prefill 4,217 tok/s   generation 41.4 tok/s
    batch=40 window=0     61.7s   prefill 4,677 tok/s   generation 39.0 tok/s

Prefill is two orders of magnitude faster than generation, so prompt size is
nearly free and **output tokens per candidate is the only lever that matters**.
Larger batches barely help (0.56 → 0.65 candidates/second) because generated
tokens scale linearly with candidates.

Hence the schema: single-letter field names, and non-measurements omitted
entirely rather than echoed with a `false` flag. That took 72 output tokens per
candidate down to 49, which is a real 20% and is most of what is available
without changing model.

The remaining ~1.2 seconds per candidate is a hardware fact, not a bug, and the
design already accounts for it. Pages are processed in descending candidate
density and streamed, so a reader sees real facts within seconds of uploading
whatever the document's size; and the shipped snapshot is built by an overnight
run on a laptop that is running anyway. Optimising further would buy hours the
project does not need at the cost of quality it does.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import UUID, uuid4

import structlog

from core.extract.gateway import Gateway, LLMUnavailable
from core.extract.spot import Candidate, spot_page
from core.models import (
    Accounting,
    Basis,
    Claim,
    Evidence,
    Modality,
    Provenance,
    Scope,
)
from core.normalize.numbers import parse_value
from core.normalize.periods import parse_period
from core.parse.pdf import Page, ParsedDocument

log = structlog.get_logger(__name__)

PROMPT_VERSION = "extract-v1"
BATCH_SIZE = 24

# Authored to Groq strict-mode rules — every field required, no additional
# properties, optionality as a nullable union — because strict is the most
# restrictive target and relaxing later is easy while tightening later means
# rewriting every schema mid-sprint. Ollama accepts the same shape.
CANDIDATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                # Field names are single letters and non-measurements are omitted
                # entirely rather than echoed with a false flag. Both choices are
                # about generation cost, which the benchmark showed is the whole
                # bottleneck: prefill runs at 1,900-4,700 tok/s while generation
                # runs at ~40, so the only lever that matters is output tokens
                # per candidate. Measured 72 → 49 tokens per candidate.
                "required": ["i", "p", "s", "t", "b", "g", "m"],
                "properties": {
                    "i": {"type": "integer"},  # candidate id
                    "p": {"type": "string"},  # predicate
                    "s": {"type": ["string", "null"]},  # subject, if not the doc entity
                    "t": {"type": ["string", "null"]},  # period, document's notation
                    "b": {
                        "type": ["string", "null"],
                        "enum": ["consolidated", "standalone", "segment", None],
                    },
                    "g": {"type": ["string", "null"]},  # segment
                    "m": {
                        "type": ["string", "null"],
                        "enum": [
                            "reported", "restated", "estimated", "provisional",
                            "projected", "guidance", None,
                        ],
                    },
                },
            },
        }
    },
}

DOC_CONTEXT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "entity", "document_type", "published_on", "reporting_currency",
        "reporting_scale", "default_basis", "accounting_standard",
    ],
    "properties": {
        "entity": {"type": ["string", "null"]},
        "document_type": {"type": ["string", "null"]},
        "published_on": {"type": ["string", "null"]},
        "reporting_currency": {"type": ["string", "null"]},
        "reporting_scale": {"type": ["string", "null"]},
        "default_basis": {
            "type": ["string", "null"],
            "enum": ["consolidated", "standalone", None],
        },
        "accounting_standard": {"type": ["string", "null"]},
    },
}

EXTRACT_SYSTEM = """\
You label numbers that have already been found in a financial document. You do \
not find them, and you never write a number yourself.

For each numbered candidate you are given the value exactly as it appears and \
the text around it, with THAT OCCURRENCE marked as «value». Other numbers may \
appear in the same context; answer only about the marked one. You are also given \
the table column headers it sits under, if any.

OMIT any candidate that is not a measurement. Returning fewer, correct entries \
is much better than returning one for every candidate. Omit, do not label:
  · standard and regulation numbers — "ISO «27001»", "Regulation «30»", \
"Ind AS «115»", "Section «135»"
  · version and clause numbers — "version «1.9»", "clause «4.2»"
  · years, months and dates used as dates — "«2015»", "March «2024»"
  · identifiers — page and note references, postal codes, phone, CIN, GST, PAN \
and registration numbers, S. No. and serial columns
  · counts of pages, items or paragraphs in the document itself

The test: would a financial analyst put this number in a table next to the same \
number from another document and compare them? If not, omit it. If you find \
yourself writing a predicate like "year", "month", "version", "regulation", \
"standard" or "number", that candidate should have been omitted.

Fields are single letters because every output token costs generation time:
  i  the candidate id you were given
  p  what is measured, in the document's own words, WITHOUT units, scales or \
currencies. Write "revenue from operations", never "revenue in millions".
  s  the entity, only if it differs from the document entity; otherwise null. \
Resolve "the Company", "the Group" and "we" to the document entity.
  t  the period in the document's own notation ("FY24", "Q4FY24", "year ended \
March 31, 2024", "as at March 31, 2024"). Prefer the table column header when \
there is one. null if genuinely absent.
  b  consolidated or standalone, only when the document says so; else null
  g  a named business segment when the value is segment-level; else null
  m  reported, restated, estimated, provisional, projected or guidance

Never invent an id. Never return an id twice."""

DOC_CONTEXT_SYSTEM = """\
You read the front matter of a financial document and report its conventions.

These are the settings the rest of the document inherits. `reporting_scale` is \
the multiplier stated in captions such as "(₹ in millions)" — report the word \
itself ("millions", "crore", "lakhs"), or null if the document does not say. \
`published_on` should be an ISO date if one is stated. Use null anywhere the \
document does not tell you; do not infer."""


@dataclass
class DocumentContext:
    """Conventions the whole document inherits.

    Financial filings state the scale once, in a caption, and then never again.
    A figure extracted fifty pages later has to inherit it, and where that
    inheritance came from is itself evidence a reader can check.
    """

    entity: str | None = None
    document_type: str | None = None
    published_on: dt.date | None = None
    reporting_currency: str | None = None
    reporting_scale: str | None = None
    default_basis: Basis = Basis.UNKNOWN
    accounting: Accounting = Accounting.UNKNOWN
    source_quote: str = ""

    def as_unit_context(self) -> str:
        return " ".join(x for x in (self.reporting_currency, self.reporting_scale) if x)


async def read_document_context(
    doc: ParsedDocument, gateway: Gateway, *, pages: int = 4
) -> DocumentContext:
    """One pass over the front matter, before any extraction."""
    head = "\n\n".join(p.text[:2500] for p in doc.pages[:pages])
    if not head.strip():
        return DocumentContext()

    try:
        resp = await gateway.complete_json(
            system=DOC_CONTEXT_SYSTEM,
            user=f"Document: {doc.filename}\n\n{head}",
            schema=DOC_CONTEXT_SCHEMA,
            max_tokens=400,
        )
    except LLMUnavailable:
        return DocumentContext()

    d = resp.parsed or {}
    return DocumentContext(
        entity=_clean(d.get("entity")),
        document_type=_clean(d.get("document_type")),
        published_on=_parse_iso(d.get("published_on")),
        reporting_currency=_clean(d.get("reporting_currency")),
        reporting_scale=_clean(d.get("reporting_scale")),
        default_basis=_enum(Basis, d.get("default_basis"), Basis.UNKNOWN),
        accounting=_accounting(d.get("accounting_standard")),
        source_quote=head[:400],
    )


CONTEXT_CHARS = 400


def _marked(c: Candidate) -> str:
    """The candidate's context with the exact occurrence marked in place.

    This matters far more than it looks. Inside a financial table every
    candidate's surrounding window is nearly identical — the same rows, the same
    header, the same column labels — so a batch of sixteen cells arrives at the
    model looking like sixteen copies of one question. It answers plausibly and
    attaches the answers to the wrong candidates: on the FY24 annual report's
    page 55 the predicate "percentage coverage" came back bound to 100, 27,001,
    84 and 45 in the same batch.

    Marking the exact character range removes the ambiguity entirely. The model
    is no longer asked "what does 27,001 mean somewhere in this table" but "what
    does THIS 27,001 mean", which is a question with one answer.
    """
    rel = c.char_start - c.window_start
    if not (0 <= rel <= len(c.window) - len(c.text)):
        return c.window.strip()[:CONTEXT_CHARS]
    marked = f"{c.window[:rel]}«{c.text}»{c.window[rel + len(c.text):]}"

    # Inside a table the row is the unit of meaning, and the rest of the table is
    # noise that makes every candidate look alike. Outside one, a couple of
    # sentences of prose is what carries the meaning.
    if c.block_kind == "table":
        left = marked.rfind("\n", 0, marked.index("«"))
        right = marked.find("\n", marked.index("»"))
        row = marked[left + 1 if left != -1 else 0 : right if right != -1 else len(marked)]
        if row.strip():
            return row.strip()[:CONTEXT_CHARS]
    return marked.strip()[:CONTEXT_CHARS]


def _render_batch(batch: list[Candidate]) -> str:
    """Prompt tokens are nearly free — prefill runs ~50x faster than generation —
    so context is sized for the model's benefit rather than to save budget."""
    lines = []
    for i, c in enumerate(batch):
        parts = [f"[{i}] value «{c.text}» on page {c.page}"]
        if c.header_path:
            parts.append(f"    cols: {c.header_path[:160]}")
        parts.append(f"    ctx: {_marked(c)}")
        lines.append("\n".join(parts))
    return "\n".join(lines)


async def extract_page(
    page: Page,
    gateway: Gateway,
    *,
    document_id: UUID,
    context: DocumentContext,
    run_id: UUID,
    batch_size: int = BATCH_SIZE,
) -> list[Claim]:
    """Label one page's candidates and assemble grounded claims."""
    candidates = [c for c in spot_page(page).candidates if c.kind not in ("date", "duration")]
    if not candidates:
        return []

    claims: list[Claim] = []
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        try:
            resp = await gateway.complete_json(
                system=EXTRACT_SYSTEM,
                user=(
                    f"Document context: entity={context.entity!r}, "
                    f"type={context.document_type!r}, currency={context.reporting_currency!r}, "
                    f"scale={context.reporting_scale!r}, basis={context.default_basis.value}\n\n"
                    f"Candidates:\n\n{_render_batch(batch)}"
                ),
                schema=CANDIDATE_SCHEMA,
                max_tokens=70 * len(batch),
            )
        except LLMUnavailable as e:
            log.warning("extract.batch_failed", page=page.number, error=str(e)[:120])
            continue

        for item in (resp.parsed or {}).get("items", []):
            claim = _assemble(item, batch, page, document_id, context, run_id, resp.model)
            if claim is not None:
                claims.append(claim)

    return claims


def _assemble(
    item: dict,
    batch: list[Candidate],
    page: Page,
    document_id: UUID,
    context: DocumentContext,
    run_id: UUID,
    model: str,
) -> Claim | None:
    idx = item.get("i")
    if not isinstance(idx, int) or not (0 <= idx < len(batch)):
        return None

    cand = batch[idx]
    predicate = _clean(item.get("p"))
    if not predicate:
        return None

    subject = _clean(item.get("s")) or context.entity
    if not subject:
        return None

    # The value is parsed from the document, never from the model's output.
    unit_context = " ".join(
        x for x in (cand.tight, cand.header_path or "", context.as_unit_context()) if x
    )
    value = parse_value(cand.text, context=unit_context)
    if value is None or value.canonical_magnitude is None:
        return None

    left = page.text.rfind("\n", 0, cand.char_start) + 1
    right = page.text.find("\n", cand.char_end)
    if right == -1:
        right = len(page.text)
    quote = page.text[left:right]
    if not quote.strip():
        return None

    block = next((b for b in page.blocks if b.id == cand.block_id), None)
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

    # Where an inherited scale came from is itself evidence, recorded so a reader
    # can check the "(₹ in millions)" caption rather than take it on trust.
    scale_inferred = False
    if context.reporting_scale and context.reporting_scale not in quote:
        from core.normalize.scale import parse_scale

        if parse_scale(cand.tight) is None and parse_scale(cand.header_path or "") is None:
            scale_inferred = True
            if context.source_quote:
                evidence.append(
                    Evidence(
                        document_id=document_id,
                        page=1,
                        char_start=0,
                        char_end=min(len(page.text), 1),
                        quote=context.source_quote[:200],
                        kind="inherited_context",
                    )
                )

    period_text = _clean(item.get("t")) or cand.header_path or cand.window
    return Claim(
        subject_raw=subject,
        predicate_raw=predicate.lower(),
        value=value,
        scope=Scope(
            period=parse_period(period_text),
            basis=_enum(Basis, item.get("b"), context.default_basis),
            segment=_clean(item.get("g")),
            accounting=context.accounting,
            modality=_enum(Modality, item.get("m"), Modality.UNKNOWN),
            vintage=context.published_on,
        ),
        evidence=evidence,
        confidence=0.85 if not scale_inferred else 0.6,
        scale_inferred=scale_inferred,
        provenance=Provenance(
            extractor=model,
            prompt_version=PROMPT_VERSION,
            pipeline_run_id=run_id,
            extracted_at=dt.datetime.now(dt.UTC),
        ),
    )


# ── small helpers ────────────────────────────────────────────────────────────


def _clean(v: object) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s or s.lower() in {"null", "none", "n/a", "unknown", "-"}:
        return None
    return s


def _parse_iso(v: object) -> dt.date | None:
    s = _clean(v)
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _enum(cls, v: object, default):
    s = _clean(v)
    if not s:
        return default
    try:
        return cls(s.lower())
    except ValueError:
        return default


def _accounting(v: object) -> Accounting:
    s = (_clean(v) or "").upper().replace(" ", "").replace("-", "_")
    if "IND" in s:
        return Accounting.IND_AS
    if "IFRS" in s:
        return Accounting.IFRS
    if "GAAP" in s:
        return Accounting.US_GAAP
    return Accounting.UNKNOWN


async def extract_document(
    doc: ParsedDocument,
    gateway: Gateway,
    *,
    document_id: UUID | None = None,
    run_id: UUID | None = None,
    pages: list[int] | None = None,
    context: DocumentContext | None = None,
) -> tuple[list[Claim], DocumentContext]:
    """Extract a document, or a chosen subset of its pages in a chosen order.

    `pages` carries the large-document work order: page numbers in descending
    candidate density, so the financial statements are labelled before the
    signature pages and a reader sees real facts within seconds.
    """
    doc_id = document_id or uuid4()
    run = run_id or uuid4()
    ctx = context or await read_document_context(doc, gateway)

    by_number = {p.number: p for p in doc.pages}
    order = pages if pages is not None else [p.number for p in doc.pages]

    claims: list[Claim] = []
    for n in order:
        page = by_number.get(n)
        if page is None:
            continue
        claims.extend(
            await extract_page(
                page, gateway, document_id=doc_id, context=ctx, run_id=run
            )
        )
    return claims, ctx
