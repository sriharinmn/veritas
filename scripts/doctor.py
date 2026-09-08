"""What this machine can do, and exactly what to run to improve it.

    python -m scripts.doctor           # report
    python -m scripts.doctor --fix     # pull the Ollama model if it is missing

Every capability here degrades rather than fails, which is the right behaviour
and also the dangerous kind: a system that quietly works less well is harder to
notice than one that stops. The embedder proved that — it fell back to surface
matching for the entire build and nothing said so, because the fallback worked.

So this reports the live answer for each tier, and where something is missing it
prints the one command that fixes it. Nothing here changes anything unless you
pass --fix, and even then the only action is a model pull you asked for. A tool
that downloads five gigabytes because you ran a status check would be a bad
tool.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.capabilities import detect
from core.route.router import probe_ollama
from core.settings import settings

OK = "ok"
GAP = "--"


def _line(status: str, name: str, detail: str) -> None:
    print(f"  [{status:^4}] {name:<22} {detail}")


def _remedy(text: str) -> None:
    print(f"         {'':22} → {text}")


def ollama_state() -> tuple[str, str, str | None]:
    """(status, detail, remedy) — distinguishing 'no daemon' from 'no model'.

    These need different messages because they need different actions, and
    collapsing them into one "not available" is how a reviewer concludes the
    local tier is broken when it is one command away.
    """
    s = settings()
    ready, detail = probe_ollama()
    if ready:
        return OK, detail, None
    if "not pulled" in detail:
        return GAP, detail, f"ollama pull {s.ollama_model}    (or: python -m scripts.doctor --fix)"
    return GAP, detail, "install Ollama from https://ollama.com — optional; Groq and tier 3 work without it"


async def main(fix: bool) -> int:
    s = settings()
    caps = await detect()

    print("\nVeritas — capability report\n")

    print("extraction tiers, best first")
    groq = next((t for t in caps.tiers if t.tier.value == "groq"), None)
    if groq and groq.available:
        _line(OK, "1 · Groq", groq.detail)
    else:
        _line(GAP, "1 · Groq", groq.detail if groq else "not configured")
        _remedy("free key at https://console.groq.com/keys, then put GROQ_API_KEY in .env")

    status, detail, remedy = ollama_state()
    _line(status, "2 · Ollama", detail)
    if remedy:
        _remedy(remedy)

    _line(OK, "3 · Deterministic", "always available — rules only, ~47% of the model tier's recall")

    print("\nsupporting")
    embedder = caps.as_dict()["embedder"]
    _line(
        OK if embedder["semantic"] else GAP,
        "embeddings",
        f"{embedder['name']} — {embedder['detail']}",
    )
    if not embedder["semantic"]:
        _remedy("pip install fastembed    (predicates currently merge on spelling, not meaning)")

    snapshot = list(__import__("pathlib").Path("evals/corpus").glob("*.jsonl*"))
    _line(
        OK if snapshot else GAP,
        "knowledge layer",
        f"{len(snapshot)} checkpoints on disk" if snapshot else "no checkpoints — the app will be empty",
    )
    if not snapshot:
        _remedy("the shipped snapshot should be in evals/corpus/*.jsonl.gz — check your clone")

    print(f"\nrouting right now: {caps.best.value}"
          + ("  (degraded — the UI says so)" if caps.degraded else ""))

    if fix and status == GAP and "not pulled" in detail:
        if not shutil.which("ollama"):
            print("\nollama is not on PATH; cannot pull.")
            return 1
        print(f"\npulling {s.ollama_model} — this is a few gigabytes and resumes if interrupted\n")
        result = subprocess.run(["ollama", "pull", s.ollama_model])
        return result.returncode

    if fix:
        print("\nnothing to fix.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--fix" in sys.argv)))
