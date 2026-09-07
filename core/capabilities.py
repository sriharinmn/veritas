"""Runtime capability detection.

Probes which extraction tier is actually reachable right now, so the UI can state
its mode unambiguously instead of failing halfway through an upload. A reviewer
with no key should be told that plainly, at the top of the screen, along with
what it costs them — not left to infer it from thin results.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx
import structlog

from core.settings import Tier, settings

log = structlog.get_logger(__name__)

PROBE_TIMEOUT = 2.5


@dataclass
class TierStatus:
    tier: Tier
    available: bool
    detail: str
    models: list[str] = field(default_factory=list)


@dataclass
class Capabilities:
    tiers: list[TierStatus]

    @property
    def best(self) -> Tier:
        for t in self.tiers:
            if t.available:
                return t.tier
        return Tier.DETERMINISTIC

    @property
    def degraded(self) -> bool:
        return self.best is Tier.DETERMINISTIC

    def as_dict(self) -> dict:
        return {
            "best_tier": self.best.value,
            "degraded": self.degraded,
            "tiers": [
                {
                    "tier": t.tier.value,
                    "available": t.available,
                    "detail": t.detail,
                    "models": t.models,
                }
                for t in self.tiers
            ],
        }


async def _probe_groq() -> TierStatus:
    s = settings()
    if not s.groq_api_key:
        return TierStatus(
            Tier.GROQ,
            False,
            "No GROQ_API_KEY set. A free key takes about a minute at console.groq.com/keys.",
        )
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as c:
            r = await c.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {s.groq_api_key}"},
            )
        if r.status_code != 200:
            return TierStatus(Tier.GROQ, False, f"Groq responded {r.status_code}.")
        ids = [m["id"] for m in r.json().get("data", [])]
        if s.groq_model not in ids:
            return TierStatus(
                Tier.GROQ, False, f"{s.groq_model} not available on this key.", ids[:20]
            )
        return TierStatus(Tier.GROQ, True, f"Ready — {s.groq_model}, strict schema mode.", ids[:20])
    except Exception as e:  # noqa: BLE001 — a probe must never raise
        return TierStatus(Tier.GROQ, False, f"Unreachable: {type(e).__name__}.")


async def _probe_ollama() -> TierStatus:
    s = settings()
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as c:
            r = await c.get(f"{s.ollama_host.rstrip('/')}/api/tags")
        if r.status_code != 200:
            return TierStatus(Tier.OLLAMA, False, f"Ollama responded {r.status_code}.")
        names = [m["name"] for m in r.json().get("models", [])]
        # Ollama reports tags as "qwen3:8b"; accept an untagged match too.
        want = s.ollama_model
        if not any(n == want or n.split(":")[0] == want.split(":")[0] for n in names):
            return TierStatus(
                Tier.OLLAMA, False, f"Reachable, but {want} is not pulled. Run: ollama pull {want}", names
            )
        return TierStatus(Tier.OLLAMA, True, f"Ready — {want} on the host.", names)
    except Exception as e:  # noqa: BLE001
        return TierStatus(
            Tier.OLLAMA,
            False,
            f"No Ollama at {s.ollama_host} ({type(e).__name__}). Optional — see .env.example.",
        )


async def detect() -> Capabilities:
    groq, ollama = await asyncio.gather(_probe_groq(), _probe_ollama())
    deterministic = TierStatus(
        Tier.DETERMINISTIC,
        True,
        "Always available. Rule-based extraction with local ONNX embeddings, "
        "no network. Materially lower recall, and semantic facts are not extracted.",
    )
    return Capabilities(tiers=[groq, ollama, deterministic])
