"""Candidate pair generation — never O(n²).

Comparing every claim against every other claim is the obvious approach and it
does not survive contact with a real corpus: the six starter documents produce
thousands of claims, and the all-pairs count is in the millions. Worse, it is
the wrong shape for the brownie point the brief asks about — adding a seventh
document should cost the new document's claims against existing blocks, not a
full rebuild.

So this is classic entity-resolution engineering: **block, then compare.**

    exact block   claims sharing (subject, predicate) — the overwhelming
                  majority of real relationships live here
    value block   claims with near-equal normalised magnitudes under *different*
                  predicates. This is how alias metrics are found: if
                  "revenue from operations" and "turnover" are the same number
                  in the same period, the ontology probably should have merged
                  them, and this surfaces that rather than hiding it.

Everything the blocks do not generate is, by construction, not compared. That is
a real recall limitation and it is stated here rather than buried: two claims
about the same thing under predicates the ontology kept separate and whose
values genuinely differ will never be paired.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from uuid import UUID

import structlog

from core.models import Claim

log = structlog.get_logger(__name__)

# A block larger than this is almost always a spurious predicate ("total",
# "amount") that the ontology failed to distinguish. Comparing all pairs inside
# it would burn the budget on noise, so it is capped and reported rather than
# silently truncated.
MAX_BLOCK = 60

# Two significant figures is enough to notice that two claims are "the same
# number" without demanding they match exactly.
VALUE_BUCKET_SIGFIGS = 3


@dataclass(frozen=True)
class Pair:
    a: Claim
    b: Claim
    reason: str

    @property
    def cross_document(self) -> bool:
        return self.a.evidence[0].document_id != self.b.evidence[0].document_id


def _value_bucket(claim: Claim) -> str | None:
    v = claim.value.canonical_magnitude
    if v is None or v == 0:
        return None
    try:
        rounded = round(v, -(v.copy_abs().adjusted() - VALUE_BUCKET_SIGFIGS + 1))
    except (ValueError, ArithmeticError):
        return None
    return f"{claim.value.kind}:{rounded.normalize()}"


def exact_blocks(claims: list[Claim]) -> dict[tuple[UUID, UUID], list[Claim]]:
    blocks: dict[tuple[UUID, UUID], list[Claim]] = defaultdict(list)
    for c in claims:
        if c.subject_id and c.predicate_id:
            blocks[(c.subject_id, c.predicate_id)].append(c)
    return blocks


def value_blocks(claims: list[Claim]) -> dict[str, list[Claim]]:
    blocks: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        if (b := _value_bucket(c)) is not None:
            blocks[b].append(c)
    return blocks


@dataclass
class PairingReport:
    claims: int = 0
    exact_blocks: int = 0
    value_blocks: int = 0
    pairs: int = 0
    oversized_blocks: int = 0
    skipped_for_size: int = 0

    def summary(self) -> str:
        return (
            f"{self.pairs:,} candidate pairs from {self.claims:,} claims "
            f"({self.exact_blocks:,} exact blocks, {self.value_blocks:,} value blocks)"
            + (
                f"; {self.skipped_for_size:,} pairs skipped in "
                f"{self.oversized_blocks} oversized blocks"
                if self.oversized_blocks
                else ""
            )
        )


def generate_pairs(
    claims: list[Claim], *, cross_document_only: bool = False
) -> tuple[list[Pair], PairingReport]:
    """Produce the pairs worth comparing, and account for what was skipped.

    `cross_document_only` narrows to relationships *between* documents, which is
    what the assignment's first three cases are about. Same-document pairs still
    matter — a restated figure contradicting its own earlier statement is real —
    so they are included by default.
    """
    report = PairingReport(claims=len(claims))
    seen: set[tuple[str, str]] = set()
    pairs: list[Pair] = []

    def emit(a: Claim, b: Claim, reason: str) -> None:
        key = tuple(sorted((str(a.id), str(b.id))))
        if key in seen:
            return
        if cross_document_only and a.evidence[0].document_id == b.evidence[0].document_id:
            return
        seen.add(key)  # type: ignore[arg-type]
        pairs.append(Pair(a, b, reason))

    ex = exact_blocks(claims)
    report.exact_blocks = len(ex)
    for (_subject, _predicate), block in ex.items():
        if len(block) < 2:
            continue
        if len(block) > MAX_BLOCK:
            report.oversized_blocks += 1
            report.skipped_for_size += len(block) * (len(block) - 1) // 2
            # Keep the most confident claims rather than an arbitrary prefix, so
            # the cap degrades quality gently instead of by position in the file.
            block = sorted(block, key=lambda c: -c.confidence)[:MAX_BLOCK]
        for a, b in combinations(block, 2):
            emit(a, b, "same subject and predicate")

    vb = value_blocks(claims)
    report.value_blocks = len(vb)
    for _bucket, block in vb.items():
        if len(block) < 2 or len(block) > MAX_BLOCK:
            continue
        for a, b in combinations(block, 2):
            if a.predicate_id == b.predicate_id:
                continue  # already covered by the exact block
            if a.subject_id != b.subject_id:
                continue
            emit(a, b, "same subject, equal value under different predicates")

    report.pairs = len(pairs)
    log.info("paired", **{k: v for k, v in report.__dict__.items()})
    return pairs, report


def pairs_for_new_claims(
    new_claims: list[Claim], existing: list[Claim]
) -> Iterator[Pair]:
    """Incremental pairing: a new document against what is already known.

    This is what makes adding a seventh document cost O(new × block) rather than
    a full rebuild — the brief's "new documents incrementally, without
    rebuilding all existing knowledge".
    """
    index: dict[tuple[UUID, UUID], list[Claim]] = defaultdict(list)
    for c in existing:
        if c.subject_id and c.predicate_id:
            index[(c.subject_id, c.predicate_id)].append(c)

    for c in new_claims:
        if not (c.subject_id and c.predicate_id):
            continue
        for other in index.get((c.subject_id, c.predicate_id), [])[:MAX_BLOCK]:
            yield Pair(c, other, "same subject and predicate (incremental)")
