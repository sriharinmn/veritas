"""Re-cut every claim's evidence quote to a sentence. Idempotent, no model.

    python -m scripts.migrate_evidence --dry-run
    python -m scripts.migrate_evidence

Claims quoted the physical line their value sat on. For a table row that is
right -- the row is the unit of meaning -- but in prose a line break falls
wherever the typesetter put it, and the citation then reads like a parsing
failure:

    2021 and Rs.48,105.30 million for nine months period ended December 31,
    2021, while during the same period, (i)

That is a verbatim quote of a real line. A reviewer looking at it concluded the
extraction was corrupted, which is a fair reading: nothing about it says "this
sentence continues above". `core/parse/pdf.evidence_span` now widens prose to
sentence boundaries at extraction time, and this applies the same rule to the
claims already on disk.

The value, the page and the bounding boxes do not move. Only the quoted span
grows, and it grows to text that was always on the page -- so the grounding
invariant holds before and after, and every rewritten quote is checked against
it here rather than assumed.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.parse.pdf import evidence_span, parse_pdf
from core.store.checkpoints import CORPUS_DIR, _pdf_path


def _widen(page, evidence: dict, value_raw: str) -> bool:
    """Re-cut one evidence row. True when it changed and stayed grounded."""
    start, end = evidence.get("char_start"), evidence.get("char_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if not (0 <= start < end <= len(page.text)):
        return False
    if page.text[start:end] != evidence.get("quote"):
        # The stored span no longer matches the page — a different parse, or a
        # checkpoint from an older layout pass. Left alone rather than guessed
        # at: a wrong quote is worse than an ugly one.
        return False

    inner = page.text.find(value_raw, start, end)
    if inner == -1:
        inner, inner_end = start, end
    else:
        inner_end = inner + len(value_raw)

    block = next(
        (b for b in page.blocks if b.char_start <= inner < b.char_end),
        None,
    )
    lo, hi = evidence_span(page, block, inner, inner_end)
    if (lo, hi) == (start, end):
        return False

    quote = page.text[lo:hi]
    if value_raw and value_raw not in quote:
        return False  # never trade a grounded quote for a wider one

    evidence["char_start"], evidence["char_end"], evidence["quote"] = lo, hi, quote
    return True


def migrate(path: Path, *, dry_run: bool) -> dict:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    records, out = [], []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append(None)

    filename = next((r["document"] for r in records if r and r.get("document")), path.stem)
    pdf = _pdf_path(filename)
    if not pdf:
        return {"error": f"no PDF for {filename}"}

    doc = parse_pdf(Path(pdf))
    by_number = {p.number: p for p in doc.pages}
    stats = {"claims": 0, "widened": 0, "pages": 0}

    for record in records:
        if record is None:
            continue
        page = by_number.get(record.get("page"))
        if page is None:
            continue
        stats["pages"] += 1
        for bucket in ("claims", "quarantined"):
            for item in record.get(bucket) or []:
                claim = item.get("claim", item) if bucket == "quarantined" else item
                raw = (claim.get("value") or {}).get("raw") or ""
                for evidence in claim.get("evidence") or []:
                    if evidence.get("kind") == "inherited_context":
                        continue  # a document-level statement, not a span on this page
                    stats["claims"] += 1
                    if _widen(page, evidence, raw):
                        stats["widened"] += 1

    if not dry_run and stats["widened"]:
        shutil.copy2(path, path.with_suffix(".jsonl.evbak"))
        path.write_text(
            "\n".join(
                json.dumps(r, default=str) if r is not None else lines[i]
                for i, r in enumerate(records)
            )
            + "\n",
            encoding="utf-8",
        )
    return stats


def main(dry_run: bool) -> int:
    files = sorted(CORPUS_DIR.glob("*.jsonl"))
    if not files:
        print(f"no checkpoints in {CORPUS_DIR}")
        return 1

    print(f"{'checkpoint':<50} {'evidence':>9} {'widened':>9}")
    print("-" * 72)
    total = {"claims": 0, "widened": 0}
    for f in files:
        s = migrate(f, dry_run=dry_run)
        if "error" in s:
            print(f"{f.name:<50} SKIPPED — {s['error']}")
            continue
        print(f"{f.name:<50} {s['claims']:>9,} {s['widened']:>9,}")
        total["claims"] += s["claims"]
        total["widened"] += s["widened"]

    print("-" * 72)
    share = total["widened"] / max(total["claims"], 1) * 100
    print(f"{'':<50} {total['claims']:>9,} {total['widened']:>9,}   ({share:.1f}%)")
    print("dry run — nothing written" if dry_run else "rewritten; .jsonl.evbak kept alongside")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))
