"""Run one PDF through every tier and check the result against a known answer.

    python -m scripts.tier_check                    # all three tiers, fileupload.pdf
    python -m scripts.tier_check --tier deterministic
    python -m scripts.tier_check --pdf seed/something.pdf

The shipped corpus is real and therefore unlabelled: when a tier gets something
wrong there, telling that apart from the document being unusual takes reading
the page. `fileupload.pdf` is built to have an answer key, so this can assert
rather than describe.

The headline assertion is the first one. "Rs. 7,225 crore" in a sentence on page
2 and "72,251" in a table cell under a "(Rs. in millions)" caption on page 3 are
the same fact written two ways, and the two strings have nothing in common —
different digits, different scale word, different page, one prose and one a
table cell. Text similarity calls them unrelated; an embedding of the sentences
calls them unrelated too. Both canonicalise to 7.225e10 base rupees, and the
comparator agrees them within the rounding four significant figures implies.

Each tier writes into its own throwaway directory, so the shipped corpus is
never touched and the three runs cannot contaminate each other.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import shutil
import sys
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.extract.deterministic import extract_page as extract_page_rules
from core.extract.gateway import build_gateway
from core.extract.llm import extract_page as extract_page_model
from core.extract.llm import read_document_context
from core.extract.semantic import extract_semantic_page, prose_blocks
from core.extract.spot import spot_document
from core.ground.verify import verify_all
from core.models import Relation, ValueKind
from core.parse.pdf import document_uuid, parse_pdf
from core.route import route_document
from core.settings import Tier
from core.store.checkpoints import build

SCRATCH = Path(".tier-check")
TIERS = {"groq": Tier.GROQ, "ollama": Tier.OLLAMA, "deterministic": Tier.DETERMINISTIC}

# Rs. 7,225 crore and 72,251 million, both expressed in base rupees.
CRORE = Decimal("72250000000")
MILLION = Decimal("72251000000")
LOSS = Decimal("1820000000")
MARGIN = Decimal("0.082")


def _near(a: Decimal, b: Decimal, tol: Decimal = Decimal("0.001")) -> bool:
    return abs(a - b) <= abs(b) * tol


def _magnitude(claim):
    return claim.value.canonical_magnitude


def _line(page: int, pdf: Path, doc, grounded, quarantined) -> str:
    """A checkpoint line in the exact format the corpus run and the API write."""
    return json.dumps(
        {
            "page": page,
            "document": pdf.name,
            "document_sha256": doc.sha256,
            "extracted_at": dt.datetime.now(dt.UTC).isoformat(),
            "claims": [c.model_dump(mode="json") for c in grounded],
            "quarantined": [
                {"claim": c.model_dump(mode="json"), "reason": r.reason, "detail": r.detail}
                for c, r in quarantined
            ],
        },
        default=str,
    )


async def run_tier(name: str, tier: Tier, pdf: Path, *, pages: int | None) -> dict:
    out = SCRATCH / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    started = time.time()
    doc = parse_pdf(pdf)
    spots = spot_document(doc)
    route_document(spots, force=tier)  # exercised for its trace, not its answer

    by_number = {p.number: p for p in doc.pages}
    order = spots.pages_by_density()
    if pages:
        order = order[:pages]

    run_id, doc_id = uuid4(), document_uuid(doc.sha256)
    gateway = build_gateway(tier) if tier is not Tier.DETERMINISTIC else None
    context = await read_document_context(doc, gateway) if gateway else None

    claims_total = quarantined_total = semantic_total = 0
    checkpoint = out / f"{pdf.stem}.jsonl"

    with checkpoint.open("w", encoding="utf-8") as sink:
        for number in order:
            page = by_number[number]
            if gateway is not None and context is not None:
                claims = await extract_page_model(
                    page, gateway, document_id=doc_id, context=context, run_id=run_id
                )
            else:
                claims = extract_page_rules(
                    page, document_id=doc_id, subject_raw=pdf.stem, run_id=run_id
                )
            grounded, quarantined, _ = verify_all(claims, {number: page})
            claims_total += len(grounded)
            quarantined_total += len(quarantined)
            sink.write(_line(number, pdf, doc, grounded, quarantined) + "\n")

        if gateway is not None:
            ranked = sorted(
                (p for p in doc.pages if prose_blocks(p)),
                key=lambda p: -sum(len(b.text) for b in prose_blocks(p)),
            )[:3]
            for page in ranked:
                facts, _refused = await extract_semantic_page(
                    page,
                    gateway,
                    document_id=doc_id,
                    run_id=run_id,
                    entity_hint=context.entity if context else None,
                )
                grounded, quarantined, _ = verify_all(facts, {page.number: page})
                if not grounded and not quarantined:
                    continue
                semantic_total += len(grounded)
                claims_total += len(grounded)
                quarantined_total += len(quarantined)
                sink.write(_line(page.number, pdf, doc, grounded, quarantined) + "\n")

    return {
        "tier": name,
        "seconds": round(time.time() - started, 1),
        "pages": len(order),
        "claims": claims_total,
        "quarantined": quarantined_total,
        "semantic": semantic_total,
        "layer": build(out),
        "published": context.published_on if context else None,
        "entity": context.entity if context else None,
    }


# ── the answer key ───────────────────────────────────────────────────────────


def check(result: dict) -> list[tuple[str, bool, str]]:
    layer = result["layer"]
    claims = layer.claims
    out: list[tuple[str, bool, str]] = []

    def shown(items, n=3):
        return ", ".join(f"{c.predicate_raw!r}={c.value.raw!r}" for c in items[:n]) or "none"

    # Matched on the printed token, not the magnitude: the two agree to within
    # 0.0014%, which is the whole point of the test and would make a
    # magnitude-only filter return both claims for both checks.
    crore = [
        c for c in claims
        if "7,225" in c.value.raw and _magnitude(c) is not None and _near(_magnitude(c), CRORE)
    ]
    million = [
        c for c in claims
        if "72,251" in c.value.raw and _magnitude(c) is not None and _near(_magnitude(c), MILLION)
    ]

    out.append((
        "'Rs. 7,225 crore' resolves to 7.225e10 base rupees",
        bool(crore),
        f"{len(crore)}: {shown(crore)}",
    ))
    out.append((
        "'72,251' under a millions caption resolves to 7.2251e10",
        bool(million),
        f"{len(million)}: {shown(million)}",
    ))

    same_node = [
        (a, b)
        for a in crore
        for b in million
        if a.predicate_id is not None and a.predicate_id == b.predicate_id
    ]
    out.append((
        "the two wordings land on ONE predicate node",
        bool(same_node),
        f"{same_node[0][0].predicate_raw!r} == {same_node[0][1].predicate_raw!r}"
        if same_node
        else f"crore={[c.predicate_raw for c in crore[:2]]} million={[c.predicate_raw for c in million[:2]]}",
    ))

    crore_ids = {str(c.id) for c in crore}
    million_ids = {str(c.id) for c in million}
    joined = [
        e
        for e in layer.edges
        if {str(e.a.id), str(e.b.id)} & crore_ids and {str(e.a.id), str(e.b.id)} & million_ids
    ]
    corroborating = [e for e in joined if e.verdict.relation is Relation.CORROBORATION]
    out.append((
        "the comparator calls them CORROBORATION",
        bool(corroborating),
        (corroborating[0].verdict.trace[-1][:110] if corroborating[0].verdict.trace else "agreed")
        if corroborating
        else f"{len(joined)} edge(s): " + (", ".join(sorted({e.verdict.relation.value for e in joined})) or "none"),
    ))

    losses = [c for c in claims if _magnitude(c) is not None and _near(abs(_magnitude(c)), LOSS)]
    out.append((
        "'(1,820)' is read as a negative",
        bool(losses) and all(_magnitude(c) < 0 for c in losses),
        ", ".join(f"{c.value.raw!r} -> {_magnitude(c):,}" for c in losses[:3]) or "not found",
    ))

    rates = [
        c
        for c in claims
        if c.value.kind is ValueKind.RATIO
        and _magnitude(c) is not None
        and _near(_magnitude(c), MARGIN, Decimal("0.01"))
    ]
    out.append((
        "'8.20%' and the bare '8.2' both canonicalise to 0.082",
        len(rates) >= 2,
        ", ".join(f"{c.value.raw!r} -> {_magnitude(c)}" for c in rates[:4]) or "none",
    ))

    published = result["published"]
    impossible = [
        c
        for c in claims
        if c.scope.vintage and c.scope.period and c.scope.period.end
        and c.scope.period.end > c.scope.vintage
    ]
    out.append((
        "no claim asserts a period its own document predates",
        not impossible,
        f"published {published or 'unknown'}; {len(impossible)} impossible period(s)",
    ))

    semantic = [c for c in claims if c.value.kind is ValueKind.TEXT]
    if result["tier"] == "deterministic":
        out.append((
            "no semantic facts on the rules tier (expected — rules cannot read prose)",
            not semantic,
            f"{len(semantic)} found",
        ))
    else:
        out.append((
            "at least one non-numeric fact",
            bool(semantic),
            ", ".join(f"{c.predicate_raw}={c.value.raw!r}"[:72] for c in semantic[:2]) or "none",
        ))

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", default="fileupload.pdf")
    ap.add_argument("--tier", action="append", choices=sorted(TIERS))
    ap.add_argument("--pages", type=int, default=None)
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"no such file: {pdf}   (run: python -m scripts.make_test_pdf)")
        return 1

    failures = 0
    for name in args.tier or list(TIERS):
        print(f"\n{'=' * 78}\n  {name.upper()}   {pdf}\n{'=' * 78}")
        try:
            result = asyncio.run(run_tier(name, TIERS[name], pdf, pages=args.pages))
        except Exception as e:
            print(f"  RUN FAILED   {type(e).__name__}: {str(e)[:200]}")
            failures += 1
            continue

        stats = result["layer"].stats()
        print(
            f"  {result['claims']} claims · {result['quarantined']} quarantined · "
            f"{result['semantic']} semantic · {result['pages']} pages · {result['seconds']}s"
            + (f" · entity {result['entity']!r}" if result["entity"] else "")
        )
        print(
            f"  ontology: {stats['entities']} entities, {stats['predicates']} predicates, "
            f"{stats['edges']} edges  {stats['relations']}"
        )
        print()
        for label, ok, detail in check(result):
            print(f"  [{'PASS' if ok else 'FAIL'}]  {label}")
            print(f"          {detail}")
            failures += not ok

    print(f"\n{'=' * 78}")
    print("all checks passed" if not failures else f"{failures} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
