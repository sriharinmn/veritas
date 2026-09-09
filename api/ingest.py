"""Uploading a PDF and watching it become facts.

This closes the loop the assignment actually describes: a reader hands the system
a document it has never seen, and gets back grounded facts linked to the pages
they came from and compared against everything already known.

Three decisions shape it.

**Progress is streamed, not polled.** Extraction is minutes of work, and a
progress bar that only moves when the whole document finishes is a worse
experience than no progress bar at all. Pages are processed in descending
candidate density and each one's claims are announced as they land, so the
financial statements arrive first and the reader is reading real facts while the
signature pages are still being worked through.

**The provider router decides the tier, once, before any work starts.** This is
the first caller it has had. An eight-page document with a key present goes to
Groq; a two-hundred-page filing goes to the local GPU because the daily cap
cannot cover it; with nothing reachable it runs deterministically and the stream
says so in the first event rather than at the end.

**Checkpoints are written per page, in the same format the corpus run uses.**
The upload path and the overnight path produce identical artefacts, so a browser
upload is picked up by the same loader, the same evals and the same case
curation with no special handling anywhere. It also means an upload survives the
server being killed halfway.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from core.extract.deterministic import extract_page as extract_page_rules
from core.extract.gateway import build_gateway
from core.extract.llm import extract_page as extract_page_model
from core.extract.llm import read_document_context
from core.extract.semantic import extract_semantic_page, prose_blocks
from core.extract.spot import spot_document
from core.ground.verify import verify_all
from core.parse.pdf import document_uuid, parse_pdf
from core.route import route_document
from core.settings import Tier
from core.store.checkpoints import CORPUS_DIR

log = structlog.get_logger(__name__)
router = APIRouter(tags=["ingest"])

UPLOADS = Path("seed/uploads")
MAX_BYTES = 80 * 1024 * 1024

# A soft ceiling on a single upload, not a hard page cap. Pages are ordered by
# density, so this keeps the fact-bearing pages and drops the sparse tail — and
# the stream says how many were skipped rather than pretending the document was
# fully covered. plan.md is explicit that a large PDF must never be refused.
DEFAULT_PAGE_BUDGET = int(os.environ.get("VERITAS_UPLOAD_PAGE_BUDGET", "30"))

# The semantic pass gets its own small budget over the prose-richest pages.
# Non-numeric facts do not live where the numeric candidates are: an audit
# report page has no figures worth extracting and every fact about who signed
# it, so ranking by density would miss them entirely.
SEMANTIC_PAGE_BUDGET = int(os.environ.get("VERITAS_SEMANTIC_PAGE_BUDGET", "5"))


@dataclass
class Job:
    id: str
    filename: str
    status: str = "queued"  # queued | running | done | failed
    tier: str = ""
    degraded: bool = False
    pages_total: int = 0
    pages_done: int = 0
    pages_skipped: int = 0
    claims: int = 0
    quarantined: int = 0
    document_id: str | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    finished: bool = False

    def emit(self, kind: str, **payload) -> None:
        """Fire and forget. A reader who has closed the tab must not stall the job."""
        event = {"type": kind, "job": self.id, **payload}
        with contextlib.suppress(asyncio.QueueFull):  # pragma: no cover — unbounded
            self.events.put_nowait(event)


_jobs: dict[str, Job] = {}

# Strong references to in-flight extraction tasks; see `upload`.
_running: set[asyncio.Task] = set()


def _record(page: int, path: Path, doc, grounded, quarantined) -> str:
    """One checkpoint line, in the exact format the corpus run writes.

    Shared by the numeric and semantic passes so a reader of the file cannot
    tell which produced a given line, and neither can the loader — which is the
    point: semantic claims are claims.
    """
    return json.dumps(
        {
            "page": page,
            "document": path.name,
            "document_sha256": doc.sha256,
            "extracted_at": datetime.now(UTC).isoformat(),
            "claims": [c.model_dump(mode="json") for c in grounded],
            "quarantined": [
                {"claim": c.model_dump(mode="json"), "reason": r.reason, "detail": r.detail}
                for c, r in quarantined
            ],
        },
        default=str,
    )


def _append(checkpoint: Path, page: int, path: Path, doc, grounded, quarantined) -> None:
    with checkpoint.open("a", encoding="utf-8") as sink:
        sink.write(_record(page, path, doc, grounded, quarantined) + "\n")


def _discard(path: Path) -> None:
    """Delete a rejected upload, and do not fail the request if it will not go.

    On Windows a PDF that failed to open may still be held by the parser when
    the exception surfaces, and the unlink then raises PermissionError — turning
    a clean "this is not a PDF" 400 into a 500 that tells the uploader nothing.
    The stray file is a few megabytes in a directory that is already ignored;
    the wrong status code is what actually costs someone their afternoon.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError as e:  # pragma: no cover — platform-dependent
        log.warning("ingest.cleanup_failed", path=str(path), error=str(e)[:120])


