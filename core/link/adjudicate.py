"""The adjudicator — the model's only say in what a relationship is, and a narrow one.

The comparator decides every pair it can decide from evidence: same scope and
same value is corroboration, one differing axis is reconciliation and the axis is
the reason. What reaches this module is the residue it cannot decide — pairs
whose scopes differ on two or more axes, or whose values differ with no resolved
period on either side to establish that they describe the same thing.

Three constraints keep this honest, and they matter more than the prompt does.

**The model chooses, it does not compose.** It picks one of four relations and
optionally one axis from a fixed list. It never writes a number, a period, a
subject or a predicate. Every value in the resulting verdict came from the
extractor and is grounded in a source span; adjudication cannot introduce a
figure that is not in a document.

**It cannot invent a scope difference.** If it answers "reconciled by segment"
on a pair where the comparator found both segments identical, the answer is
rejected and the pair stays ambiguous. The model is allowed to *select* among
differences the deterministic layer actually found; it is not allowed to claim
one that is not there. This is the guard that stops the adjudicator from being
a plausible-sounding way to launder a wrong answer.

**Contradiction carries the same burden here as it does there.** The comparator
refuses to call a conflict without a resolved period on both sides, and a model
does not get to overrule that on the strength of confident prose. An
unsupportable contradiction is downgraded, not accepted.

Volume is the other half of the design. There are roughly thirty thousand
ambiguous pairs across the starter corpus and a free tier of 200,000 tokens a
day, so adjudicating all of them is not arithmetic that works. Pairs are
adjudicated **on demand** — when a reader opens one — with a small, deliberately
chosen set precomputed for the shipped snapshot. A pair nobody has looked at
stays ambiguous and says so, which is a truthful state rather than a gap.
"""

from __future__ import annotations

import structlog

from core.extract.gateway import Gateway, LLMUnavailable, RateLimited
from core.models import Claim, Evidence, Relation, Verdict

log = structlog.get_logger(__name__)

ADJUDICATION_PROMPT_VERSION = "adjudicate-v1"

AXES = ["period", "basis", "segment", "geography", "accounting", "modality", "vintage"]

ADJUDICATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relation", "axis", "reason"],
    "properties": {
        "relation": {
            "type": "string",
            "enum": ["corroboration", "contradiction", "reconciled", "unrelated"],
        },
        # Required, nullable — Groq strict mode forbids omitted fields, so
        # "no axis" is expressed as null rather than by leaving it out.
        "axis": {"type": ["string", "null"], "enum": [*AXES, None]},
        "reason": {"type": "string"},
    },
}

ADJUDICATE_SYSTEM = """\
You are resolving whether two extracted financial facts are related, and how.

A deterministic comparator has already done everything that can be decided from
structure. It could not decide this pair, and its full reasoning is given to you.
Your job is to choose between four answers, using the scope axes and the source
sentences.

  corroboration  the two statements assert the same thing, and agree
  contradiction  the two statements assert the same thing, and cannot both be true
  reconciled     the values differ, and ONE named scope axis explains why
  unrelated      they are not actually about the same quantity

Rules you must follow:

- Choose "reconciled" only if you can name the single axis that explains the
  difference, and only if the comparator's trace shows that axis actually
  differs. Do not name an axis it reports as identical.
- Choose "contradiction" only if both statements clearly cover the same period.
  If either period is unknown, you cannot establish they describe the same
  thing, and the honest answer is "unrelated" or the axis that separates them.
  Absence of evidence is not a conflict.
- Prefer "unrelated" over a guess. Two figures that merely landed on the same
  ontology node are common; a false contradiction is expensive.
- Your reason must be one sentence, and must refer to what the documents say.
"""


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
    period = (
        f"{scope.period.start} to {scope.period.end}"
        if scope.period.start
        else f"unresolved (written as {scope.period.label or 'nothing'})"
    )
    return "\n".join(
        [
            f"{label}:",
            f"  subject:    {claim.subject_raw}",
            f"  predicate:  {claim.predicate_raw}",
            f"  value:      {claim.value.raw}  (normalised {claim.value.canonical_magnitude})",
            f"  period:     {period}",
            f"  basis:      {scope.basis.value}",
            f"  segment:    {scope.segment or 'not stated'}",
            f"  geography:  {scope.geography or 'not stated'}",
            f"  accounting: {scope.accounting.value}",
            f"  modality:   {scope.modality.value}",
            f"  source:     {_source(claim, names)}",
            f"  sentence:   {_quote(claim, 400)}",
        ]
    )


