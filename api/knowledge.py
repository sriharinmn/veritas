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

import time
from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from core.models import Claim, Relation
from core.store.checkpoints import CORPUS_DIR, Edge, KnowledgeLayer, build

log = structlog.get_logger(__name__)
router = APIRouter(tags=["knowledge"])

_cache: dict[str, object] = {"layer": None, "signature": None, "built_at": 0.0}
MIN_REBUILD_INTERVAL = 20.0


def _signature() -> tuple:
    if not CORPUS_DIR.exists():
        return ()
    return tuple(
        sorted((p.name, p.stat().st_mtime, p.stat().st_size) for p in CORPUS_DIR.glob("*.jsonl"))
    )


def layer() -> KnowledgeLayer:
    now = time.time()
    sig = _signature()
    stale = sig != _cache["signature"]
    cooled = now - float(_cache["built_at"] or 0) > MIN_REBUILD_INTERVAL
    if _cache["layer"] is None or (stale and cooled):
        t = time.perf_counter()
        _cache["layer"] = build()
        _cache["signature"] = sig
        _cache["built_at"] = now
        log.info("knowledge.rebuilt", seconds=round(time.perf_counter() - t, 2))
    return _cache["layer"]  # type: ignore[return-value]


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
    doc = layer().document(document_id)
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