def _safe_stem(name: str) -> str:
    """A filename we are willing to write to disk.

    Uploads are attacker-controlled and this is used to build a path. Anything
    that is not a plain name is discarded rather than sanitised, because
    sanitising path traversal is a game nobody wins.

    **Both separators, explicitly.** This used `Path(name).stem`, whose meaning
    depends on the platform it runs on: a backslash separates directories on
    Windows and is an ordinary filename character on Linux. So
    "..\..\windows\system32.pdf" reduced to "system32" on the machine this
    was written on and to "windows-system32" in the container it ships in.

    Neither result is dangerous — every character outside a small allow-list is
    replaced either way — but a path sanitiser whose behaviour depends on its
    host is one whose behaviour nobody has actually verified, and the uploader
    can be on any operating system. The test suite caught it on Linux; the
    lesson is that this function should never have asked the platform.
    """
    last = re.split(r"[\\/]", name)[-1]
    stem = last[: last.rfind(".")] if "." in last else last
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in stem).strip("-")
    return (cleaned or "document")[:80]


async def _run(job: Job, path: Path) -> None:
    """Parse, route, extract, ground — announcing each page as it lands."""
    try:
        job.status = "running"
        doc = await asyncio.to_thread(parse_pdf, path)
        job.document_id = str(document_uuid(doc.sha256))
        job.emit(
            "parsed",
            pages=doc.page_count,
            scanned_pages=len(doc.scanned_pages),
            document_id=job.document_id,
        )

        spots = await asyncio.to_thread(spot_document, doc)
        job.emit("spotted", candidates=spots.total, signal=spots.signal)

        decision = await asyncio.to_thread(route_document, spots, page_budget=DEFAULT_PAGE_BUDGET)
        job.tier = decision.tier.value
        job.degraded = decision.degraded
        job.emit(
            "routed",
            tier=decision.tier.value,
            degraded=decision.degraded,
            banner=decision.banner,
            eta_seconds=decision.eta_seconds,
            estimate=decision.estimate.describe(),
            trace=decision.trace,
        )

        order = spots.pages_by_density()
        budget = order[:DEFAULT_PAGE_BUDGET]
        job.pages_total = len(budget)
        job.pages_skipped = max(0, len(order) - len(budget))

        checkpoint = CORPUS_DIR / f"{path.stem}.jsonl"
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        by_number = {p.number: p for p in doc.pages}
        run_id = uuid4()
        doc_id = UUID(job.document_id)

        gateway = build_gateway(decision.tier) if decision.tier is not Tier.DETERMINISTIC else None
        context = None
        if gateway is not None:
            context = await read_document_context(doc, gateway)
            job.emit(
                "context",
                entity=context.entity,
                currency=context.reporting_currency,
                scale=context.reporting_scale,
                basis=context.default_basis.value,
            )

        subject = (context.entity if context else None) or _safe_stem(job.filename)

        with checkpoint.open("a", encoding="utf-8") as sink:
            for number in budget:
                page = by_number[number]
                try:
                    if gateway is not None and context is not None:
                        claims = await extract_page_model(
                            page, gateway, document_id=doc_id, context=context, run_id=run_id
                        )
                    else:
                        claims = await asyncio.to_thread(
                            extract_page_rules,
                            page,
                            document_id=doc_id,
                            subject_raw=subject,
                            run_id=run_id,
                        )
                except Exception as e:
                    log.warning("ingest.page_failed", page=number, error=str(e)[:160])
                    job.emit("page_failed", page=number, error=type(e).__name__)
                    job.pages_done += 1
                    continue

                grounded, quarantined, _ = verify_all(claims, {number: page})
                sink.write(_record(number, path, doc, grounded, quarantined) + "\n")
                sink.flush()  # survive a kill, exactly as the corpus run does

                job.pages_done += 1
                job.claims += len(grounded)
                job.quarantined += len(quarantined)
                job.emit(
                    "page",
                    page=number,
                    grounded=len(grounded),
                    quarantined=len(quarantined),
                    pages_done=job.pages_done,
                    pages_total=job.pages_total,
                    claims_total=job.claims,
                )

        # ── the semantic pass ────────────────────────────────────────────────
        #
        # Non-numeric facts live in prose, and prose is not where the numeric
        # candidates are — an audit report page has no figures worth extracting
        # and every fact about who signed it. So this runs over its own small
        # budget of the prose-richest pages rather than piggy-backing on the
        # density order, and it is skipped entirely on the deterministic tier
        # because there is no rule that finds "ceased to be a Director".
        if gateway is not None:
            ranked = sorted(
                (p for p in doc.pages if prose_blocks(p)),
                key=lambda p: -sum(len(b.text) for b in prose_blocks(p)),
            )[:SEMANTIC_PAGE_BUDGET]

            for page in ranked:
                try:
                    facts, refused = await extract_semantic_page(
                        page,
                        gateway,
                        document_id=doc_id,
                        run_id=run_id,
                        entity_hint=context.entity if context else None,
                    )
                except Exception as e:
                    log.warning("ingest.semantic_failed", page=page.number, error=str(e)[:160])
                    continue

                grounded, quarantined, _ = verify_all(facts, {page.number: page})
                if not grounded and not quarantined:
                    continue

                _append(checkpoint, page.number, path, doc, grounded, quarantined)
                job.claims += len(grounded)
                job.quarantined += len(quarantined)
                job.emit(
                    "semantic",
                    page=page.number,
                    facts=len(grounded),
                    refused=refused,
                    claims_total=job.claims,
                )

        # Fold the new document into the cached layer rather than rebuilding it.
        # Imported here, not at module scope, because knowledge.py is the read
        # API and importing it eagerly would make the two modules circular.
        try:
            from api.knowledge import absorb_new_claims

            await asyncio.to_thread(absorb_new_claims)
        except Exception as e:
            log.warning("ingest.absorb_failed", error=str(e)[:160])

        job.status = "done"
        job.emit(
            "done",
            claims=job.claims,
            quarantined=job.quarantined,
            pages=job.pages_done,
            pages_skipped=job.pages_skipped,
            document_id=job.document_id,
            seconds=round(time.time() - job.started_at, 1),
        )
    except Exception as e:
        log.exception("ingest.failed", job=job.id)
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
        job.emit("error", message=job.error)
    finally:
        job.finished = True


