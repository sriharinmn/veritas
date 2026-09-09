"""The shipped snapshot — the thing that makes a fresh clone useful.

The README promises `docker compose up` boots an already-populated app with no
API key, no GPU and no waiting. That promise rests entirely on the knowledge
layer being *in the repository*, and for a while it was not: `evals/corpus/` was
gitignored along with the run logs, so a clone would have started empty and
every screen would have been blank.

These tests are cheap insurance on the one artefact whose absence is invisible
until a reviewer clones the repo and finds nothing.
"""

from __future__ import annotations

import gzip
import json

from core.store.checkpoints import CORPUS_DIR, _checkpoints, _open_checkpoint, load_claims

SNAPSHOT = sorted(CORPUS_DIR.glob("*.jsonl.gz"))


def test_the_snapshot_exists_and_covers_every_document():
    """If this fails, a fresh clone boots an empty app."""
    assert SNAPSHOT, (
        "no *.jsonl.gz in evals/corpus — run `python -m scripts.build_snapshot`. "
        "Without it a fresh clone has no knowledge layer."
    )
    assert len(SNAPSHOT) >= 6


def test_a_gzipped_checkpoint_reads_back_as_jsonl():
    with _open_checkpoint(SNAPSHOT[0]) as f:
        first = json.loads(f.readline())
    assert "page" in first
    assert "document" in first
    assert isinstance(first.get("claims"), list)


def test_the_loader_finds_the_snapshot_when_no_plain_checkpoint_exists(tmp_path):
    """The fresh-clone case: only the gzipped snapshot is present."""
    (tmp_path / "doc.jsonl.gz").write_bytes(SNAPSHOT[0].read_bytes())

    claims, _, docs = load_claims(tmp_path)

    assert claims, "the snapshot produced no claims"
    assert docs and docs[0].pages_processed > 0


def test_a_live_run_takes_precedence_over_the_snapshot(tmp_path):
    """A reviewer who ingests their own PDF must see their own result, not ours.

    Both files present, same stem: the plain one wins and the archive is not
    read a second time, or every claim would be duplicated.
    """
    (tmp_path / "doc.jsonl.gz").write_bytes(SNAPSHOT[0].read_bytes())
    with gzip.open(SNAPSHOT[0], "rt", encoding="utf-8") as f:
        one_page = f.readline()
    (tmp_path / "doc.jsonl").write_text(one_page, encoding="utf-8")

    chosen = _checkpoints(tmp_path)
    assert [p.name for p in chosen] == ["doc.jsonl"]

    _, _, docs = load_claims(tmp_path)
    assert docs[0].pages_processed == 1
