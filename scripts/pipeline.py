"""The whole pipeline, across several documents, with no model and no network.

    python -m scripts.pipeline seed/delhivery/*.pdf

parse → spot → extract → ground → canonicalise → pair → compare

Everything here is deterministic. If corroborations, contradictions and
context-reconciled pairs come out of this, they came out of the data model
rather than out of a prompt — which is the claim the whole design rests on.
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.canon.assign import canonicalise
from core.canon.embed import HashingEmbedder
from core.canon.registry import Registry
from core.extract.deterministic import extract_document
from core.extract.spot import spot_document
from core.ground.verify import verify_all
from core.link.compare import compare
from core.link.pairing import generate_pairs
from core.models import Relation
from core.parse.pdf import parse_pdf


def main(paths: list[str]) -> int:
    embedder = HashingEmbedder()
    entities = Registry("entity", embedder)
    predicates = Registry("predicate", embedder)

    all_claims = []
    all_pages: dict[str, dict] = {}
    t0 = time.perf_counter()

    print()
    for path in paths:
        p = Path(path)
        doc = parse_pdf(p)
        spots = spot_document(doc)
        doc_id = uuid4()

        # Entity naming is document-derived, never hard-coded: the PDF's own
        # title metadata, falling back to the filename. Crude, and precisely the
        # kind of thing the document-context pass replaces once a model is
        # available.
        subject = (doc.metadata.get("title") or p.stem).strip()[:60]

        claims = extract_document(doc, document_id=doc_id, subject_raw=subject)
        pages = {pg.number: pg for pg in doc.pages}
        grounded, quarantined, report = verify_all(claims, pages)
        all_pages[str(doc_id)] = pages
        all_claims.extend(grounded)

        print(f"  {p.name[:46]:46} {doc.page_count:3}pp  "
              f"{spots.total:5,} spotted → {len(grounded):5,} grounded  "
              f"({report.pass_rate:.0%} pass)")

    canon = canonicalise(all_claims, entities, predicates)
    pairs, pair_report = generate_pairs(canon.claims)

    verdicts = [compare(pr.a, pr.b) for pr in pairs]
    counts = Counter(v.relation for v in verdicts)

    print()
    print(f"  {len(all_claims):,} grounded claims across {len(paths)} documents")
    print(f"  ontology: {entities.stats()['nodes']:,} entities, "
          f"{predicates.stats()['nodes']:,} predicates, "
          f"{len(predicates.pending_review()):,} awaiting adjudication")
    print(f"  {pair_report.summary()}")
    print()
    print("  relations")
    for rel in Relation:
        print(f"    {counts.get(rel, 0):6,}  {rel.value}")

    for rel, title in (
        (Relation.CORROBORATION, "corroboration"),
        (Relation.CONTRADICTION, "contradiction"),
        (Relation.RECONCILED, "reconciled by context"),
    ):
        examples = [(p, v) for p, v in zip(pairs, verdicts, strict=True) if v.relation is rel]
        cross = [e for e in examples if e[0].cross_document] or examples
        if not cross:
            continue
        pr, v = cross[0]
        print(f"\n  ── {title} ".ljust(72, "─"))
        print(f"    {pr.a.predicate_raw[:44]:44} = {pr.a.value.raw:>12}"
              f"  p{pr.a.evidence[0].page} {pr.a.scope.period.label or '—'}")
        print(f"    {pr.b.predicate_raw[:44]:44} = {pr.b.value.raw:>12}"
              f"  p{pr.b.evidence[0].page} {pr.b.scope.period.label or '—'}")
        if v.axis:
            print(f"    axis: {v.axis}")
        for line in v.trace[-3:]:
            print(f"    · {line}")

    print(f"\n  {time.perf_counter() - t0:.1f}s total\n")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:] or sorted(str(p) for p in Path("seed/delhivery").glob("*.pdf"))
    raise SystemExit(main(args))
