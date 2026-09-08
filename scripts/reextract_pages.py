"""Re-extract chosen pages of the corpus in place, without redoing the rest.

    python -m scripts.reextract_pages --suspect --dry-run
    python -m scripts.reextract_pages --suspect
    python -m scripts.reextract_pages --checkpoint 03-imf-... --pages 45,48,49

A fix in the normalisation library does not reach the shipped corpus, because
the corpus is the *output* of a run that happened before the fix. Re-running all
146 pages is a night's work on the local GPU; the pages that actually carry a
wrong value are usually a handful.

`--suspect` finds them without needing to be told: a ratio whose printed token
carries a thousands separator is a revenue line that picked up a stray percent
sign from the sentence beside it. Genuine percentages do not have commas in them.

The rewrite is line-for-line. Each checkpoint is one JSON record per page, so a
re-extracted page replaces exactly one line and every other page keeps the ids,
the claims and the evidence it already had. Old claim ids on the touched pages
do not survive -- they are new claims -- so re-run `scripts.curate_cases` and
`scripts.build_snapshot` afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import shutil
import sys
from pathlib import Path
from uuid import UUID, uuid4

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.extract.gateway import build_gateway
from core.extract.llm import extract_page, read_document_context
from core.ground.verify import verify_all
from core.parse.pdf import document_uuid, parse_pdf
from core.settings import Tier
from core.store.checkpoints import CORPUS_DIR, _pdf_path
from scripts.corpus_run import thermal_guard


def suspect_pages(path: Path) -> tuple[set[int], int]:
    """Pages holding a ratio whose token carries a thousands separator."""
    pages: set[int] = set()
    claims = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        for claim in record.get("claims") or []:
            value = claim.get("value") or {}
            if value.get("kind") == "ratio" and "," in str(value.get("raw", "")):
                pages.add(record["page"])
                claims += 1
    return pages, claims


async def redo(path: Path, wanted: set[int], *, dry_run: bool) -> dict:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append(None)

    filename = next((r["document"] for r in records if r and r.get("document")), path.stem)
    pdf = _pdf_path(filename)
    if not pdf:
        return {"error": f"no PDF found for {filename}"}
    if dry_run:
        return {"pages": len(wanted), "before": 0, "after": 0, "pdf": pdf}

    doc = parse_pdf(Path(pdf))
    guard = thermal_guard(lambda m: print(m, flush=True))
    gateway = build_gateway(Tier.OLLAMA)
    context = await read_document_context(doc, gateway)
    by_number = {p.number: p for p in doc.pages}
    run_id = uuid4()
    doc_id = UUID(
        next(
            (c["evidence"][0]["document_id"] for r in records if r for c in r.get("claims") or []),
            str(document_uuid(doc.sha256)),
        )
    )

    before = after = 0
    for i, record in enumerate(records):
        if record is None or record.get("page") not in wanted:
            continue
        page = by_number.get(record["page"])
        if page is None:
            continue

        was = len(record.get("claims") or [])
        before += was
        claims = await extract_page(
            page, gateway, document_id=doc_id, context=context, run_id=run_id,
            # The same guard the overnight run uses, for the same reason: a
            # dense page is minutes of uninterrupted GPU load, and a check that
            # only runs between pages let the card sit at 86 C. Between batches
            # it fires roughly every forty seconds.
            on_batch=guard,
        )
        grounded, quarantined, _ = verify_all(claims, {page.number: page})
        after += len(grounded)

        record["claims"] = [c.model_dump(mode="json") for c in grounded]
        record["quarantined"] = [
            {"claim": c.model_dump(mode="json"), "reason": r.reason, "detail": r.detail}
            for c, r in quarantined
        ]
        record["extracted_at"] = dt.datetime.now(dt.UTC).isoformat()
        # Per page, not cumulative. The first version printed the running
        # total on the left and this page's count on the right, which read as a
        # catastrophic recall collapse and was nothing of the sort.
        print(f"    page {page.number:>4}  {was:>4} -> {len(grounded):>4} claims", flush=True)

    shutil.copy2(path, path.with_suffix(".jsonl.rebak"))
    path.write_text(
        "\n".join(json.dumps(r, default=str) if r is not None else lines[i]
                  for i, r in enumerate(records)) + "\n",
        encoding="utf-8",
    )
    return {"pages": len(wanted), "before": before, "after": after, "pdf": pdf}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--suspect", action="store_true", help="find the pages automatically")
    ap.add_argument("--checkpoint", help="one checkpoint stem, with --pages")
    ap.add_argument("--pages", help="comma-separated page numbers")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets: dict[Path, set[int]] = {}
    if args.checkpoint:
        path = CORPUS_DIR / f"{args.checkpoint}.jsonl"
        targets[path] = {int(x) for x in (args.pages or "").split(",") if x.strip()}
    else:
        for path in sorted(CORPUS_DIR.glob("*.jsonl")):
            pages, claims = suspect_pages(path)
            if pages:
                targets[path] = pages
                print(f"{path.name:<48} {len(pages):>3} pages, {claims:>4} suspect claims")

    if not targets:
        print("nothing to re-extract")
        return 0

    total = sum(len(v) for v in targets.values())
    print(f"\n{total} pages to re-extract" + (" (dry run)" if args.dry_run else " on ollama"))

    for path, pages in targets.items():
        print(f"\n  {path.name}  pages {sorted(pages)}", flush=True)
        result = asyncio.run(redo(path, pages, dry_run=args.dry_run))
        if "error" in result:
            print(f"    SKIPPED — {result['error']}")
            continue
        if not args.dry_run:
            print(f"    {result['before']} claims replaced by {result['after']}")

    if not args.dry_run:
        print("\nrewritten; .jsonl.rebak kept alongside")
        print("now run:  python -m scripts.curate_cases  &&  python -m scripts.build_snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
