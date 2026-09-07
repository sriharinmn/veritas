"""Rewrite checkpoint document ids to be content-derived. One-off, idempotent.

Kept in the repository rather than run and deleted, because the bug it repairs is
worth being able to point at.

`corpus_run` minted `uuid4()` for each document at the start of each *run*. That
is fine until a run is interrupted and resumed — which is the whole point of the
checkpoints — at which time the same PDF acquires a second identity. Pages 68 and
86 of one annual report then compare as though they came from two different
filings, `cross_document` reports true for a within-document pair, and half the
claims cannot resolve their own document's name.

Nothing about the extraction was wrong, so re-running 125 pages of GPU work to
fix an identifier would be wasteful. The claims are correct; only their document
pointer is not. Each checkpoint line already carries `document_sha256`, so the
correct id is recoverable without reopening a single PDF.

    python -m scripts.migrate_document_ids --dry-run   # report, change nothing
    python -m scripts.migrate_document_ids             # rewrite, keeping backups
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.parse.pdf import document_uuid

CORPUS = Path("evals/corpus")


def _rewrite_claim(claim: dict, correct: str) -> int:
    changed = 0
    for ev in claim.get("evidence", []) or []:
        if ev.get("document_id") != correct:
            ev["document_id"] = correct
            changed += 1
    return changed


def migrate(path: Path, *, dry_run: bool) -> tuple[int, int, set[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    changed_claims = 0
    seen_ids: set[str] = set()

    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # A torn final line after a kill. Preserve it untouched rather than
            # dropping it: the loader already tolerates it, and silently
            # discarding data during a migration is how migrations earn their
            # reputation.
            out.append(line)
            continue

        sha = record.get("document_sha256")
        if not sha:
            out.append(line)
            continue
        correct = str(document_uuid(sha))

        for claim in record.get("claims", []) or []:
            for ev in claim.get("evidence", []) or []:
                if ev.get("document_id"):
                    seen_ids.add(ev["document_id"])
            changed_claims += min(_rewrite_claim(claim, correct), 1)
        for q in record.get("quarantined", []) or []:
            claim = q.get("claim") or {}
            changed_claims += min(_rewrite_claim(claim, correct), 1)

        out.append(json.dumps(record, default=str))

    if not dry_run and changed_claims:
        backup = path.with_suffix(".jsonl.bak")
        shutil.copy2(path, backup)
        path.write_text("\n".join(out) + "\n", encoding="utf-8")

    return len(lines), changed_claims, seen_ids


def main(dry_run: bool) -> int:
    files = sorted(CORPUS.glob("*.jsonl"))
    if not files:
        print(f"no checkpoints in {CORPUS}")
        return 1

    print(f"{'checkpoint':<52} {'pages':>6} {'claims fixed':>13} {'ids before':>11}")
    print("-" * 86)
    totals = defaultdict(int)
    for f in files:
        pages, changed, ids = migrate(f, dry_run=dry_run)
        print(f"{f.name:<52} {pages:>6} {changed:>13,} {len(ids):>11}")
        totals["pages"] += pages
        totals["claims"] += changed
        if len(ids) > 1:
            print(f"{'':>52} ^ this document had {len(ids)} identities")

    print("-" * 86)
    print(f"{'':<52} {totals['pages']:>6} {totals['claims']:>13,}")
    if dry_run:
        print("\ndry run — nothing was written")
    elif totals["claims"]:
        print("\nrewritten; .jsonl.bak kept alongside each file")
    else:
        print("\nalready consistent — nothing to do")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))
