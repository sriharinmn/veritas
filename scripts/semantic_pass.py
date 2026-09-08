"""Add non-numeric facts to documents that have already been extracted.

    python -m scripts.semantic_pass                    # every seed document
    python -m scripts.semantic_pass --pages 5          # prose pages per document
    python -m scripts.semantic_pass seed/delhivery/*.pdf

The numeric corpus took the better part of a day on a laptop GPU. Re-running it
to add a second kind of fact would be an absurd trade, and it is not necessary:
semantic facts come from a different pass over different pages, and checkpoints
are append-only. So this adds a layer rather than rebuilding one.

Pages are chosen by how much *prose* they carry, not by candidate density.
Those are close to opposite orderings — a statement of profit and loss is the
densest page in a filing and contains no semantic facts at all, while the
auditor's report contains no figures worth extracting and every fact about who
signed it, under what standard, for which entity.

Runs on Ollama by default. The work is low-value-per-token and there is a lot of
it, which is exactly the shape the local tier exists for; Groq's daily cap would
be gone in one document.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.extract.gateway import build_gateway
from core.extract.llm import read_document_context
from core.extract.semantic import SEMANTIC_PROMPT_VERSION, extract_semantic_page, prose_blocks
from core.ground.verify import verify_all
from core.parse.pdf import document_uuid, parse_pdf
from core.settings import Tier
from core.store.checkpoints import CORPUS_DIR

DEFAULT_PAGES = 6

DOCS = [
    "seed/delhivery/01-delhivery-prospectus-2022-excerpt.pdf",
    "seed/delhivery/02-delhivery-annual-report-fy24-excerpt.pdf",
    "seed/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf",
    "seed/india-macroeconomy/01-india-economic-survey-2024-25-excerpt.pdf",
    "seed/india-macroeconomy/02-rbi-annual-report-2024-25-excerpt.pdf",
    "seed/india-macroeconomy/03-imf-india-2025-article-iv-excerpt.pdf",
]


def strip_semantic(checkpoint: Path) -> int:
    """Remove every semantic claim, leaving the numeric extraction untouched.

    For re-running after the pass itself improves. The numeric claims cost a day
    of GPU time and must not be disturbed; the semantic ones cost minutes and
    are identifiable by their prompt version, so they can be lifted out cleanly.
    A line left with no claims at all is dropped rather than written empty.
    """
    if not checkpoint.exists():
        return 0

    kept_lines: list[str] = []
    removed = 0
    for line in checkpoint.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            kept_lines.append(line)
            continue

        claims = record.get("claims") or []
        numeric = [
            c
            for c in claims
            if (c.get("provenance") or {}).get("prompt_version") != SEMANTIC_PROMPT_VERSION
        ]
        removed += len(claims) - len(numeric)
        if not numeric and not record.get("quarantined"):
            continue
        record["claims"] = numeric
        kept_lines.append(json.dumps(record, default=str))

    if removed:
        checkpoint.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
    return removed


def already_done(checkpoint: Path) -> set[int]:
    """Pages that already carry semantic claims, so a re-run is idempotent."""
    if not checkpoint.exists():
        return set()
    done: set[int] = set()
    for line in checkpoint.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        for claim in record.get("claims") or []:
            provenance = claim.get("provenance") or {}
            if provenance.get("prompt_version") == SEMANTIC_PROMPT_VERSION:
                done.add(record.get("page"))
                break
    return done


async def enrich(path: str, gateway, *, pages: int, redo: bool = False) -> dict:
    src = Path(path)
    if not src.exists():
        return {"error": f"missing {path}"}

    doc = parse_pdf(src)
    checkpoint = CORPUS_DIR / f"{src.stem}.jsonl"
    if redo:
        dropped = strip_semantic(checkpoint)
        if dropped:
            print(f"    removed {dropped} semantic claims from an earlier pass")
    skip = already_done(checkpoint)

    ranked = sorted(
        (p for p in doc.pages if prose_blocks(p)),
        key=lambda p: -sum(len(b.text) for b in prose_blocks(p)),
    )
    todo = [p for p in ranked if p.number not in skip][:pages]

    print(f"\n  {src.name}")
    print(f"    {len(ranked)} pages carry prose · {len(skip)} already enriched · "
          f"{len(todo)} to do")
    if not todo:
        return {"facts": 0, "refused": 0, "pages": 0}

    context = await read_document_context(doc, gateway)
    doc_id = document_uuid(doc.sha256)
    run_id = uuid4()
    totals = {"facts": 0, "refused": 0, "pages": 0, "quarantined": 0}

    for page in todo:
        try:
            facts, refused = await extract_semantic_page(
                page, gateway, document_id=doc_id, run_id=run_id,
                entity_hint=context.entity,
            )
        except Exception as e:  # noqa: BLE001 — one page must not end the run
            print(f"    p{page.number:<4} FAILED {type(e).__name__}: {str(e)[:70]}")
            continue

        grounded, quarantined, _ = verify_all(facts, {page.number: page})
        if grounded or quarantined:
            with checkpoint.open("a", encoding="utf-8") as sink:
                sink.write(
                    json.dumps(
                        {
                            "page": page.number,
                            "document": src.name,
                            "document_sha256": doc.sha256,
                            "extracted_at": datetime.now(UTC).isoformat(),
                            "claims": [c.model_dump(mode="json") for c in grounded],
                            "quarantined": [
                                {"claim": c.model_dump(mode="json"),
                                 "reason": r.reason, "detail": r.detail}
                                for c, r in quarantined
                            ],
                        },
                        default=str,
                    )
                    + "\n"
                )
        totals["facts"] += len(grounded)
        totals["refused"] += refused
        totals["quarantined"] += len(quarantined)
        totals["pages"] += 1
        print(f"    p{page.number:<4} {len(grounded):3} facts  {refused:2} refused as paraphrase")

    print(f"    → {totals['facts']} semantic facts, {totals['refused']} refused")
    return totals


async def main(paths: list[str], pages: int, redo: bool = False) -> int:
    gateway = build_gateway(Tier.OLLAMA)
    print(f"semantic pass · {gateway.model} · {len(paths)} documents · {pages} pages each")

    grand = {"facts": 0, "refused": 0, "pages": 0, "quarantined": 0}
    for path in paths:
        result = await enrich(path, gateway, pages=pages, redo=redo)
        if "error" in result:
            print(f"  {result['error']}")
            continue
        for k in grand:
            grand[k] += result.get(k, 0)

    total = grand["facts"] + grand["refused"]
    share = grand["refused"] / total * 100 if total else 0
    print(f"\n  TOTAL {grand['facts']:,} semantic facts over {grand['pages']} pages")
    print(f"  {grand['refused']:,} refused as paraphrase ({share:.1f}% of what the model "
          f"proposed was not verbatim on the page)")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    page_budget = DEFAULT_PAGES
    if "--pages" in sys.argv:
        i = sys.argv.index("--pages")
        page_budget = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) else DEFAULT_PAGES
        args = [a for a in args if a != str(page_budget)]
    raise SystemExit(asyncio.run(main(args or DOCS, page_budget, "--redo" in sys.argv)))
