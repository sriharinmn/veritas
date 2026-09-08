"""Adding a document without rebuilding everything.

The brief's fourth brownie point, verbatim: "new documents incrementally,
without rebuilding all existing knowledge". A full rebuild reloads every claim,
regrows the ontology and re-compares 135,893 pairs — measured at 101 seconds and
getting worse with every document, which is the wrong shape for a system whose
whole premise is that documents accumulate.

The property that matters is not that it is faster. It is that it is *the same*:
an incrementally extended layer must find the relationships a rebuilt one finds,
or the optimisation has quietly changed the answer.
"""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import pytest

from core.store.checkpoints import CORPUS_DIR, build, extend

SNAPSHOT = sorted(CORPUS_DIR.glob("*.jsonl.gz"))


def _write_pages(source: Path, dest: Path, pages: slice) -> int:
    """Copy a slice of one checkpoint's pages into a plain .jsonl file."""
    with gzip.open(source, "rt", encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    chosen = lines[pages]
    dest.write_text("\n".join(chosen) + "\n", encoding="utf-8")
    return sum(len(json.loads(ln).get("claims") or []) for ln in chosen)


@pytest.fixture
def two_documents(tmp_path):
    """A corpus of two small documents, so 'incremental' has something to add to."""
    if len(SNAPSHOT) < 2:
        pytest.skip("snapshot not present")
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    _write_pages(SNAPSHOT[0], first, slice(0, 2))
    _write_pages(SNAPSHOT[1], second, slice(0, 2))
    return tmp_path, first, second


def test_extending_finds_what_rebuilding_finds(two_documents, tmp_path):
    """The property worth having. Faster is only worth it if it agrees."""
    directory, first, second = two_documents

    # Build with only the first document present, then add the second.
    held = second.read_text(encoding="utf-8")
    second.unlink()
    incremental = build(directory)
    before = len(incremental.claims)
    second.write_text(held, encoding="utf-8")
    incremental = extend(incremental, directory)

    rebuilt = build(directory)

    assert len(incremental.claims) == len(rebuilt.claims) > before
    assert {d.filename for d in incremental.documents} == {
        d.filename for d in rebuilt.documents
    }

    # The relation mix is what a reader actually sees, and it must match.
    assert incremental.counts() == rebuilt.counts()


def test_extending_grows_the_existing_ontology_rather_than_starting_a_new_one(
    two_documents, tmp_path
):
    """The second document's predicates must join the first document's registry.

    If they did not, an incrementally added document could never corroborate
    anything already known — every fact would land on a fresh node and compare
    against nothing, which would look like the system working while it silently
    found no relationships at all.
    """
    directory, first, second = two_documents
    held = second.read_text(encoding="utf-8")
    second.unlink()

    layer = build(directory)
    predicates_before = len(layer.predicates.nodes)
    registry_id = id(layer.predicates)

    second.write_text(held, encoding="utf-8")
    layer = extend(layer, directory)

    assert id(layer.predicates) == registry_id, "a new registry was started"
    assert len(layer.predicates.nodes) >= predicates_before


def test_extending_with_nothing_new_changes_nothing(two_documents):
    directory, _, _ = two_documents
    layer = build(directory)
    claims, edges = len(layer.claims), len(layer.edges)

    layer = extend(layer, directory)

    assert len(layer.claims) == claims
    assert len(layer.edges) == edges


def test_extending_an_unbuilt_layer_falls_back_to_a_full_build(two_documents):
    """A layer with no registries has no ontology to extend, and quietly
    producing claims that belong to no registry would be worse than the cost of
    building properly."""
    from core.store.checkpoints import KnowledgeLayer

    directory, _, _ = two_documents
    layer = extend(KnowledgeLayer(), directory)
    assert layer.claims and layer.predicates is not None
