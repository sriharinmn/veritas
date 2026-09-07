"""The explainer — prose over a decision the model did not make.

The brief asks for relationships that are *explained*. There are two ways to
read that and only one of them is worth building. The easy reading is "have a
language model write a paragraph", which produces something fluent that a reader
cannot check. The reading taken here is that a person should be able to follow
the decision and disagree with it at a specific step.

So the machine reasoning is the primary artefact — `Verdict.trace` is a list of
the actual steps, and the UI shows it. This module adds a paragraph on top, for
readability, under a strict constraint: **it may restate the verdict, never
revise it.** The relation is already decided when this runs. The model is a
writer here, not a judge.

That constraint is enforced rather than requested. If the generated prose
asserts a relation other than the one it was given, it is discarded and the
trace stands alone. A pretty sentence that disagrees with the decision it is
attached to is worse than no sentence, because it is the part people read.
"""

from __future__ import annotations

import re

import structlog

from core.extract.gateway import Gateway, LLMUnavailable, RateLimited
from core.models import Claim, Evidence, Relation, Verdict

log = structlog.get_logger(__name__)

EXPLAIN_PROMPT_VERSION = "explain-v1"

EXPLAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["explanation"],
    "properties": {"explanation": {"type": "string"}},
}

EXPLAIN_SYSTEM = """\
You are writing one short paragraph for a financial analyst, explaining a
relationship between two facts that has ALREADY been decided.

You are not deciding anything. The verdict is given to you and is correct. Your
job is to say, in plain English, why these two numbers stand in that relationship
— using the scope axes and what the documents actually say.

Rules:
- Two or three sentences. No preamble, no "In summary".
- State the verdict's own conclusion. Never suggest a different one.
- Name the concrete things: the periods, the segments, the basis, the figures.
- If the verdict is "reconciled", the named axis is the explanation. Lead with it.
- Do not hedge with "may" or "possibly". The decision has been made.
- Never introduce a number that is not in the material you were given.
"""

# Vocabulary that would mean the prose has picked a different verdict than the
# one it was handed, as anchored regexes rather than substrings.
#
# Substring matching is wrong here and quietly so: "consistent" is inside
# "inconsistent", so a paragraph saying two figures are *inconsistent* read as
# corroboration vocabulary and sailed through a corroboration verdict unchecked.
# "agree" inside "disagree" is the same trap. A leading \b fixes both, because
# there is no word boundary between the negating prefix and the stem.
_CLAIMS_RELATION = {
    Relation.CONTRADICTION: (
        r"\bcontradict", r"\bconflict", r"\binconsisten", r"\bcannot both\b",
        r"\bdisagree",
    ),
    Relation.CORROBORATION: (
        r"\bcorroborat", r"\bagree", r"\bconfirm", r"\bconsistent\b",
        r"\bthe same figure\b",
    ),
    Relation.RECONCILED: (
        r"\breconcil", r"\bexplained by\b", r"\bdifference is due\b",
        r"\baccounted for by\b",
    ),
    Relation.UNRELATED: (
        r"\bunrelated\b", r"\bnot comparable\b", r"\bdifferent quantit",
    ),
}


def _contradicts_verdict(text: str, relation: Relation) -> str | None:
    """Does this prose assert a relation other than the one that was decided?

    Deliberately conservative: it fires only when the text uses the vocabulary of
    a *different* verdict and not the vocabulary of its own. Prose that says
    "these figures agree" under a CONTRADICTION verdict is caught; prose that
    merely mentions the word "agree" in passing under a CORROBORATION verdict is
    not, because its own vocabulary is present too.
    """
    lowered = text.lower()
    own = _CLAIMS_RELATION.get(relation, ())
    if any(re.search(pattern, lowered) for pattern in own):
        return None
    for other, patterns in _CLAIMS_RELATION.items():
        if other is relation:
            continue
        hit = next((m.group(0) for p in patterns if (m := re.search(p, lowered))), None)
        if hit is not None:
            return (
                f"prose used the language of {other.value} ({hit!r}) "
                f"under a {relation.value} verdict"
            )
    return None


def _primary(claim: Claim) -> Evidence | None:
    """The span the claim's value was actually read from.

    A claim can carry a second Evidence row for context inherited from a table
    header or a document-level statement. That row is real evidence and is kept,
    but it is not where the number is, so it is not what a reader is shown first.
    """
    for e in claim.evidence:
        if e.kind == "primary":
            return e
    return claim.evidence[0] if claim.evidence else None


