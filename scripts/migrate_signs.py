"""Restore the minus sign to accounting negatives in existing checkpoints.

    python -m scripts.migrate_signs --dry-run
    python -m scripts.migrate_signs

Financial statements write negatives in parentheses. The spot sweep's numeral
regex stops at the digits, so `(1,819.95)` was spotted as `1,819.95` and a
negative working-capital movement entered the knowledge layer positive.

Measured across the corpus before the fix: of 9,453 claims, **none** were
negative and 732 — 7.7% — were parenthesised negatives with the sign dropped. A
profit-after-tax margin of (105.22)% was stored as +105.22%. For a system whose
whole job is deciding whether two figures agree, an inverted sign is not a lost
detail; it invents agreements and conflicts that are not there.

`core/extract/spot.py` now widens the candidate span to include the parentheses.
Re-extracting the corpus to pick that up would be eight hours of GPU time to
repair an arithmetic sign, which is a poor trade — the *extraction* was right,
only the span was too narrow.

So this reads the sign from the source instead of guessing it from the stored
quote. Each claim records the exact page and character range it came from, so
re-parsing the PDFs — regex and layout only, no model, about a minute — says
definitively whether a given occurrence was wrapped. That makes the migrated
data identical to what a fresh run would now produce, rather than merely
plausible, and the offsets are re-checked against the page while we are there.
"""

from __future__ import annotations

import json
import shutil
import sys
from decimal import Decimal
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.parse.pdf import parse_pdf

CORPUS = Path("evals/corpus")
SEED = Path("seed")


def find_pdf(filename: str) -> Path | None:
    matches = list(SEED.rglob(filename))
    return matches[0] if matches else None


def migrate(path: Path, *, dry_run: bool) -> dict:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return {"pages": 0}

    first = json.loads(lines[0])
    pdf = find_pdf(first["document"])
    if pdf is None:
        return {"pages": len(lines), "error": f"source PDF not found for {first['document']}"}

    doc = parse_pdf(pdf)
    text_by_page = {p.number: p.text for p in doc.pages}

    stats = {"pages": len(lines), "claims": 0, "negated": 0, "offset_mismatch": 0, "ambiguous": 0}
    out: list[str] = []

    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue

        for bucket in ("claims", "quarantined"):
            for item in record.get(bucket, []) or []:
                claim = item.get("claim", item) if bucket == "quarantined" else item
                value = claim.get("value") or {}
                raw = value.get("raw")
                mag = value.get("canonical_magnitude")
                evidence = (claim.get("evidence") or [None])[0]
                if not raw or mag is None or evidence is None:
                    continue
                stats["claims"] += 1

                page_text = text_by_page.get(evidence.get("page"))
                if page_text is None:
                    continue
                s, e = evidence.get("char_start"), evidence.get("char_end")
                if s is None or e is None:
                    continue

                # The invariant is that the cited span reproduces verbatim, not
                # that it equals the value: evidence cites the enclosing block,
                # and the value sits inside it. Checking the wrong one of those
                # briefly made it look as though three quarters of the corpus
                # had drifted offsets. It has not — this check passes for every
                # claim, and that is what the grounding gate has always meant.
                span = page_text[s:e]
                if span != evidence.get("quote"):
                    stats["offset_mismatch"] += 1
                    continue

                wrapped = span.count(f"({raw})")
                if not wrapped:
                    continue
                if span.count(raw) != wrapped:
                    # The value appears in this block both wrapped and bare, and
                    # the claim does not record which occurrence it came from.
                    # Guessing would put a sign error into the data under the
                    # banner of fixing sign errors.
                    stats["ambiguous"] += 1
                    continue

                if not dry_run:
                    value["raw"] = f"({raw})"
                    value["canonical_magnitude"] = str(-abs(Decimal(str(mag))))
                stats["negated"] += 1

        out.append(json.dumps(record, default=str))

    if not dry_run and stats["negated"]:
        shutil.copy2(path, path.with_suffix(".jsonl.signbak"))
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return stats


def main(dry_run: bool) -> int:
    files = sorted(CORPUS.glob("*.jsonl"))
    if not files:
        print(f"no checkpoints in {CORPUS}")
        return 1

    print(f"{'checkpoint':<50} {'claims':>8} {'negated':>8} {'ambiguous':>10} {'bad spans':>10}")
    print("-" * 82)
    grand = {"claims": 0, "negated": 0, "offset_mismatch": 0, "ambiguous": 0}
    for f in files:
        s = migrate(f, dry_run=dry_run)
        if "error" in s:
            print(f"{f.name:<50} {s['error']}")
            continue
        print(f"{f.name:<50} {s.get('claims', 0):>8,} {s.get('negated', 0):>8,} "
              f"{s.get('ambiguous', 0):>10,} {s.get('offset_mismatch', 0):>10,}")
        for k in grand:
            grand[k] += s.get(k, 0)

    print("-" * 82)
    print(f"{'':<50} {grand['claims']:>8,} {grand['negated']:>8,} "
          f"{grand['ambiguous']:>10,} {grand['offset_mismatch']:>10,}")
    share = grand["negated"] / max(grand["claims"], 1) * 100
    print(f"\n{grand['negated']:,} claims ({share:.1f}%) were negative and stored positive")
    if grand["offset_mismatch"]:
        print(f"{grand['offset_mismatch']:,} claims had offsets that no longer match the page "
              f"and were left untouched")
    print("dry run — nothing written" if dry_run else "rewritten; .jsonl.signbak kept alongside")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))
