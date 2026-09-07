"""Attaching claims to the ontology.

Extraction produces `subject_raw` and `predicate_raw` — whatever the document
happened to call things. Comparison needs `subject_id` and `predicate_id`, which
are nodes in an ontology grown from every document seen so far. This is the step
between, and it is deliberately separate from both: extraction should not know
about the ontology, and the comparator should not be doing entity resolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from core.canon.registry import Adjudicator, Decision, Registry
from core.models import Claim

log = structlog.get_logger(__name__)


@dataclass
class Canonicalisation:
    claims: list[Claim]
    decisions: list[Decision] = field(default_factory=list)
    unresolved: list[tuple[Claim, str]] = field(default_factory=list)

    def stats(self) -> dict[str, int]:
        return {
            "claims_in": len(self.claims) + len(self.unresolved),
            "claims_canonicalised": len(self.claims),
            "unresolved": len(self.unresolved),
        }


def canonicalise(
    claims: list[Claim],
    entities: Registry,
    predicates: Registry,
    *,
    adjudicator: Adjudicator | None = None,
) -> Canonicalisation:
    """Resolve every claim's subject and predicate onto ontology nodes.

    A claim whose subject or predicate cannot be resolved is set aside rather
    than given a guessed identity. An unresolved claim simply does not
    participate in comparison; a wrongly resolved one generates false
    relationships, which is much worse and far harder to notice.
    """
    out = Canonicalisation(claims=[])

    for claim in claims:
        try:
            subject = entities.resolve(claim.subject_raw, adjudicator=adjudicator)
        except ValueError as e:
            out.unresolved.append((claim, f"subject: {e}"))
            continue

        try:
            predicate = predicates.resolve(
                claim.predicate_raw,
                adjudicator=adjudicator,
                example=claim.evidence[0].quote[:120] if claim.evidence else None,
            )
        except ValueError as e:
            out.unresolved.append((claim, f"predicate: {e}"))
            continue

        claim.subject_id = subject.node_id
        claim.predicate_id = predicate.node_id
        out.claims.append(claim)
        out.decisions.extend([subject, predicate])

    log.info("canonicalised", **out.stats())
    return out