@router.post("/documents")
async def upload(file: UploadFile) -> dict:
    """Accept a PDF and start extracting it. Returns immediately with a job id."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted.")

    UPLOADS.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(file.filename)
    target = UPLOADS / f"{stem}.pdf"
    if target.exists():
        target = UPLOADS / f"{stem}-{uuid4().hex[:6]}.pdf"

    size = 0
    with target.open("wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_BYTES:
                out.close()
                _discard(target)
                raise HTTPException(413, f"File is larger than {MAX_BYTES // 1024 // 1024} MB.")
            out.write(chunk)

    if size == 0:
        _discard(target)
        raise HTTPException(400, "The uploaded file was empty.")

    # Fail here, on the request, rather than inside the job — an unreadable PDF
    # should be an immediate error the uploader can act on, not a stream that
    # opens hopefully and dies four seconds later.
    try:
        parsed = await asyncio.to_thread(parse_pdf, target, detect_tables=False)
    except ValueError as e:
        _discard(target)
        raise HTTPException(400, str(e)) from e

    # The same bytes, ingested twice, is not two documents.
    #
    # A document's identity is a pure function of its content, so a re-upload
    # produces a second checkpoint carrying the *same* document id — and every
    # fact in it then meets its own twin under an identical scope and is
    # reported as corroborating itself. Confirmation from one source counted
    # twice is exactly the failure a system built to compare sources must not
    # produce.
    #
    # 409 rather than a silent no-op: the uploader gets the id they were
    # reaching for, and can go and look at it.
    from api.knowledge import document_index

    # Against the corpus this router writes to, not a global — which is also
    # what lets a test point the whole ingest path at a temp directory.
    known = document_index(CORPUS_DIR).get(str(document_uuid(parsed.sha256)))
    if known is not None:
        _discard(target)
        raise HTTPException(
            409,
            f"Already ingested as {known.filename!r} — the same file, by content hash. "
            f"Its facts are already in the knowledge layer.",
            headers={"X-Document-Id": known.id},
        )

    job = Job(id=uuid4().hex[:12], filename=file.filename)
    _jobs[job.id] = job

    # Keep a strong reference until the task finishes.
    #
    # The event loop holds only a weak one, so a fire-and-forget
    # `create_task` can be garbage collected mid-flight — and what would be
    # collected here is the extraction the uploader is watching, with no error
    # anywhere, just a stream that stops. Rare and impossible to reproduce on
    # demand, which is the worst combination.
    task = asyncio.create_task(_run(job, target))
    _running.add(task)
    task.add_done_callback(_running.discard)
    log.info("ingest.accepted", job=job.id, filename=file.filename, bytes=size)
    return {"job": job.id, "filename": file.filename, "bytes": size}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    return {
        "job": job.id,
        "filename": job.filename,
        "status": job.status,
        "tier": job.tier,
        "degraded": job.degraded,
        "pages_done": job.pages_done,
        "pages_total": job.pages_total,
        "pages_skipped": job.pages_skipped,
        "claims": job.claims,
        "quarantined": job.quarantined,
        "document_id": job.document_id,
        "error": job.error,
    }


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """Server-sent events for one ingest job.

    A heartbeat every fifteen seconds keeps proxies from closing an idle
    connection during a slow page — a dense page on a local GPU is minutes of
    silence, and a stream that dies at that moment looks exactly like a crash.
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")

    async def stream():
        yield f"data: {json.dumps({'type': 'hello', 'job': job.id, 'status': job.status})}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(job.events.get(), timeout=15.0)
            except TimeoutError:
                if job.finished and job.events.empty():
                    break
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event, default=str)}\n\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
