"""Record which tier read each fact, not just which model. Idempotent.

    python -m scripts.migrate_provenance --dry-run
    python -m scripts.migrate_provenance

The numeric pass recorded `provenance.extractor` as the bare model name —
"qwen3:8b" — while the semantic pass recorded "ollama:qwen3:8b". Two formats for
one field, so the interface could not show a consistent answer to "where did
this come from".

That question matters more than it looks. The three tiers are not equally good:
rules alone recover about 47% of what a model finds on the same pages. A reader
comparing two facts should be able to see which tier produced each, because it
changes what a *missing* fact means. A model name on its own does not answer it.

The tier is recoverable without re-extracting anything: a model with a slash in
it is a hosted Groq model, a bare tag like "qwen3:8b" is an Ollama one, and
"deterministic" is already unambiguous.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CORPUS = Path("evals/corpus")
KNOWN_TIERS = ("groq", "ollama", "deterministic")


def normalise(extractor: str | None) -> str | None:
    """`model` → `tier:model`, leaving anything already qualified alone."""
    if not extractor:
        return extractor
    if extractor.split(":", 1)[0] in KNOWN_TIERS:
        return extractor
    if extractor == "deterministic":
        return extractor
    # "openai/gpt-oss-120b" is a hosted model; "qwen3:8b" is a local tag.
    return f"{'groq' if '/' in extractor else 'ollama'}:{extractor}"


def migrate(path: Path, *, dry_run: bool) -> Counter:
    counts: Counter = Counter()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out: list[str] = []
    changed = 0

    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        for bucket in ("claims", "quarantined"):
            for item in record.get(bucket, []) or []:
                claim = item.get("claim", item) if bucket == "quarantined" else item
                prov = claim.get("provenance")
                if not prov:
                    continue
                before = prov.get("extractor")
                after = normalise(before)
                counts[after] += 1
                if after != before:
                    changed += 1
                    if not dry_run:
                        prov["extractor"] = after
        out.append(json.dumps(record, default=str))

    if not dry_run and changed:
        shutil.copy2(path, path.with_suffix(".jsonl.provbak"))
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    counts["_changed"] = changed
    return counts


def main(dry_run: bool) -> int:
    files = sorted(CORPUS.glob("*.jsonl"))
    if not files:
        print(f"no checkpoints in {CORPUS}")
        return 1

    grand: Counter = Counter()
    for f in files:
        counts = migrate(f, dry_run=dry_run)
        grand.update(counts)
        print(f"  {f.name:<50} {counts['_changed']:>7,} rewritten")

    print("\nextractor, after:")
    for tier, n in sorted(grand.items()):
        if tier != "_changed":
            print(f"  {tier!s:<34} {n:>7,}")
    print(f"\n{grand['_changed']:,} claims rewritten")
    print("dry run — nothing written" if dry_run else "rewritten; .jsonl.provbak kept alongside")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))
