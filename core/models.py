"""The domain model.

The whole design is in this file. Everything else serves it.

A fact is not a sentence — it is a typed tuple with an explicit scope. Once
claims are normalised into `(subject, predicate, value, scope, evidence)`, the
three relations the assignment asks for become derivable rather than opinions:

    scopes identical + values agree     → CORROBORATION
    scopes identical + values disagree  → CONTRADICTION
    scopes differ on exactly one axis   → RECONCILED, and that axis is the reason

`Scope` is the object that makes this work. A "contradiction" in financial
documents is almost always a scope mismatch in disguise: a different period, a
different reporting basis, a restatement, a projection rather than an actual.
Model scope explicitly and the system explains itself for free.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


# ─── value ───────────────────────────────────────────────────────────────────


class ValueKind(StrEnum):
    MONEY = "money"
    QUANTITY = "quantity"
    RATIO = "ratio"
    DATE = "date"
    TEXT = "text"
    ENTITY = "entity"
    BOOL = "bool"


class RatioBasis(StrEnum):
    PERCENT = "percent"
    BPS = "bps"
    MULTIPLE = "multiple"  # "1.4x"
    RAW = "raw"


class TypedValue(Base):
    """A value that can be compared with another value.

    `canonical_magnitude` is the only field the comparator ever looks at for
    numeric kinds. It is always populated at normalisation time, in a fixed
    canonical unit per kind: base currency units for money (not crore, not
    million), SI base units for quantities, and a plain fraction for ratios
    (so 8.2% and 820 bps both canonicalise to 0.082).

    This is what makes "Rs. 72,251 mn" and "₹7,225 crore" the same fact.
    """

    kind: ValueKind

    # As written in the document, kept verbatim for evidence display.
    raw: str

    # Populated for money / quantity / ratio.
    canonical_magnitude: Decimal | None = None
    currency: str | None = None  # ISO-4217, money only
    unit: str | None = None  # canonical unit symbol, quantity only
    ratio_basis: RatioBasis | None = None

    # Populated for date / text / entity / bool.
    date_value: dt.date | None = None
    text_value: str | None = None
    bool_value: bool | None = None

    # A document may state a range ("6.3 to 6.8 per cent"). Containment is a
    # legitimate form of agreement and the comparator needs to know about it.
    range_low: Decimal | None = None
    range_high: Decimal | None = None

    @property
    def is_numeric(self) -> bool:
        return self.kind in (ValueKind.MONEY, ValueKind.QUANTITY, ValueKind.RATIO)

    @property
    def is_range(self) -> bool:
        return self.range_low is not None and self.range_high is not None


# ─── scope ───────────────────────────────────────────────────────────────────


class PeriodKind(StrEnum):
    INSTANT = "instant"  # "as at 31 March 2024"
    INTERVAL = "interval"  # "year ended 31 March 2024", "Q4 FY24", "9M FY25"
    UNKNOWN = "unknown"


class FiscalConvention(StrEnum):
    """Which calendar the label is expressed in.

    This axis exists because it is the single most common cause of an apparent
    contradiction in the macroeconomic corpus: the IMF reports India on calendar
    years while the Economic Survey and the RBI report on the April-March
    fiscal year. Two institutions can state different numbers for "2024" and
    both be right.
    """

    IN_APR_MAR = "IN_APR_MAR"
    CALENDAR = "CALENDAR"
    US_OCT_SEP = "US_OCT_SEP"
    UNKNOWN = "unknown"


class TemporalScope(Base):
    kind: PeriodKind = PeriodKind.UNKNOWN
    label: str | None = None  # "FY24", "Q4FY24", "9MFY25", as written
    start: dt.date | None = None
    end: dt.date | None = None
    convention: FiscalConvention = FiscalConvention.UNKNOWN

    def overlaps(self, other: TemporalScope) -> bool:
        if not (self.start and self.end and other.start and other.end):
            return False
        return self.start <= other.end and other.start <= self.end

    def same_as(self, other: TemporalScope) -> bool:
        """Identical in real time, regardless of how each was written.

        FY24 in an Indian filing and "year ended March 2024" in another document
        are the same period and must compare equal, which is precisely why the
        comparator works on resolved dates rather than on labels.
        """
        if self.start and self.end and other.start and other.end:
            return self.start == other.start and self.end == other.end
        return False


class Basis(StrEnum):
    CONSOLIDATED = "consolidated"
    STANDALONE = "standalone"
    SEGMENT = "segment"
    UNKNOWN = "unknown"


class Accounting(StrEnum):
    IND_AS = "IND_AS"
    IFRS = "IFRS"
    US_GAAP = "US_GAAP"
    UNKNOWN = "unknown"


class Modality(StrEnum):
    """How strongly the document asserts the value.

    A projection disagreeing with an actual is not a contradiction, and this is
    the axis that says so.
    """

    REPORTED = "reported"
    RESTATED = "restated"
    ESTIMATED = "estimated"
    PROVISIONAL = "provisional"
    PROJECTED = "projected"
    GUIDANCE = "guidance"
    UNKNOWN = "unknown"


SCOPE_AXES = ("period", "basis", "segment", "geography", "accounting", "modality")


class Scope(Base):
    period: TemporalScope = Field(default_factory=TemporalScope)
    basis: Basis = Basis.UNKNOWN
    segment: str | None = None
    geography: str | None = None
    accounting: Accounting = Accounting.UNKNOWN
    modality: Modality = Modality.UNKNOWN

    # When the asserting document was published. Not a comparison axis in the
    # same sense as the others — it is the tie-breaker that lets a later
    # restatement supersede an earlier figure rather than merely contradict it.
    vintage: dt.date | None = None

    def differing_axes(self, other: Scope) -> list[str]:
        """Which axes materially differ. UNKNOWN never counts as a difference.

        Treating UNKNOWN as a difference would make every under-specified claim
        incomparable, which would quietly destroy recall. Treating it as a match
        risks a false corroboration. We take the second risk deliberately and
        carry it into the confidence score, because the alternative silently
        drops facts and this way the uncertainty stays visible.
        """
        diffs: list[str] = []

        if not self.period.same_as(other.period) and _both_known_periods(self.period, other.period):
            diffs.append("period")
        if _both_known(self.basis, other.basis, Basis.UNKNOWN) and self.basis != other.basis:
            diffs.append("basis")
        if self.segment and other.segment and self.segment != other.segment:
            diffs.append("segment")
        if self.geography and other.geography and self.geography != other.geography:
            diffs.append("geography")
        if (
            _both_known(self.accounting, other.accounting, Accounting.UNKNOWN)
            and self.accounting != other.accounting
        ):
            diffs.append("accounting")
        if (
            _both_known(self.modality, other.modality, Modality.UNKNOWN)
            and self.modality != other.modality
        ):
            diffs.append("modality")

        return diffs


def _both_known(a: object, b: object, unknown: object) -> bool:
    return a != unknown and b != unknown


def _both_known_periods(a: TemporalScope, b: TemporalScope) -> bool:
    return bool(a.start and a.end and b.start and b.end)


# ─── evidence ────────────────────────────────────────────────────────────────


class Rect(Base):
    """Normalised to 0..1 against the page rectangle at extraction time.

    Normalised rather than absolute so the same box renders correctly at any
    zoom level in PDF.js without the frontend needing to know the page size.
    """

    x0: float
    y0: float
    x1: float
    y1: float


class Evidence(Base):
    document_id: UUID
    page: int  # 1-indexed physical page in the PDF
    char_start: int
    char_end: int
    quote: str  # verbatim from the page text
    rects: list[Rect] = Field(default_factory=list)
    block_id: UUID | None = None

    # Set when the claim's scale or currency was not in the quote itself but
    # inherited from a table header or document-level statement. That inherited
    # context is itself evidence, and is recorded as a second Evidence row.
    kind: Literal["primary", "inherited_context"] = "primary"


# ─── claim ───────────────────────────────────────────────────────────────────


class Provenance(Base):
    extractor: str  # "ollama:qwen3:8b" / "groq:openai/gpt-oss-120b" / "deterministic"
    prompt_version: str
    pipeline_run_id: UUID
    extracted_at: dt.datetime


class Claim(Base):
    id: UUID = Field(default_factory=uuid4)
    subject_id: UUID | None = None  # canonical entity node
    predicate_id: UUID | None = None  # node in the evolving ontology

    # As extracted, before canonicalisation. Kept so the ontology's merge
    # decisions stay auditable against what the document actually said.
    subject_raw: str
    predicate_raw: str

    value: TypedValue
    scope: Scope
    evidence: list[Evidence]
    confidence: float = 1.0
    provenance: Provenance | None = None

    # Set when the scale or currency was inherited across a page break rather
    # than stated locally. Surfaced in the UI, and reported as its own failure
    # rate in the evals rather than averaged away.
    scale_inferred: bool = False


class Relation(StrEnum):
    CORROBORATION = "corroboration"
    CONTRADICTION = "contradiction"
    RECONCILED = "reconciled"
    UNRELATED = "unrelated"
    AMBIGUOUS = "ambiguous"  # escalates to the LLM adjudicator


class Verdict(Base):
    """The comparator's output, carrying the reasoning that produced it.

    `trace` exists so the UI can show the machine's steps rather than only an
    LLM's paragraph. "Explained" in the brief means the reader can follow the
    decision, not merely read a fluent summary of it.
    """

    relation: Relation
    axis: str | None = None  # the scope axis that explains a RECONCILED verdict
    confidence: float
    trace: list[str] = Field(default_factory=list)
    decided_by: Literal["deterministic", "llm"] = "deterministic"
    explanation: str | None = None  # prose, generated on top of the trace
