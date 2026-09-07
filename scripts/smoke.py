"""End-to-end smoke run with no model and no network.

Parses a real document, sweeps it for candidates, extracts claims with the
rule-based tier, runs every claim through the grounding gate, and prints the
conversion funnel that the eval harness will later formalise.

    python -m scripts.smoke seed/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf

The funnel is the point. Because the spot sweep is exhaustive over numerals by
construction, every number in the document is accounted for exactly once, and
the bucket that does not add up is measurable recall loss — on any document,
including one a grader uploads, with no hand-written labels.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from uuid import uuid4

# Windows consoles still default to cp1252, which cannot encode the box-drawing
# and currency characters these documents are full of. Without this the script
# dies on its own output rather than on anything to do with the pipeline.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.extract.deterministic import extract_document
from core.extract.spot import spot_document
from core.ground.verify import verify_all
from core.parse.pdf import parse_pdf

DEFAULT = "seed/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf"


def main(path: str) -> int:
    p = Path(path)
    print(f"\n  {p.name}")
    print("  " + "─" * 68)

    t = time.perf_counter()
    doc = parse_pdf(p)
    t_parse = time.perf_counter() - t

    t = time.perf_counter()
    spots = spot_document(doc)
    t_spot = time.perf_counter() - t

    t = time.perf_counter()
    claims = extract_document(
        doc,
        document_id=uuid4(),
        subject_raw=doc.metadata.get("title") or p.stem,
        run_id=uuid4(),
    )
    t_extract = time.perf_counter() - t

    pages = {pg.number: pg for pg in doc.pages}
    grounded, quarantined, report = verify_all(claims, pages)

    print(f"  {doc.page_count} pages · {doc.total_chars:,} characters · "
          f"{len(doc.scanned_pages)} image-only")
    print(f"  parse {t_parse:.1f}s · spot {t_spot:.2f}s · extract {t_extract:.2f}s")
    print()
    print("  funnel")
    print(f"    {spots.total:6,}  candidates spotted        (exhaustive by construction)")
    print(f"    {spots.total - spots.signal:6,}  hinted as references      (note/page/clause numbers)")
    print(f"    {len(claims):6,}  became claims")
    print(f"    {len(grounded):6,}  survived the grounding gate")
    print(f"    {len(quarantined):6,}  quarantined")
    if spots.signal:
        print(f"\n    conversion {len(grounded) / spots.signal:.1%} of signal candidates")
    print(f"    {report.summary()}")

    if report.by_reason:
        print("\n  quarantine reasons")
        for reason, count in sorted(report.by_reason.items(), key=lambda kv: -kv[1]):
            print(f"    {count:6,}  {reason}")

    print("\n  sample of grounded claims")
    for c in grounded[:6]:
        ev = c.evidence[0]
        print(f"    p{ev.page:<3} {c.predicate_raw[:34]:34} = {c.value.raw:>12} "
              f"[{c.value.kind}] {c.scope.period.label or '—'}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT))
