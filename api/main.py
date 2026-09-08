"""Veritas API.

The interface the assignment asks for: upload PDFs, inspect the resulting facts,
their evidence, and the relationships between them.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.ingest import router as ingest_router
from api.knowledge import router as knowledge_router
from core.capabilities import detect
from core.settings import settings

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    caps = await detect()
    app.state.capabilities = caps
    log.info(
        "startup",
        tier=caps.best.value,
        degraded=caps.degraded,
        cors_origins=CORS_ORIGINS,
    )
    yield


app = FastAPI(
    title="Veritas",
    description="A fact knowledge layer for financial documents.",
    version=settings().version,
    lifespan=lifespan,
)

# The web port is configurable because 3000 and 8000 are the two most commonly
# occupied ports on a developer's machine — this project hit exactly that clash
# on the machine it was built on, and runs on 3010/8010 as a result.
#
# **Read through settings(), not os.getenv.** This was `os.getenv("WEB_PORT")`,
# which sees the process environment and not `.env`. Under compose that is fine,
# because compose passes WEB_PORT explicitly. Started by hand it is not: `.env`
# said 3010, the API allowed 3000, and the browser on localhost:3010 had every
# response silently stripped of its allow-origin header.
#
# The symptom is an interface that renders perfectly and shows nothing, with the
# tier badge stuck on "Checking…", because a blocked fetch is indistinguishable
# from a dead backend to everything except the browser console. It has now cost
# this project two separate evenings, which is why the allowed origins are
# logged at startup rather than left to be inferred.
_ports = {settings().web_port, 3000}
CORS_ORIGINS = sorted(
    {f"http://{host}:{port}" for port in _ports for host in ("localhost", "127.0.0.1")}
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(knowledge_router)
app.include_router(ingest_router)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "version": settings().version}


@app.get("/capabilities", tags=["ops"])
async def capabilities() -> dict:
    """Which extraction tier is reachable right now, and why.

    The UI renders this as a banner. A reviewer should never have to guess
    whether they are seeing full extraction or the degraded fallback.
    """
    return (await detect()).as_dict()
