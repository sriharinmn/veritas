"""Veritas API.

The interface the assignment asks for: upload PDFs, inspect the resulting facts,
their evidence, and the relationships between them.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.capabilities import detect
from core.settings import settings

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    caps = await detect()
    app.state.capabilities = caps
    log.info("startup", tier=caps.best.value, degraded=caps.degraded)
    yield


app = FastAPI(
    title="Veritas",
    description="A fact knowledge layer for financial documents.",
    version=settings().version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
