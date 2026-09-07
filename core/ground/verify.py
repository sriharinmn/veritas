"""The grounding verifier — the guardrail the rest of the system leans on.

A claim whose value is not literally present in the span it cites does not enter
the knowledge layer. It is written to quarantine with a reason code. There is no
"low confidence" escape hatch, because a confidence score is a number a reader
can talk themselves past and a hard gate is not.

This is about a hundred and fifty lines of code and it buys three things:

1. **A hallucination firewall.** A model that invents a plausible figure and
   attaches a plausible citation is the single most dangerous failure mode in a
   product a banker signs their name under. Here it is caught mechanically,
   without another model being asked for an opinion.

2. **A headline number.** `grounded / (grounded + quarantined)`, computed over
   every claim, reported per reason code. Not an estimate — a count.

3. **Real material for the assignment's fourth required case.** The quarantine
   queue fills with genuine failures, so the honest answer to "what went wrong"
   comes from data rather than from memory.

The verifier is deliberately dumb. Every check is a string or integer
comparison. Nothing here is probabilistic, so nothing here can be argued with.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from core.models import Claim, Evidence
from core.parse.pdf import Page


class QuarantineReason(StrEnum):
    NO_EVIDENCE = "no_evidence"
    PAGE_NOT_FOUND = "page_not_found"
    SPAN_OUT_OF_RANGE = "span_out_of_range"
    EMPTY_QUOTE = "empty_quote"
    QUOTE_MISMATCH = "quote_mismatch"
    VALUE_NOT_IN_QUOTE = "value_not_in_quote"


REASON_HELP: dict[QuarantineReason, str] = {
    QuarantineReason.NO_EVIDENCE: "The extractor produced a claim with no citation at all.",
    QuarantineReason.PAGE_NOT_FOUND: "The cited page does not exist in this document.",
    QuarantineReason.SPAN_OUT_OF_RANGE: "The cited character span falls outside the page text.",
    QuarantineReason.EMPTY_QUOTE: "The citation carries no quoted text.",
    QuarantineReason.QUOTE_MISMATCH: (
        "The quoted text does not match what is actually at that position on the page — "
        "the model paraphrased its source instead of quoting it."
    ),
    QuarantineReason.VALUE_NOT_IN_QUOTE: (
        "The claimed value does not appear in the text it cites. This is the signature "
        "of a hallucinated figure with a real-looking citation."
    ),
}

# Typographic separators that appear inside numbers in PDF text extraction and
# carry no meaning: thin spaces, non-breaking spaces, soft hyphens, zero-width
# joiners. Present in every one of the starter documents.
_INVISIBLE = dict.fromkeys(
    map(ord, "     ​‌‍⁠­﻿"), " "
)

_WS = re.compile(r"\s+")


def normalise_text(s: str) -> str:
    """Collapse the differences that are artefacts of PDF extraction, not content.

    Unicode NFKC folds the ligatures and full-width forms that appear in
    typeset financial documents; whitespace is collapsed because a line break
    inside a quoted sentence is a rendering detail, not a difference in what the
    document says.
    """
    s = unicodedata.normalize("NFKC", (s or "").translate(_INVISIBLE))
    return _WS.sub(" ", s).strip()


def _numeric_key(s: str) -> str:
    """Reduce a numeric token to what a reader would consider its identity.

    "72,251", "72 251" and "72251" are the same number written three ways, and a
    verifier that treats them as different would quarantine correct claims —
    which is worse than useless, because it would make the pass rate meaningless.
    """
    return re.sub(r"[,\s−–—]", "", normalise_text(s)).lstrip("+").rstrip(".")


@dataclass
class GroundingResult:
    ok: bool
    reason: QuarantineReason | None = None
    detail: str = ""

    @property
    def explanation(self) -> str:
        if self.ok:
            return "Grounded: the cited span exists and contains the claimed value."
        return f"{REASON_HELP.get(self.reason, '')} {self.detail}".strip()


def verify_evidence(ev: Evidence, pages: dict[int, Page]) -> GroundingResult:
    """Check one citation against the page it points at."""
    page = pages.get(ev.page)
    if page is None:
        return GroundingResult(
            False, QuarantineReason.PAGE_NOT_FOUND, f"Cited page {ev.page}."
        )

    if not (ev.quote or "").strip():
        return GroundingResult(False, QuarantineReason.EMPTY_QUOTE)

    if not (0 <= ev.char_start < ev.char_end <= len(page.text)):
        return GroundingResult(
            False,
            QuarantineReason.SPAN_OUT_OF_RANGE,
            f"Span [{ev.char_start}:{ev.char_end}] against a page of {len(page.text)} characters.",
        )

    actual = page.text[ev.char_start : ev.char_end]
    if normalise_text(actual) != normalise_text(ev.quote):
        return GroundingResult(
            False,
            QuarantineReason.QUOTE_MISMATCH,
            f"Page says {normalise_text(actual)[:80]!r}; the citation claims "
            f"{normalise_text(ev.quote)[:80]!r}.",
        )

    return GroundingResult(True)


def verify_claim(claim: Claim, pages: dict[int, Page]) -> GroundingResult:
    """Decide whether a claim may enter the knowledge layer.

    A claim needs at least one *primary* citation that survives verification and
    that actually contains the value being asserted. Inherited-context evidence —
    the table header or the "(₹ in millions)" caption a scale came from — is
    verified for position but is not expected to contain the value, because the
    whole point of it is that the value appears elsewhere.
    """
    if not claim.evidence:
        return GroundingResult(False, QuarantineReason.NO_EVIDENCE)

    primary = [e for e in claim.evidence if e.kind == "primary"] or claim.evidence

    failures: list[GroundingResult] = []
    for ev in primary:
        result = verify_evidence(ev, pages)
        if not result.ok:
            failures.append(result)
            continue
        if _value_present(claim, ev):
            return GroundingResult(True)
        failures.append(
            GroundingResult(
                False,
                QuarantineReason.VALUE_NOT_IN_QUOTE,
                f"Claimed {claim.value.raw!r}; the cited text is "
                f"{normalise_text(ev.quote)[:100]!r}.",
            )
        )

    # Report the most diagnostic failure rather than merely the first. A
    # value-not-present result tells a reader far more than a span error does.
    for reason in (
        QuarantineReason.VALUE_NOT_IN_QUOTE,
        QuarantineReason.QUOTE_MISMATCH,
        QuarantineReason.SPAN_OUT_OF_RANGE,
        QuarantineReason.EMPTY_QUOTE,
        QuarantineReason.PAGE_NOT_FOUND,
    ):
        for f in failures:
            if f.reason is reason:
                return f
    return failures[0]


def _value_present(claim: Claim, ev: Evidence) -> bool:
    """Is the asserted value literally in the quoted text?

    Checked on the raw form the document used, not on the canonical magnitude:
    the canonical form is our arithmetic, and verifying our own arithmetic
    against the page would prove nothing. What must be true is that the reader,
    looking at the highlighted span, sees the number the system is reporting.
    """
    quote = normalise_text(ev.quote)
    raw = normalise_text(claim.value.raw)
    if not raw:
        return False

    if raw in quote:
        return True

    key = _numeric_key(raw)
    if key and key in _numeric_key(quote):
        return True

    # A parenthesised negative is quoted as "(2,345)" but may be carried as
    # "-2,345" once parsed. Same characters on the page, different sign notation.
    stripped = key.lstrip("-()")
    return bool(stripped) and stripped in _numeric_key(quote)


@dataclass
class GroundingReport:
    """The headline metric, and the breakdown behind it."""

    grounded: int = 0
    quarantined: int = 0
    by_reason: dict[str, int] | None = None

    @property
    def total(self) -> int:
        return self.grounded + self.quarantined

    @property
    def pass_rate(self) -> float:
        return self.grounded / self.total if self.total else 0.0

    def summary(self) -> str:
        return (
            f"grounding pass rate {self.pass_rate:.1%} "
            f"({self.grounded} grounded, {self.quarantined} quarantined)"
        )


def verify_all(
    claims: list[Claim], pages: dict[int, Page]
) -> tuple[list[Claim], list[tuple[Claim, GroundingResult]], GroundingReport]:
    """Partition claims into those that may enter the graph and those that may not."""
    grounded: list[Claim] = []
    quarantined: list[tuple[Claim, GroundingResult]] = []
    by_reason: dict[str, int] = {}

    for claim in claims:
        result = verify_claim(claim, pages)
        if result.ok:
            grounded.append(claim)
        else:
            quarantined.append((claim, result))
            key = result.reason.value if result.reason else "unknown"
            by_reason[key] = by_reason.get(key, 0) + 1

    return (
        grounded,
        quarantined,
        GroundingReport(len(grounded), len(quarantined), by_reason),
    )