async def adjudicate(
    a: Claim,
    b: Claim,
    verdict: Verdict,
    gateway: Gateway,
    names: dict | None = None,
) -> Verdict:
    """Resolve one ambiguous pair, or return the original verdict unchanged.

    Never raises. A pair that cannot be adjudicated — no provider, a rate limit,
    a malformed answer, an answer that fails the guards — stays exactly as the
    comparator left it. Degrading to "we do not know" is always available and is
    always better than degrading to a confident guess.
    """
    if verdict.relation is not Relation.AMBIGUOUS:
        return verdict

    diffs = set(a.scope.differing_axes(b.scope))
    user = "\n\n".join(
        [
            _render(a, "Statement A", names),
            _render(b, "Statement B", names),
            "The comparator's reasoning:\n" + "\n".join(f"  {t}" for t in verdict.trace),
            f"Axes it found genuinely differing: {', '.join(sorted(diffs)) or 'none'}",
        ]
    )

    try:
        resp = await gateway.complete_json(
            system=ADJUDICATE_SYSTEM, user=user, schema=ADJUDICATE_SCHEMA, max_tokens=300
        )
    except (LLMUnavailable, RateLimited) as e:
        log.info("adjudicate.unavailable", error=str(e)[:120])
        return verdict

    answer = resp.parsed or {}
    raw_relation = answer.get("relation")
    reason = (answer.get("reason") or "").strip()
    axis = answer.get("axis")

    try:
        relation = Relation(raw_relation)
    except ValueError:
        log.warning("adjudicate.bad_relation", got=raw_relation)
        return verdict

    # ── the guards ───────────────────────────────────────────────────────────

    if relation is Relation.RECONCILED:
        if axis not in diffs:
            # The model named a scope difference the comparator did not find.
            # Reconciliation whose explanation is not true of the data is worse
            # than no answer, because it reads as an explanation.
            return _refused(
                verdict,
                f"the model answered 'reconciled by {axis}', but the two claims do not "
                f"differ on {axis}. An explanation that is not true of the data is not "
                f"an explanation, so the pair stays ambiguous.",
            )

    if relation is Relation.CONTRADICTION:
        if a.scope.period.start is None or b.scope.period.start is None:
            return _refused(
                verdict,
                "the model answered 'contradiction', but at least one claim has no "
                "resolved period, so there is no positive evidence that the two "
                "statements describe the same thing. The same burden applies to the "
                "model as to the comparator.",
            )

    return Verdict(
        relation=relation,
        axis=axis if relation is Relation.RECONCILED else None,
        # Adjudicated verdicts are worth less than derived ones and should not
        # be presented as equals. A reader sorting by confidence should see the
        # things the machine *proved* before the things it was persuaded of.
        confidence=min(verdict.confidence, 0.7),
        decided_by="llm",
        trace=verdict.trace
        + [
            f"→ escalated to {resp.model} ({resp.provider}) after the comparator "
            f"could not decide",
            f"→ adjudicated as {relation.value}"
            + (f" on the {axis} axis" if axis and relation is Relation.RECONCILED else ""),
            f"→ the model's reason: {reason}",
        ],
        explanation=reason or None,
    )


def _refused(verdict: Verdict, why: str) -> Verdict:
    log.info("adjudicate.refused", why=why[:160])
    return Verdict(
        relation=Relation.AMBIGUOUS,
        confidence=verdict.confidence,
        decided_by="deterministic",
        trace=verdict.trace + [f"→ the adjudicator's answer was rejected: {why}"],
    )


async def adjudicate_all(
    pairs: list[tuple[Claim, Claim, Verdict]],
    gateway: Gateway,
    *,
    limit: int = 50,
) -> list[Verdict]:
    """Adjudicate a bounded number of pairs, most confident first.

    The limit is not timidity. Thirty thousand ambiguous pairs against 200,000
    tokens a day is about six days of allowance for one corpus, so the question
    is not whether to bound this but where to spend the bound. Highest
    confidence first: those are the pairs where the extraction is most likely
    sound and the ambiguity most likely real, which is where an answer is worth
    having.
    """
    ordered = sorted(pairs, key=lambda p: -p[2].confidence)[:limit]
    out: list[Verdict] = []
    for a, b, verdict in ordered:
        out.append(await adjudicate(a, b, verdict, gateway))
    return out
