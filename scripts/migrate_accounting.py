"""Strip accounting standards that the source document never actually stated.

    python -m scripts.migrate_accounting --dry-run
    python -m scripts.migrate_accounting

The document-context pass asks a model to report the conventions a document
declares, and its prompt says plainly: use null where the document does not tell
you, do not infer. It inferred anyway. Measured across the corpus:

    Delhivery annual report   "Ind AS" x32   labelled IND_AS   correct
    Economic Survey           no mention     labelled IND_AS   invented
    RBI Annual Report         no mention     labelled IND_AS   invented
    IMF Article IV            "IFRS" x3      labelled IFRS     not a basis of
                                                               preparation

`accounting` is a comparator axis, so a document-level guess is not a cosmetic
error. Every pair crossing two documents with different labels differs on
exactly one axis, which is the definition of RECONCILED â€” so all 1,238
IMF-to-Economic-Survey pairs were reported as reconciled, each explained by "the
two statements are prepared under different accounting standards". A staff
report's GDP growth is not prepared under IFRS. The explanation was fabricated,
and it also made a genuine contradiction between those two documents impossible
to express.

`core/extract/llm.py` now grounds the answer against the front matter it showed
the model. This applies the same rule to what is already on disk: re-read each
document's first pages and keep the standard only where a marker for it is
actually present. Rule-based, not a list of filenames â€” a fix that only works on
this corpus is not a fix.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.extract.llm import accounting_evidenced_in
from core.models import Accounting
from core.parse.pdf import parse_pdf

CORPUS = Path("evals/corpus")
SEED = Path("seed")
FRONT_PAGES = 4


def evidenced_standards(pdf: Path) -> set[str]:
    """Which standards this document asserts as its basis of preparation.

    The whole document, not just the front matter: these are excerpts that do
    not start at page 1, and a filing states its basis in the accounting-policies
    note. The proximity rule is what keeps that from over-matching -- three
    incidental mentions of IFRS in a macroeconomic staff report are not a basis
    of preparation, and would pass a naive substring test.
    """
    doc = parse_pdf(pdf, detect_tables=False)
    whole = "\n".join(p.text for p in doc.pages)
    return {s.value for s in accounting_evidenced_in(whole)}


def migrate(path: Path, *, dry_run: bool) -> dict:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return {}

    first = json.loads(lines[0])
    matches = list(SEED.rglob(first["document"]))
    if not matches:
        return {"error": f"source PDF not found for {first['document']}"}

    evidenced = evidenced_standards(matches[0])
    seen: Counter[str] = Counter()
    cleared = 0
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
                scope = claim.get("scope") or {}
                current = scope.get("accounting")
                if not current or current == Accounting.UNKNOWN.value:
                    continue
                seen[current] += 1
                if current not in evidenced:
                    cleared += 1
                    if not dry_run:
                        scope["accounting"] = Accounting.UNKNOWN.value
        out.append(json.dumps(record, default=str))

    if not dry_run and cleared:
        shutil.copy2(path, path.with_suffix(".jsonl.acctbak"))
        path.write_text("\n".join(out) + "\n", encoding="utf-8")

    return {"labelled": sum(seen.values()), "cleared": cleared,
            "evidenced": sorted(evidenced), "was": dict(seen)}


def main(dry_run: bool) -> int:
    files = sorted(CORPUS.glob("*.jsonl"))
    if not files:
        print(f"no checkpoints in {CORPUS}")
        return 1

    print(f"{'checkpoint':<50} {'labelled':>9} {'cleared':>8}  basis of preparation stated")
    print("-" * 96)
    total_labelled = total_cleared = 0
    for f in files:
        s = migrate(f, dry_run=dry_run)
        if not s:
            continue
        if "error" in s:
            print(f"{f.name:<50} {s['error']}")
            continue
        evidence = ", ".join(s["evidenced"]) or "none â€” standard not stated"
        print(f"{f.name:<50} {s['labelled']:>9,} {s['cleared']:>8,}  {evidence}")
        total_labelled += s["labelled"]
        total_cleared += s["cleared"]

    print("-" * 96)
    print(f"{'':<50} {total_labelled:>9,} {total_cleared:>8,}")
    share = total_cleared / max(total_labelled, 1) * 100
    print(f"\n{total_cleared:,} of {total_labelled:,} accounting labels ({share:.1f}%) "
          f"were not supported by the document and are now UNKNOWN")
    print("dry run â€” nothing written" if dry_run else "rewritten; .jsonl.acctbak kept alongside")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))
