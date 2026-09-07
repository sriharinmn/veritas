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

Candidates are batched, which matters more than it sounds. Measured on this
hardware, one candidate per call is about three seconds; a thousand-candidate
document would take fifty minutes. Batching twelve at a time turns that into
single-digit minutes, and the model does a *better* job on a batch because
neighbouring candidates share their context.
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
BATCH_SIZE = 12

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
                "required": [
                    "id", "is_measurement", "subject", "predicate",
                    "period", "basis", "segment", "modality",
                ],
                "properties": {
                    "id": {"type": "integer"},
                    "is_measurement": {"type": "boolean"},
                    "subject": {"type": ["string", "null"]},
                    "predicate": {"type": ["string", "null"]},
                    "period": {"type": ["string", "null"]},
                    "basis": {
                        "type": ["string", "null"],
                        "enum": ["consolidated", "standalone", "segment", None],
                    },
                    "segment": {"type": ["string", "null"]},
                    "modality": {
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

For each numbered candidate you are given the value exactly as it appears, the \
text immediately around it, and the table column headers it sits under if any. \
Decide what the value measures.

Rules:
- `is_measurement` is false for identifiers, page and note references, postal \
codes, phone numbers, registration numbers, dates used as dates, and any figure \
that is not a measurement of something. Be strict: a number nobody would compare \
against another number is not a measurement.
- `predicate` is what is being measured, in the document's own words, without \
units, scales or currencies. Write "revenue from operations", never "revenue in \
millions". If you cannot tell, use null rather than guessing.
- `subject` is the entity the measurement is about. Resolve "the Company", "the \
Group" and "we" to the entity named in the document context. If the value is \
about a different entity, name that one.
- `period` is the period the value covers, copied in the document's own notation \
("FY24", "Q4FY24", "year ended March 31, 2024", "as at March 31, 2024"). Prefer \
the table column header when there is one. Use null if genuinely absent.
- `basis` is consolidated or standalone only when the document says so nearby or \
in the document context.
- `segment` is a named business segment when the value is segment-level, else null.
- `modality` is how strongly the figure is asserted: reported, restated, \
estimated, provisional, projected, or guidance.

Return one entry per candidate id. Do not add, merge or omit candidates."""

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


def _render_batch(batch: list[Candidate]) -> str:
    lines = []
    for i, c in enumerate(batch):
        parts = [f"[{i}] value as written: {c.text!r}"]
        if c.header_path:
            parts.append(f"    table columns: {c.header_path}")
        parts.append(f"    page {c.page}, context: ...{c.window.strip()[:600]}...")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


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
                max_tokens=180 * len(batch),
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
    idx = item.get("id")
    if not isinstance(idx, int) or not (0 <= idx < len(batch)):
        return None
    if not item.get("is_measurement"):
        return None

    cand = batch[idx]
    predicate = _clean(item.get("predicate"))
    if not predicate:
        return None

    subject = _clean(item.get("subject")) or context.entity
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

    period_text = _clean(item.get("period")) or cand.header_path or cand.window
    return Claim(
        subject_raw=subject,
        predicate_raw=predicate.lower(),
        value=value,
        scope=Scope(
            period=parse_period(period_text),
            basis=_enum(Basis, item.get("basis"), context.default_basis),
            segment=_clean(item.get("segment")),
            accounting=context.accounting,
            modality=_enum(Modality, item.get("modality"), Modality.UNKNOWN),
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
