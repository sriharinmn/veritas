"""The read API over the knowledge layer.

Serves documents, claims, evidence and classified relationships to the UI, plus
the PDF bytes themselves so the browser can render the exact page a claim cites
and draw its bounding box over it.

The layer is rebuilt from checkpoints on demand and cached, with a cheap
staleness check on the checkpoint directory's modification time — so while the
overnight corpus run is still writing, a refresh in the browser picks up
whatever has landed since. That matters during the build: the interface is
developed against real claims from real filings from the first screen onwards,
rather than against mocks that quietly diverge from what the pipeline actually
produces.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from core.models import Claim, Relation
from core.parse.pdf import document_uuid
from core.store.checkpoints import (
    CORPUS_DIR,
    DocumentSummary,
    Edge,
    KnowledgeLayer,
    _checkpoints,
    _open_checkpoint,
    _pdf_path,
    build,
    extend,
)

log = structlog.get_logger(__name__)
router = APIRouter(tags=["knowledge"])

_cache: dict[str, object] = {"layer": None, "signature": None, "built_at": 0.0, "ready": False}
MIN_REBUILD_INTERVAL = 20.0


def _signature() -> tuple:
    if not CORPUS_DIR.exists():
        return ()
    return tuple(
        sorted((p.name, p.stat().st_mtime, p.stat().st_size) for p in CORPUS_DIR.glob("*.jsonl"))
    )


_rebuilding = threading.Lock()


def _rebuild(signature: tuple) -> None:
    """Build a fresh layer off the request path and swap it in when ready."""
    if not _rebuilding.acquire(blocking=False):
        return  # one rebuild at a time; a second would duplicate a minute of work
    try:
        t = time.perf_counter()
        fresh = build()
        _cache["layer"] = fresh
        _cache["signature"] = signature
        _cache["built_at"] = time.time()
        _cache["ready"] = True
        log.info(
            "knowledge.rebuilt",
            seconds=round(time.perf_counter() - t, 2),
            claims=len(fresh.claims),
        )
    except Exception:  # noqa: BLE001 — a failed rebuild must not lose the good layer
        log.exception("knowledge.rebuild_failed")
    finally:
        _rebuilding.release()


def reset_cache() -> None:
    """Forget everything. For tests, and for a corpus that changed underneath us."""
    _cache.update({"layer": None, "signature": None, "built_at": 0.0, "ready": False})
    _index_cache.clear()


def layer_ready() -> bool:
    """Has a real layer been built, or are we serving the empty placeholder?"""
    return bool(_cache.get("ready"))


_index_cache: dict[tuple, dict[str, DocumentSummary]] = {}


def document_index(directory: Path | None = None) -> dict[str, DocumentSummary]:
    """Every document's id, name and file path — without building anything.

    The evidence pane needs one thing to put a page on screen: a path on disk.
    Coupling that to the knowledge layer meant the most important screen in the
    product waited 101 seconds behind a graph build, and the browser gave up
    first with a message that blamed the network.

    Only the first line of each checkpoint is read. Every line carries the same
    document name and content hash, and the id is derived from the hash exactly
    as extraction derives it, so this agrees with what the claims say while
    costing a handful of short reads rather than a full parse.
    """
    root = directory or CORPUS_DIR
    # Keyed on the signature, not just the path.
    #
    # This cached on the path alone and was therefore never invalidated: a
    # document uploaded into a running server was absent from the index for the
    # life of the process, so its evidence pane could not find the PDF it had
    # just written. The signature is three stat() calls per checkpoint, which is
    # nothing next to being wrong until a restart.
    key = (str(root), _signature())
    if key in _index_cache:
        return _index_cache[key]
    _index_cache.clear()

    found: dict[str, DocumentSummary] = {}
    for path in sorted(_checkpoints(root)):
        try:
            with _open_checkpoint(path) as f:
                first = f.readline()
            record = json.loads(first)
        except (OSError, json.JSONDecodeError, ValueError):
            continue

        filename = record.get("document")
        sha = record.get("document_sha256")
        if not filename or not sha:
            continue

        found[str(document_uuid(sha))] = DocumentSummary(
            id=str(document_uuid(sha)),
            filename=filename,
            path=_pdf_path(filename),
            sha256=sha,
            pages_processed=0,
            claims=0,
            quarantined=0,
        )

    _index_cache[key] = found
    return found


def layer() -> KnowledgeLayer:
    """The knowledge layer, never blocking a request to rebuild it.

    Rebuilding means loading every checkpoint, regrowing the ontology and
    classifying a hundred thousand pairs: measured at 101 seconds on this
    corpus. Doing that inline meant the first request after any checkpoint
    changed hung for a minute and a half — and during an upload the checkpoint
    changes after every single page, so the app was unusable at exactly the
    moment somebody was watching it work.

    So a stale layer is served while a fresh one is built on a worker thread and
    swapped in atomically. The counts lag by a page or two during ingest, which
    nobody notices; a hundred-second stall is all anyone notices. The only
    blocking build is the very first one, when there is nothing to serve.
    """
    now = time.time()
    sig = _signature()

    if _cache["layer"] is None:
        # An empty layer now, and a real one shortly — never a blocking build.
        #
        # This was `build()` called inline, and because `layer()` is called
        # synchronously from `async def` endpoints, those 101 CPU-bound seconds
        # stalled the entire event loop. Every other request queued behind it,
        # including the evidence pane's PDF fetches, and the browser gave up
        # first with "Failed to fetch" — a message that blames the network for
        # a server that accepted the connection and never answered.
        _cache["layer"] = KnowledgeLayer()
        _cache["signature"] = sig
        _cache["built_at"] = now
        _cache["ready"] = False
        threading.Thread(target=_rebuild, args=(sig,), daemon=True).start()
        return _cache["layer"]  # type: ignore[return-value]

    stale = sig != _cache["signature"]
    cooled = now - float(_cache["built_at"] or 0) > MIN_REBUILD_INTERVAL

    # Not while a document is being ingested.
    #
    # A checkpoint is written after every page, so the signature moves every
    # ninety seconds during an upload and each move starts a full rebuild that
    # is stale before it finishes — a four-minute build, repeatedly, over a
    # document that is still arriving. The layer being a few pages behind
    # during ingest is invisible; the machine grinding through the same work
    # over and over is not.
    #
    # The upload path calls `absorb_new_claims()` when it finishes, which folds
    # the completed document in incrementally. That is the right moment, and it
    # is the only one needed.
    if stale and cooled and not _run_active():
        threading.Thread(target=_rebuild, args=(sig,), daemon=True).start()
    return _cache["layer"]  # type: ignore[return-value]


def absorb_new_claims() -> None:
    """Fold a freshly ingested document into the cached layer, incrementally.

    Called when an upload finishes. `build()` would reload every claim, regrow
    the whole ontology and re-compare 135,893 pairs — 101 seconds that gets
    worse with every document, which is precisely the shape the brief asks us
    to avoid: "new documents incrementally, without rebuilding all existing
    knowledge".

    `extend()` canonicalises only what arrived, into the registries already
    built, and pairs it against what was already known. Same relationships, work
    proportional to the new document rather than to the corpus.
    """
    # This *waits* for an in-flight rebuild rather than skipping.
    #
    # Both paths used to give up silently. A rebuild is very likely to be in
    # flight at exactly this moment, because writing the first page checkpoint
    # changes the corpus signature and so starts one — and that rebuild reads
    # the document while it is still being extracted, so it captures a partial
    # file or none at all. Skipping then left the upload nowhere: the interface
    # said it was available, and it appeared four minutes later when a request
    # happened to notice the signature had moved and paid for a second full
    # rebuild.
    #
    # Blocking here is free. `absorb_new_claims` is already called from a worker
    # thread, so the only thing waiting is the ingest job that has just
    # finished, and what it is waiting for is the work that makes its own
    # document findable.
    _index_cache.clear()

    with _rebuilding:
        if _cache["layer"] is None or not _cache.get("ready"):
            # Nothing to extend — no layer has been built yet. Force the next
            # request to build one, rather than leaving it to the signature
            # check and the rebuild interval.
            _cache["signature"] = None
            _cache["built_at"] = 0.0
            return
        _absorb_locked()


def _absorb_locked() -> None:
    """The extend itself. Caller holds `_rebuilding`."""
    try:
        t = time.perf_counter()
        before = len(_cache["layer"].claims)  # type: ignore[union-attr]
        _cache["layer"] = extend(_cache["layer"])  # type: ignore[arg-type]
        _cache["signature"] = _signature()
        _cache["built_at"] = time.time()
        after = len(_cache["layer"].claims)  # type: ignore[union-attr]
        log.info(
            "knowledge.absorbed",
            seconds=round(time.perf_counter() - t, 2),
            new_claims=after - before,
        )
    except Exception:  # noqa: BLE001 — a failed absorb must not lose the good layer
        log.exception("knowledge.absorb_failed")
        # Do not strand the document. A rebuild is slower than an extend but it
        # is correct, and being slow is a better failure than being invisible.
        _cache["signature"] = None
        _cache["built_at"] = 0.0


# ── serialisation ────────────────────────────────────────────────────────────


def _claim_json(c: Claim) -> dict:
    ev = c.evidence[0] if c.evidence else None
    return {
        "id": str(c.id),
        "subject": c.subject_raw,
        "predicate": c.predicate_raw,
        "value": {
            "raw": c.value.raw,
            "kind": c.value.kind.value,
            "canonical": str(c.value.canonical_magnitude) if c.value.canonical_magnitude is not None else None,
            "currency": c.value.currency,
            "unit": c.value.unit,
            "is_range": c.value.is_range,
        },
        "scope": {
            "period": c.scope.period.label,
            "period_start": c.scope.period.start.isoformat() if c.scope.period.start else None,
            "period_end": c.scope.period.end.isoformat() if c.scope.period.end else None,
            "convention": c.scope.period.convention.value,
            "basis": c.scope.basis.value,
            "segment": c.scope.segment,
            "geography": c.scope.geography,
            "accounting": c.scope.accounting.value,
            "modality": c.scope.modality.value,
            "vintage": c.scope.vintage.isoformat() if c.scope.vintage else None,
        },
        "confidence": c.confidence,
        "scale_inferred": c.scale_inferred,
        "extractor": c.provenance.extractor if c.provenance else None,
        "evidence": [
            {
                "document_id": str(e.document_id),
                "page": e.page,
                "char_start": e.char_start,
                "char_end": e.char_end,
                "quote": e.quote,
                "kind": e.kind,
                "rects": [{"x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1} for r in e.rects],
            }
            for e in c.evidence
        ],
        "document_id": str(ev.document_id) if ev else None,
        "page": ev.page if ev else None,
    }


def _edge_json(e: Edge, index: int) -> dict:
    return {
        "id": f"{e.a.id}~{e.b.id}",
        "index": index,
        "relation": e.verdict.relation.value,
        "axis": e.verdict.axis,
        "confidence": round(e.verdict.confidence, 3),
        "decided_by": e.verdict.decided_by,
        "trace": e.verdict.trace,
        "explanation": e.verdict.explanation,
        "reason": e.reason,
        "cross_document": e.cross_document,
        "a": _claim_json(e.a),
        "b": _claim_json(e.b),
    }


# ── endpoints ────────────────────────────────────────────────────────────────


@router.get("/stats")
async def stats() -> dict:
    L = layer()
    return {
        **L.stats(),
        "ingest_in_progress": _run_active(),
        # Zero facts and "not loaded yet" are different states, and the
        # difference decides whether a reader thinks extraction failed.
        "ready": layer_ready(),
        "documents_on_disk": len(document_index()),
    }


def _run_active() -> bool:
    """Whether the corpus run has written in the last two minutes.

    Lets the UI say "still ingesting" honestly rather than presenting a partial
    knowledge layer as though it were the whole thing.
    """
    log_file = CORPUS_DIR / "run.log"
    if not log_file.exists():
        return False
    return (time.time() - log_file.stat().st_mtime) < 120


@router.get("/documents")
async def documents() -> list[dict]:
    L = layer()
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "entity": d.entity,
            "pages_processed": d.pages_processed,
            "claims": d.claims,
            "quarantined": d.quarantined,
            "sha256": d.sha256[:12],
            "has_pdf": bool(d.path),
        }
        for d in L.documents
    ]


@router.get("/documents/{document_id}/file")
async def document_file(document_id: str) -> FileResponse:
    """The PDF itself.

    Resolved from the cheap index rather than the knowledge layer: this is the
    one request the evidence pane cannot do without, and it must answer while
    the graph is still being built rather than queue behind it.
    """
    doc = document_index().get(document_id) or layer().document(document_id)
    if doc is None or not doc.path or not Path(doc.path).exists():
        raise HTTPException(404, "No PDF on disk for that document")
    return FileResponse(doc.path, media_type="application/pdf", filename=doc.filename)


@router.get("/claims")
async def claims(
    document_id: str | None = None,
    predicate: str | None = None,
    period: str | None = None,
    basis: str | None = None,
    q: str | None = None,
    limit: int = Query(200, le=2000),
    offset: int = 0,
) -> dict:
    L = layer()
    rows = L.claims
    if document_id:
        rows = [c for c in rows if c.evidence and str(c.evidence[0].document_id) == document_id]
    if predicate:
        rows = [c for c in rows if predicate.lower() in c.predicate_raw.lower()]
    if period:
        rows = [c for c in rows if (c.scope.period.label or "").lower() == period.lower()]
    if basis:
        rows = [c for c in rows if c.scope.basis.value == basis]
    if q:
        needle = q.lower()
        rows = [
            c
            for c in rows
            if needle in c.predicate_raw.lower()
            or needle in c.value.raw.lower()
            or needle in c.subject_raw.lower()
        ]
    return {
        "total": len(rows),
        "items": [_claim_json(c) for c in rows[offset : offset + limit]],
    }


@router.get("/claims/{claim_id}")
async def claim(claim_id: str) -> dict:
    L = layer()
    found = L.by_id(claim_id)
    if found is None:
        raise HTTPException(404, "No such claim")
    related = [
        _edge_json(e, i)
        for i, e in enumerate(L.edges)
        if str(e.a.id) == claim_id or str(e.b.id) == claim_id
    ][:50]
    return {"claim": _claim_json(found), "related": related}


@router.get("/relations")
async def relations(
    relation: str | None = None,
    cross_document: bool | None = None,
    limit: int = Query(60, le=500),
    offset: int = 0,
) -> dict:
    L = layer()
    rows = L.edges
    if relation:
        try:
            wanted = Relation(relation)
        except ValueError as e:
            raise HTTPException(400, f"Unknown relation {relation!r}") from e
        rows = [e for e in rows if e.verdict.relation is wanted]
    if cross_document is not None:
        rows = [e for e in rows if e.cross_document is cross_document]
    return {
        "total": len(rows),
        "counts": L.counts(),
        "items": [_edge_json(e, offset + i) for i, e in enumerate(rows[offset : offset + limit])],
    }


@router.get("/ontology")
async def ontology(kind: str = "predicate", limit: int = Query(120, le=1000)) -> dict:
    L = layer()
    registry = L.predicates if kind == "predicate" else L.entities
    if registry is None:
        return {"nodes": [], "decisions": [], "stats": {}}
    nodes = sorted(registry.nodes.values(), key=lambda n: -n.alias_count)[:limit]
    return {
        "stats": registry.stats(),
        "nodes": [
            {"id": str(n.id), "label": n.label, "aliases": sorted(n.aliases)[:12], "alias_count": n.alias_count}
            for n in nodes
        ],
        "decisions": [
            {
                "query": d.query,
                "action": d.action.value,
                "similarity": round(d.similarity, 3),
                "nearest": d.nearest_label,
                "describe": d.describe(),
                "needs_review": d.needs_review,
            }
            for d in registry.decisions[-limit:]
        ],
    }


@router.get("/quarantine")
async def quarantine(limit: int = Query(100, le=1000)) -> dict:
    L = layer()
    by_reason: dict[str, int] = {}
    for q in L.quarantined:
        by_reason[q.get("reason", "unknown")] = by_reason.get(q.get("reason", "unknown"), 0) + 1
    return {"total": len(L.quarantined), "by_reason": by_reason, "items": L.quarantined[:limit]}


@router.get("/cases")
async def cases() -> dict:
    """The assignment's four required cases, as selected by published criteria.

    Served from `evals/cases.json`, written by `python -m scripts.curate_cases`.
    On disk rather than computed here for the same reason as the eval report,
    only more so: choosing them means building the whole knowledge layer and
    scoring a hundred thousand edges.

    Selection is deliberately not a matter of taste. Each case is a scoring
    function anyone can read and disagree with, and a case that nothing in the
    corpus satisfies reports that rather than lowering the bar — case 2 does
    exactly that today, and the empty result is a finding rather than a gap.
    """
    path = Path("evals/cases.json")
    if not path.exists():
        return {
            "available": False,
            "hint": "Run `python -m scripts.curate_cases` to select them.",
        }
    import json as _json

    return {"available": True, **_json.loads(path.read_text(encoding="utf-8"))}


@router.get("/evals")
async def evals() -> dict:
    """The most recent eval report, as written by `python -m evals.run`.

    Served from disk rather than computed on request: the harness shells out to
    pytest and re-parses every document, which is a minute of work and has no
    business happening inside a page load. A stale report with its timestamp
    shown is more honest than a fresh one the reader waited for.
    """
    latest = Path("evals/reports/latest.json")
    if not latest.exists():
        return {
            "available": False,
            "hint": "Run `python -m evals.run` to generate a report.",
        }
    import json as _json

    return {"available": True, **_json.loads(latest.read_text(encoding="utf-8"))}
