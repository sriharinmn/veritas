"""Compress the corpus checkpoints into the snapshot that ships with the repo.

    python -m scripts.build_snapshot

The README promises that `docker compose up` boots an already-populated app with
no API key, no GPU and no waiting. That promise is only true if the knowledge
layer is actually in the repository, and for a while it was not: `evals/corpus/`
was gitignored along with the run logs, so a fresh clone would have started
empty and every screen would have been blank. The pre-computed snapshot is the
single most important artefact here — it is what makes the project reviewable at
all — and it was being excluded by a line meant to keep logs out.

Gzipped rather than raw: 17 MB of JSONL becomes about 2 MB, which is the
difference between a clone somebody minds and one they do not. The loader reads
`.jsonl.gz` transparently and prefers a plain `.jsonl` beside it, so a reviewer
who runs their own extraction sees their own results rather than these.

Regenerate this whenever the corpus changes, and commit the result.
"""

from __future__ import annotations

import gzip
import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CORPUS = Path("evals/corpus")


def main() -> int:
    sources = sorted(CORPUS.glob("*.jsonl"))
    if not sources:
        print(f"no checkpoints in {CORPUS} — run `python -m scripts.corpus_run` first")
        return 1

    print(f"{'checkpoint':<52} {'raw':>10} {'gzipped':>10} {'ratio':>7}")
    print("-" * 84)
    raw_total = gz_total = 0

    for src in sources:
        dest = src.with_suffix(".jsonl.gz")
        with src.open("rb") as f_in, gzip.open(dest, "wb", compresslevel=9) as f_out:
            shutil.copyfileobj(f_in, f_out)
        raw, gz = src.stat().st_size, dest.stat().st_size
        raw_total += raw
        gz_total += gz
        print(f"{src.name:<52} {raw / 1e6:>9.1f}M {gz / 1e6:>9.1f}M {raw / gz:>6.1f}x")

    print("-" * 84)
    print(f"{'':<52} {raw_total / 1e6:>9.1f}M {gz_total / 1e6:>9.1f}M "
          f"{raw_total / max(gz_total, 1):>6.1f}x")
    print(f"\n{len(sources)} files written. Commit the .jsonl.gz — they are the "
          f"shipped knowledge layer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
