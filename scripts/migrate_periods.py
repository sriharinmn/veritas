"""Clear periods that the source document could not have reported. Idempotent.

    python -m scripts.migrate_periods --dry-run
    python -m scripts.migrate_periods

A prospectus dated 25 April 2022 does not contain actuals for the year ending 31
March 2024. The pipeline stored both dates on every claim and never compared
them, so 811 of the 5,808 claims carrying both — 14.0% — asserted a period their
own document predated.

That is not a harmless label. Period is the axis the comparator leans on hardest:
it decides whether two figures are comparable at all, and it is the one thing a
contradiction requires on both sides. One of these impossible periods is what the
curator selected as case 3, where a reader was told two values differ "because
the basis differs" while the real explanation was that one of the periods could
not be true.

The value, the page and the quote are all still correct, so the period is
cleared rather than the claim dropped, and the written label is kept so the
mistake stays visible rather than being tidied away.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CORPUS = Path("evals/corpus")


def _date(value: object) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def migrate(path: Path, *, dry_run: bool) -> dict:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out: list[str] = []
    stats = {"claims": 0, "with_both_dates": 0, "cleared": 0}

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
                period = scope.get("period") or {}
                if not isinstance(period, dict):
                    continue
                stats["claims"] += 1

                end = _date(period.get("end"))
                published = _date(scope.get("vintage"))
                if end is None or published is None:
                    continue
                stats["with_both_dates"] += 1

                if end <= published:
                    continue

                stats["cleared"] += 1
                if not dry_run:
                    # Keep the label and the convention; drop only the dates
                    # that cannot be true.
                    period["start"] = None
                    period["end"] = None

        out.append(json.dumps(record, default=str))

    if not dry_run and stats["cleared"]:
        shutil.copy2(path, path.with_suffix(".jsonl.periodbak"))
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return stats


def main(dry_run: bool) -> int:
    files = sorted(CORPUS.glob("*.jsonl"))
    if not files:
        print(f"no checkpoints in {CORPUS}")
        return 1

    print(f"{'checkpoint':<50} {'claims':>8} {'datable':>9} {'cleared':>8}")
    print("-" * 80)
    grand = {"claims": 0, "with_both_dates": 0, "cleared": 0}
    for f in files:
        s = migrate(f, dry_run=dry_run)
        print(f"{f.name:<50} {s['claims']:>8,} {s['with_both_dates']:>9,} {s['cleared']:>8,}")
        for k in grand:
            grand[k] += s[k]

    print("-" * 80)
    print(f"{'':<50} {grand['claims']:>8,} {grand['with_both_dates']:>9,} {grand['cleared']:>8,}")
    share = grand["cleared"] / max(grand["with_both_dates"], 1) * 100
    print(f"\n{grand['cleared']:,} of {grand['with_both_dates']:,} claims carrying both dates "
          f"({share:.1f}%) claimed a period their document predated")
    print("dry run — nothing written" if dry_run else "rewritten; .jsonl.periodbak kept alongside")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))
