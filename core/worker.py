"""Ingest worker.

Polls the Postgres-backed job queue and runs the ingest pipeline. A dedicated
queue service (Redis, Celery) would be reasonable at scale; at a workload of a
handful of documents it would be one more container that can fail during a
reviewer's first `docker compose up`.

P0: a heartbeat loop that proves the worker starts, reaches the database, and
reports which extraction tier it can see. The pipeline lands in P1-P4.
"""

from __future__ import annotations

import asyncio
import signal

import structlog

from core.capabilities import detect
from core.settings import settings

log = structlog.get_logger(__name__)

POLL_INTERVAL = 2.0


class Worker:
    def __init__(self) -> None:
        self._stop = asyncio.Event()

    def request_stop(self, *_: object) -> None:
        log.info("worker.stop_requested")
        self._stop.set()

    async def run(self) -> None:
        caps = await detect()
        log.info(
            "worker.start",
            tier=caps.best.value,
            degraded=caps.degraded,
            database=settings().database_url.split("@")[-1],
        )
        for t in caps.tiers:
            log.info("worker.tier", tier=t.tier.value, available=t.available, detail=t.detail)

        while not self._stop.is_set():
            # P1-P4: claim the next queued document and run the pipeline.
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=POLL_INTERVAL)
            except TimeoutError:
                pass

        log.info("worker.stopped")


def main() -> None:
    worker = Worker()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:
            signal.signal(sig, worker.request_stop)  # Windows
    loop.run_until_complete(worker.run())


if __name__ == "__main__":
    main()
