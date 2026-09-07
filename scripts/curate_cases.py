"""Select the assignment's four required cases from the corpus, by criterion.

    python -m scripts.curate_cases            # choose and write evals/cases.json
    python -m scripts.curate_cases --show     # print the current selection

The assignment asks for four demonstrations. The tempting way to produce them is
to read the output until something looks good and paste it into the README. That
works, and it proves nothing: a reader cannot tell a representative example from
a lucky one, and neither can the person who picked it.

So each case is defined as a *scoring function* over every pair the system
produced, and the winner is whatever scores highest. The criteria are written
down here in the open, which means a reviewer can disagree with a criterion â€”
which is a much better conversation than disagreeing with a hand-picked example.

The scores deliberately reward the properties that make a case *demonstrative*
rather than merely correct:

  case 1  cross-document, and the two values written in different units, so the
          normaliser is visibly doing work rather than matching strings
  case 2  cross-document, both periods resolved, values far apart â€” a conflict
          that survives every check the comparator can make
  case 3  exactly one axis differs and that axis is the whole explanation;
          fiscal-vs-calendar scores highest because it is the failure mode this
          corpus was chosen to contain
  case 4  a real failure, taken from the quarantine queue and the ambiguous
          residue rather than from memory

Case 4 is not a consolation prize. A system that reports its own failure modes
with the same machinery it reports its successes is making a stronger claim than
one that only shows wins.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.models import Claim, FiscalConvention, Relation
from core.store.checkpoints import Edge, KnowledgeLayer, build

OUT = Path("evals/cases.json")


@dataclass
class Scored:
    edge: Edge
    score: float
    why: list[str]


def _units(claim: Claim) -> str:
    """How the value was denominated, as written.

    Scale is not a field on TypedValue â€” it is folded into `canonical_magnitude`
    at parse time, which is the right place for it â€” so "crore" versus "million"
    shows up as a difference in `raw` rather than here. Both are checked.
    """
    v = claim.value
    return f"{v.currency or ''}|{v.unit or ''}|{v.ratio_basis or ''}".strip("|")


_SCALE_NAMES = {
    1: "units",
    100_000: "lakh",
    1_000_000: "million",
    10_000_000: "crore",
    1_000_000_000: "billion",
    1_000_000_000_000: "trillion",
}


def _implied_scale(claim: Claim) -> tuple[int | None, str | None]:
    """Recover the multiplier the document was written in.

    Scale is not stored on TypedValue — it is applied during parsing and folded
    into `canonical_magnitude`, which is the right place for it because the
    comparator should never see a scale at all. But it is exactly the thing that
    makes case 1 worth showing: "1,266" and "127" are the same money only
    because one page says millions and the other says crore. Dividing the
    canonical value by the digits as written recovers it for display.
    """
    digits = claim.value.raw.replace(",", "").strip()
    try:
        written = float(digits)
    except ValueError:
        return None, None
    if not written or claim.value.canonical_magnitude is None:
        return None, None
    factor = float(claim.value.canonical_magnitude) / written
    for value, name in _SCALE_NAMES.items():
        if abs(factor - value) / value < 0.02:
            return value, name
    return int(factor), None


def _written_differently(a: Claim, b: Claim) -> bool:
    """Do the two documents express the same magnitude in different words?

    This is the property that makes case 1 worth showing. "7,225 crore" and
    "72,251 million" are the same number and different strings, and a pipeline
    built on embeddings alone gets this wrong.
    """
    return a.value.raw.strip() != b.value.raw.strip() and (
        _units(a) != _units(b) or a.value.raw.replace(",", "") != b.value.raw.replace(",", "")
    )


def _relative_gap(a: Claim, b: Claim) -> float | None:
    x, y = a.value.canonical_magnitude, b.value.canonical_magnitude
    if x is None or y is None or not x:
        return None
    return float(abs(abs(x) - abs(y)) / abs(x))


def _significant_enough(claim: Claim) -> bool:
    """Does the value assert enough precision for agreement to mean anything?

    The comparator compares at the precision the document actually claimed, so
    "0.9" and "1.0" agree — their rounding intervals meet at 0.95. That is the
    right rule and it is also a terrible demonstration, because a reader sees an
    11% difference called corroboration and stops trusting the output. A case
    selected to be *shown* needs values precise enough that agreement is not an
    artefact of the tolerance.
    """
    digits = "".join(ch for ch in claim.value.raw if ch.isdigit()).lstrip("0")
    return len(digits) >= 3


def score_case_1(edges: list[Edge]) -> list[Scored]:
    """Corroborated across documents, expressed differently."""
    out = []
    for e in edges:
        if e.verdict.relation is not Relation.CORROBORATION:
            continue
        if not (_significant_enough(e.a) and _significant_enough(e.b)):
            continue
        gap = _relative_gap(e.a, e.b)
        if gap is None or gap > 0.005:
            # Agreement should be near-exact, not merely inside a tolerance.
            # "7,225 crore" against "72,251 million" is the demonstration; two
            # figures that agree only because the coarser one was rounded is not.
            continue

        score, why = 0.0, []
        if e.cross_document:
            score += 10
            why.append("the two statements come from different documents")
        if gap == 0:
            score += 6
            why.append("identical after normalisation, not merely within tolerance")
        if _written_differently(e.a, e.b):
            score += 8
            why.append(f"written differently: {e.a.value.raw!r} against {e.b.value.raw!r}")
        _, scale_a = _implied_scale(e.a)
        _, scale_b = _implied_scale(e.b)
        if scale_a and scale_b and scale_a != scale_b:
            # The heart of the case. Two documents, two scales, one quantity —
            # and a pipeline built on string or vector similarity gets this
            # wrong, because "1,266" and "127" look nothing alike.
            score += 12
            why.append(
                f"stated in different scales: {e.a.value.raw} {scale_a} against "
                f"{e.b.value.raw} {scale_b} — the same money, written two ways"
            )
        elif _units(e.a) != _units(e.b):
            score += 6
            why.append("different currency or unit, so normalisation is doing the work")
        if e.a.scope.period.start is not None and e.b.scope.period.start is not None:
            score += 3
            why.append("both periods resolve to real dates")
        score += min(len(e.a.predicate_raw.split()), 4)
        out.append(Scored(e, score, why))
    return sorted(out, key=lambda s: -s.score)


def score_case_2(edges: list[Edge]) -> list[Scored]:
    """A genuine contradiction â€” one that survives every check available."""
    out = []
    for e in edges:
        if e.verdict.relation is not Relation.CONTRADICTION:
            continue
        a, b = e.a, e.b
        if a.scope.period.start is None or b.scope.period.start is None:
            continue  # the comparator should already have escalated these
        if _units(a) != _units(b):
            continue  # a currency or unit mismatch is an extraction fault, not a conflict
        if not (_significant_enough(a) and _significant_enough(b)):
            continue
        gap = _relative_gap(a, b)
        if gap is None or not (0.01 <= gap <= 0.60):
            # The band matters more than it looks, and getting it wrong was
            # instructive: rewarding *large* gaps surfaced a "contradiction" of
            # 112,348%, which is not a disagreement between two documents. It is
            # two different quantities collapsed onto one ontology node, and a
            # reviewer shown that as the headline contradiction would be right to
            # stop trusting everything else. Below 1% is a rounding argument;
            # above 60% the likelier explanation is that the system is wrong,
            # not the filing. Those belong in case 4, and they are sent there.
            continue

        score, why = 0.0, []
        if e.cross_document:
            score += 10
            why.append("the conflict is between two documents, not within one")
        score += 5
        why.append("every scope axis was checked and found identical")
        # Peak reward around a 5-25% gap: unmistakably a disagreement, still
        # plausibly two people describing the same thing.
        score += 8 * (1 - abs(gap - 0.15) / 0.45)
        why.append(f"the values differ by {gap * 100:.1f}%, a plausible disagreement")
        if a.scope.basis is not b.scope.basis:
            score -= 4  # if the basis is unstated on one side, be suspicious
        score += min(len(a.predicate_raw.split()), 4)
        out.append(Scored(e, score, why))
    return sorted(out, key=lambda s: -s.score)


def score_case_3(edges: list[Edge]) -> list[Scored]:
    """An apparent contradiction that context explains. The strongest case."""
    out = []
    for e in edges:
        if e.verdict.relation is not Relation.RECONCILED:
            continue
        a, b, axis = e.a, e.b, e.verdict.axis
        if _units(a) != _units(b):
            continue
        if not (_significant_enough(a) and _significant_enough(b)):
            continue
        gap = _relative_gap(a, b)
        if gap is None or not (0.02 <= gap <= 4.0):
            # The same discipline as case 2, with a wider ceiling: a quarter and
            # a full year legitimately differ by 4x. Beyond that the difference
            # is not being *explained* by the axis, it is being excused by it.
            continue

        score, why = 0.0, []
        if e.cross_document:
            score += 10
            why.append("the two statements come from different institutions")
        if axis == "period":
            score += 8
            why.append("the periods differ â€” the classic false conflict")
            conventions = {a.scope.period.convention, b.scope.period.convention}
            if FiscalConvention.CALENDAR in conventions and len(conventions) > 1:
                # The IMF reports India on calendar years; the Economic Survey
                # and the RBI use April-March. This is precisely the case the
                # macroeconomic corpus was chosen to contain.
                score += 12
                why.append(
                    "one document uses calendar years and the other the April-March "
                    "fiscal year, so both figures are correct"
                )
        elif axis in ("basis", "segment", "modality"):
            score += 6
            why.append(f"the {axis} axis explains the difference on its own")
        if gap > 0.05:
            score += 4
            why.append(
                f"the {gap * 100:.0f}% gap looks alarming until the axis is named"
            )
        score += min(len(a.predicate_raw.split()), 4)
        out.append(Scored(e, score, why))
    return sorted(out, key=lambda s: -s.score)


def case_4(layer: KnowledgeLayer) -> dict:
    """A real failure, sourced from the system's own records.

    Three kinds of honesty are available and all three are used: what the
    grounding gate refused, what the comparator could not decide, and where the
    weakest field actually is. None of it is anecdote â€” every number here comes
    from the run that produced everything else on this page.
    """
    by_reason: dict[str, int] = {}
    for q in layer.quarantined:
        by_reason[q.get("reason", "unknown")] = by_reason.get(q.get("reason", "unknown"), 0) + 1

    resolved = sum(1 for c in layer.claims if c.scope.period.start is not None)
    total = len(layer.claims) or 1
    counts = layer.counts()

    sample = []
    for q in layer.quarantined[:5]:
        claim = q.get("claim", {})
        sample.append(
            {
                "reason": q.get("reason"),
                "detail": str(q.get("detail"))[:200],
                "value": (claim.get("value") or {}).get("raw"),
                "predicate": claim.get("predicate_raw"),
            }
        )

    # Contradictions whose two values are orders of magnitude apart are not
    # disagreements between documents. They are two different quantities landed
    # on one ontology node, and the pair generator then compares them. This is
    # the single most useful failure the system can report about itself, and it
    # was found by trying to *select* a headline contradiction and getting one
    # that differed by 112,348%.
    absurd = []
    for e in layer.edges:
        if e.verdict.relation is not Relation.CONTRADICTION:
            continue
        gap = _relative_gap(e.a, e.b)
        if gap is not None and gap > 5.0:
            absurd.append((gap, e))
    absurd.sort(key=lambda t: -t[0])
    contradictions = counts.get("contradiction", 0) or 1

    return {
        "title": "Where this system is weakest, measured rather than remembered",
        "ontology_over_merge": {
            "implausible_contradictions": len(absurd),
            "of_total_contradictions": counts.get("contradiction", 0),
            "share": round(len(absurd) / contradictions, 4),
            "worst_examples": [
                {
                    "predicate": e.a.predicate_raw,
                    "a": e.a.value.raw,
                    "b": e.b.value.raw,
                    "gap_percent": round(gap * 100, 1),
                    "a_page": e.a.evidence[0].page,
                    "b_page": e.b.evidence[0].page,
                }
                for gap, e in absurd[:5]
            ],
            "note": (
                "Two figures reported as contradictory while differing by orders of "
                "magnitude are not a disagreement between documents — they are two "
                "different quantities merged onto one predicate node, after which "
                "every pair inside that node reads as a conflict. This is the "
                "dominant source of false contradictions and it is a "
                "canonicalisation problem, not a comparator problem. The fix is a "
                "unit-compatibility check at merge time: two predicates whose values "
                "never share an order of magnitude are not the same predicate."
            ),
        },
        "period_attribution": {
            "resolved": resolved,
            "total": total,
            "rate": round(resolved / total, 4),
            "note": (
                "Period is the axis the comparator leans on hardest and the one most "
                "often missing from the page. Everything downstream depends on it, "
                "which is why the ambiguous bucket is the largest one."
            ),
        },
        "quarantine": {
            "total": len(layer.quarantined),
            "by_reason": by_reason,
            "sample": sample,
            "note": (
                "These are claims the grounding gate refused because the value was not "
                "literally present in the span cited. They are counted, not discarded, "
                "and they never entered the graph."
            ),
        },
        "unresolved_relations": {
            "ambiguous": counts.get("ambiguous", 0),
            "note": (
                "Pairs the comparator declined to decide. Most carry no resolved period "
                "on either side, which is a missing-evidence problem rather than a "
                "reasoning one â€” and reporting it as a conflict would have been the "
                "easy, wrong answer."
            ),
        },
    }


def _claim_json(claim: Claim, layer: KnowledgeLayer) -> dict:
    e = claim.evidence[0]
    doc = layer.document(str(e.document_id))
    return {
        "id": str(claim.id),
        "subject": claim.subject_raw,
        "predicate": claim.predicate_raw,
        "value": claim.value.raw,
        "normalised": str(claim.value.canonical_magnitude),
        "unit": _units(claim),
        "scale": _implied_scale(claim)[1],
        "scope": {
            "period": claim.scope.period.label
            or (str(claim.scope.period.start) if claim.scope.period.start else None),
            "period_start": str(claim.scope.period.start) if claim.scope.period.start else None,
            "period_end": str(claim.scope.period.end) if claim.scope.period.end else None,
            "convention": claim.scope.period.convention.value,
            "basis": claim.scope.basis.value,
            "segment": claim.scope.segment,
            "geography": claim.scope.geography,
            "accounting": claim.scope.accounting.value,
            "modality": claim.scope.modality.value,
        },
        "evidence": {
            "document": doc.filename if doc else str(e.document_id),
            "page": e.page,
            "char_start": e.char_start,
            "char_end": e.char_end,
            "quote": e.quote,
        },
    }


def _case_json(n: int, title: str, scored: Scored | None, layer: KnowledgeLayer) -> dict:
    if scored is None:
        return {"case": n, "title": title, "found": False,
                "note": "No pair in the current corpus satisfies this case's criteria."}
    e = scored.edge
    return {
        "case": n,
        "title": title,
        "found": True,
        "score": round(scored.score, 2),
        "selected_because": scored.why,
        "relation": e.verdict.relation.value,
        "axis": e.verdict.axis,
        "confidence": e.verdict.confidence,
        "cross_document": e.cross_document,
        "a": _claim_json(e.a, layer),
        "b": _claim_json(e.b, layer),
        "trace": e.verdict.trace,
    }


def main(show_only: bool) -> int:
    if show_only and OUT.exists():
        print(OUT.read_text(encoding="utf-8"))
        return 0

    print("building the knowledge layer from checkpoints...")
    layer = build()
    print(f"  {len(layer.claims):,} claims, {len(layer.edges):,} edges, "
          f"{len(layer.quarantined):,} quarantined, {len(layer.documents)} documents\n")

    scorers = [
        (1, "Corroborated across documents, expressed differently", score_case_1),
        (2, "A genuine contradiction", score_case_2),
        (3, "An apparent contradiction explained by context", score_case_3),
    ]

    cases = []
    for n, title, scorer in scorers:
        ranked = scorer(layer.edges)
        best = ranked[0] if ranked else None
        cases.append(_case_json(n, title, best, layer))

        print(f"case {n}: {title}")
        if best is None:
            print("  nothing in the corpus satisfies this yet\n")
            continue
        print(f"  score {best.score:.1f}, chosen from {len(ranked):,} candidates")
        print(f"  {best.edge.a.predicate_raw!r}: "
              f"{best.edge.a.value.raw} vs {best.edge.b.value.raw}")
        for reason in best.why:
            print(f"    - {reason}")
        # The runners-up matter: if the winner is far ahead, the case is solid;
        # if the field is flat, the choice is closer to arbitrary and says so.
        if len(ranked) > 1:
            print(f"  runner-up scored {ranked[1].score:.1f}")
        print()

    cases.append({"case": 4, **case_4(layer)})
    print("case 4: the failure case")
    c4 = cases[-1]
    print(f"  period attribution {c4['period_attribution']['rate'] * 100:.1f}%")
    print(f"  quarantined {c4['quarantine']['total']:,}")
    print(f"  ambiguous {c4['unresolved_relations']['ambiguous']:,}\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"cases": cases}, indent=2, default=str), encoding="utf-8")
    print(f"written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--show" in sys.argv))
