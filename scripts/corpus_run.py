"""The overnight corpus run — full model-tier extraction over every document.

This produces the claims that become the shipped snapshot, so a reviewer who
clones the repository with no API key still sees the full-quality knowledge
layer. It is the reason the laptop stays on.

Three properties matter more than speed, because a job that runs for hours and
loses everything at hour six is worse than one that never started:

**Resumable.** Every page's claims are appended to a JSONL checkpoint as soon as
they are extracted, and a restart skips pages already present. Kill it, reboot,
run it again — it picks up where it stopped.

**Thermally safe.** Between pages it reads the GPU temperature and pauses if the
card is above the ceiling. A laptop 4060 throttles long before anything is at
risk, but pinning it there unattended for eight hours is not something to do to
somebody's machine.

**Independent of the database.** Persistence does not exist yet, and blocking an
eight-hour run on a schema that is still being designed would be a poor trade.
JSONL is the interchange; the snapshot builder loads it later.

    python -m scripts.corpus_run                 # everything, resuming
    python -m scripts.corpus_run --fresh         # ignore checkpoints
    python -m scripts.corpus_run seed/delhivery/*.pdf
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.extract.gateway import OllamaGateway
from core.extract.llm import extract_page, read_document_context
from core.extract.spot import spot_document
from core.ground.verify import verify_all
from core.parse.pdf import parse_pdf, sha256_file

OUT = Path("evals/corpus")
GPU_CEILING_C = 85
GPU_COOLDOWN_S = 60

# Pages per document, densest first.
#
# Measured on the FY24 annual report: ~200 candidates on a dense page at ~1.3s
# each is about 260 seconds, so a hundred-page filing is nearly eight hours and
# the six-document corpus is well over a day. Left uncapped the run would spend
# the whole night inside document two and never reach the macroeconomic
# documents at all — and those are the ones carrying the reconciled-by-context
# case, where the IMF reports India on calendar years and the Economic Survey
# does not.
#
# Breadth beats depth for the shipped snapshot: a cross-document knowledge layer
# over six documents is the thing being demonstrated, and an exhaustive layer
# over two is not. Density ordering means the cap keeps the financial statements
# and drops the signature pages, which is the right thing to lose.
PAGES_PER_DOC = int(os.environ.get("VERITAS_PAGES_PER_DOC", "26"))

# Delhivery first: it carries the corroboration and contradiction cases. The
# macro documents carry the reconciled-by-context case and come second, so a run
# that is cut short still leaves the most demonstrable material on disk.
DEFAULT_DOCS = [
    "seed/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf",
    "seed/delhivery/02-delhivery-annual-report-fy24-excerpt.pdf",
    "seed/delhivery/01-delhivery-prospectus-2022-excerpt.pdf",
    "seed/india-macroeconomy/01-india-economic-survey-2024-25-excerpt.pdf",
    "seed/india-macroeconomy/02-rbi-annual-report-2024-25-excerpt.pdf",
    "seed/india-macroeconomy/03-imf-india-2025-article-iv-excerpt.pdf",
]


def gpu_temperature() -> int | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        return int(out.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001 — the guard must never be the thing that fails
        return None


def cool_if_hot(log) -> None:
    t = gpu_temperature()
    if t is not None and t >= GPU_CEILING_C:
        log(f"    GPU at {t}°C — pausing {GPU_COOLDOWN_S}s")
        time.sleep(GPU_COOLDOWN_S)


def done_pages(path: Path) -> set[int]:
    if not path.exists():
        return set()
    seen: set[int] = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                seen.add(json.loads(line)["page"])
            except Exception:  # noqa: BLE001 — a torn final line is expected after a kill
                continue
    return seen


async def run_document(path: str, gateway: OllamaGateway, *, fresh: bool, log) -> dict:
    src = Path(path)
    OUT.mkdir(parents=True, exist_ok=True)
    checkpoint = OUT / f"{src.stem}.jsonl"
    if fresh and checkpoint.exists():
        checkpoint.unlink()

    started = time.perf_counter()
    doc = parse_pdf(src)
    spots = spot_document(doc)
    order = spots.pages_by_density()
    already = done_pages(checkpoint)
    budget = order[:PAGES_PER_DOC]
    todo = [p for p in budget if p not in already]

    log(f"\n  {src.name}")
    log(f"    {doc.page_count} pages · {spots.total:,} candidates · "
        f"budget {len(budget)} densest · {len(already)} done · {len(todo)} to go")

    context = await read_document_context(doc, gateway)
    log(f"    context: entity={context.entity!r} scale={context.reporting_scale!r} "
        f"currency={context.reporting_currency!r} basis={context.default_basis.value}")

    doc_id = uuid4()
    run_id = uuid4()
    by_number = {p.number: p for p in doc.pages}
    totals = {"claims": 0, "grounded": 0, "quarantined": 0, "pages": 0, "failed": 0}

    with checkpoint.open("a", encoding="utf-8") as sink:
        for i, number in enumerate(todo, 1):
            page = by_number[number]
            cool_if_hot(log)
            t0 = time.perf_counter()
            try:
                claims = await extract_page(
                    page, gateway, document_id=doc_id, context=context, run_id=run_id
                )
            except Exception as e:  # noqa: BLE001 — one bad page must not end the run
                totals["failed"] += 1
                log(f"    p{number:<4} FAILED {type(e).__name__}: {str(e)[:90]}")
                continue

            grounded, quarantined, report = verify_all(claims, {number: page})
            record = {
                "page": number,
                "document": src.name,
                "document_sha256": doc.sha256,
                "extracted_at": datetime.now(UTC).isoformat(),
                "claims": [c.model_dump(mode="json") for c in grounded],
                "quarantined": [
                    {"claim": c.model_dump(mode="json"), "reason": r.reason, "detail": r.detail}
                    for c, r in quarantined
                ],
            }
            sink.write(json.dumps(record, default=str) + "\n")
            sink.flush()  # survive a kill

            totals["claims"] += len(claims)
            totals["grounded"] += len(grounded)
            totals["quarantined"] += len(quarantined)
            totals["pages"] += 1

            elapsed = time.perf_counter() - t0
            rate = (time.perf_counter() - started) / max(i, 1)
            eta = rate * (len(todo) - i) / 60
            log(f"    p{number:<4} {len(grounded):3} grounded  {elapsed:5.1f}s  "
                f"[{i}/{len(todo)}]  eta {eta:.0f}m")

    totals["seconds"] = round(time.perf_counter() - started, 1)
    log(f"    → {totals['grounded']:,} grounded, {totals['quarantined']} quarantined, "
        f"{totals['failed']} failed pages, {totals['seconds'] / 60:.1f}m")
    return totals


async def main(paths: list[str], *, fresh: bool) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    logfile = OUT / "run.log"

    def log(msg: str) -> None:
        print(msg, flush=True)
        with logfile.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(UTC).isoformat()} {msg}\n")

    gateway = OllamaGateway()
    log(f"\n{'=' * 72}\ncorpus run · {datetime.now(UTC).isoformat()} · "
        f"model {gateway.model} · {len(paths)} documents")

    grand = {"grounded": 0, "quarantined": 0, "pages": 0, "failed": 0}
    started = time.perf_counter()
    for path in paths:
        if not Path(path).exists():
            log(f"  skipping missing {path}")
            continue
        try:
            t = await run_document(path, gateway, fresh=fresh, log=log)
        except Exception as e:  # noqa: BLE001 — one bad document must not end the run
            log(f"  {path} FAILED: {type(e).__name__}: {str(e)[:140]}")
            continue
        for k in grand:
            grand[k] += t.get(k, 0)

    log(f"\n  TOTAL {grand['grounded']:,} grounded claims over {grand['pages']} pages "
        f"in {(time.perf_counter() - started) / 3600:.2f}h "
        f"({grand['quarantined']} quarantined, {grand['failed']} failed pages)")
    log(f"  checkpoints in {OUT}/\n")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raise SystemExit(
        asyncio.run(main(args or DEFAULT_DOCS, fresh="--fresh" in sys.argv))
    )