def _source(claim: Claim, names: dict | None = None) -> str:
    e = _primary(claim)
    if e is None:
        return "an unrecorded source"
    name = (names or {}).get(e.document_id)
    return f"{name} p{e.page}" if name else f"page {e.page}"


def _quote(claim: Claim, limit: int = 300) -> str:
    e = _primary(claim)
    return e.quote.strip()[:limit] if e else ""


def _render(claim: Claim, label: str, names: dict | None = None) -> str:
    scope = claim.scope
    period = scope.period.label or (
        f"{scope.period.start} to {scope.period.end}"
        if scope.period.start
        else "period not stated"
    )
    bits = [f"{label}: {claim.subject_raw} — {claim.predicate_raw} = {claim.value.raw}"]
    bits.append(f"    period {period}, basis {scope.basis.value}")
    if scope.segment:
        bits.append(f"    segment {scope.segment}")
    if scope.geography:
        bits.append(f"    geography {scope.geography}")
    bits.append(f"    from {_source(claim, names)}")
    bits.append(f"    “{_quote(claim)}”")
    return "\n".join(bits)


async def explain(
    a: Claim, b: Claim, verdict: Verdict, gateway: Gateway, names: dict | None = None
) -> Verdict:
    """Attach prose to a verdict. Returns the verdict unchanged on any failure."""
    if verdict.explanation:
        return verdict

    axis_line = f"\nThe axis that explains it: {verdict.axis}" if verdict.axis else ""
    user = "\n\n".join(
        [
            _render(a, "Fact A", names),
            _render(b, "Fact B", names),
            f"The decided verdict: {verdict.relation.value}{axis_line}",
            "How it was decided:\n" + "\n".join(f"  {t}" for t in verdict.trace),
        ]
    )

    try:
        resp = await gateway.complete_json(
            system=EXPLAIN_SYSTEM, user=user, schema=EXPLAIN_SCHEMA, max_tokens=400
        )
    except (LLMUnavailable, RateLimited) as e:
        log.info("explain.unavailable", error=str(e)[:120])
        return verdict

    text = ((resp.parsed or {}).get("explanation") or "").strip()
    if not text:
        return verdict

    problem = _contradicts_verdict(text, verdict.relation)
    if problem is not None:
        # The trace records the rejection rather than hiding it. A reader who
        # notices the missing paragraph should be able to find out why.
        log.warning("explain.rejected", reason=problem)
        return verdict.model_copy(
            update={"trace": verdict.trace + [f"→ generated prose was discarded: {problem}"]}
        )

    return verdict.model_copy(update={"explanation": text})


def fallback_explanation(
    a: Claim, b: Claim, verdict: Verdict, names: dict | None = None
) -> str:
    """A readable sentence with no model involved at all.

    The zero-key demo has to show explained relationships, not empty cards, so
    the deterministic path produces its own prose from the same facts the trace
    is built on. It is drier than the generated version and it is always
    available, which on balance is the better default.
    """
    subject = a.subject_raw or "this figure"
    predicate = a.predicate_raw or "the value"
    av, bv = a.value.raw, b.value.raw
    where = f"{_source(a, names)} and {_source(b, names)}"

    match verdict.relation:
        case Relation.CORROBORATION:
            return (
                f"Both documents report {predicate} for {subject} as the same figure "
                f"({av} and {bv}) under identical scope, so they corroborate each other. "
                f"Sources: {where}."
            )
        case Relation.CONTRADICTION:
            return (
                f"{where} report {predicate} for {subject} as {av} and {bv}. Every scope "
                f"axis — period, basis, segment, geography, accounting standard and "
                f"modality — was checked and found identical, so no difference in what "
                f"is being measured accounts for the gap."
            )
        case Relation.RECONCILED:
            axis = verdict.axis or "one scope axis"
            return (
                f"{av} and {bv} look like a conflict but are not: the two statements "
                f"differ on {axis}, and that difference is the whole explanation. "
                f"Sources: {where}."
            )
        case Relation.UNRELATED:
            return f"These two figures are not measuring the same quantity. Sources: {where}."
        case _:
            return (
                f"{av} and {bv} differ, but the evidence does not establish that the two "
                f"statements describe the same thing, so no relationship is asserted. "
                f"Sources: {where}."
            )
