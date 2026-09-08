"""The API must answer while the knowledge layer is still being built.

The bug this pins down took three attempts to find, because every symptom
pointed somewhere else.

A reader opened a case page and one evidence pane rendered while the other
reported "Failed to fetch". The server logged 200 for both documents, the PDF
downloaded fine from a shell, CORS was correct, and the file was intact — so the
obvious suspects were all innocent. What actually happened is that building the
knowledge layer takes 101 seconds of CPU-bound Python, `layer()` did it *inline*,
and it was called synchronously from `async def` endpoints. One blocking call
stalls the whole event loop, so every other request — including the second pane's
PDF — waits behind it until the browser gives up.

The lesson worth keeping is that "Failed to fetch" is a client-side symptom of a
server that accepted the connection and never answered. It looks like a network
problem and is not one.

Two properties are asserted here:

  1. Serving a document's PDF must not require the knowledge layer at all. The
     evidence pane is the most important screen in the product and it needs one
     thing — a path on disk — so it must not be coupled to a graph build.
  2. A cold cache must not block. Requests answer immediately with whatever is
     known, and the layer arrives when it arrives.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from api import knowledge
from core.store.checkpoints import CORPUS_DIR

SNAPSHOT = sorted(CORPUS_DIR.glob("*.jsonl.gz"))


@pytest.fixture
def corpus(tmp_path):
    """One document's worth of checkpoint, as a plain .jsonl file."""
    if not SNAPSHOT:
        pytest.skip("snapshot not present")
    with gzip.open(SNAPSHOT[0], "rt", encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()][:2]
    (tmp_path / "doc.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path, json.loads(lines[0])


@pytest.fixture
def no_build(monkeypatch):
    """Make any attempt to build the knowledge layer a loud failure."""
    def explode(*a, **k):
        raise AssertionError(
            "building the knowledge layer on this path blocks the event loop "
            "for ~101 seconds and stalls every other request"
        )

    monkeypatch.setattr(knowledge, "build", explode)
    return explode


def test_finding_a_documents_pdf_never_builds_the_layer(corpus, no_build, monkeypatch):
    """The evidence pane must not be coupled to a graph build.

    It needs a path on disk. Everything else the layer knows is irrelevant to
    putting a page on screen.
    """
    directory, first = corpus
    monkeypatch.setattr(knowledge, "CORPUS_DIR", directory)
    knowledge.reset_cache()

    index = knowledge.document_index()

    assert index, "no documents found without a full build"
    entry = next(iter(index.values()))
    assert entry.filename == first["document"]
    assert entry.sha256 == first["document_sha256"]


def test_the_document_id_matches_the_one_claims_carry(corpus, no_build, monkeypatch):
    """The index derives ids the same way extraction does — from the content
    hash — or the evidence pane would look up a document that does not exist."""
    directory, first = corpus
    monkeypatch.setattr(knowledge, "CORPUS_DIR", directory)
    knowledge.reset_cache()

    claim_document_id = first["claims"][0]["evidence"][0]["document_id"]
    assert claim_document_id in knowledge.document_index()


def test_a_cold_cache_answers_immediately_rather_than_blocking(corpus, no_build, monkeypatch):
    """`layer()` on an empty cache must return something now, not in 101 seconds.

    An empty layer is a truthful answer to "what do you know" before anything
    has been loaded. A two-minute stall is not an answer at all.
    """
    directory, _ = corpus
    monkeypatch.setattr(knowledge, "CORPUS_DIR", directory)
    knowledge.reset_cache()

    layer = knowledge.layer()

    assert layer is not None
    assert layer.claims == []
    assert knowledge.layer_ready() is False


def test_stats_says_it_is_still_warming_up(corpus, no_build, monkeypatch):
    """Zero facts and 'not ready yet' are different states, and the difference
    matters to anyone deciding whether the extraction failed."""
    directory, _ = corpus
    monkeypatch.setattr(knowledge, "CORPUS_DIR", directory)
    knowledge.reset_cache()

    knowledge.layer()
    assert knowledge.layer_ready() is False
