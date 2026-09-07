"""Embeddings — local, in-process, and never requiring a key.

`fastembed` runs BAAI/bge-small-en-v1.5 as ONNX on the CPU inside the container.
384 dimensions, a few milliseconds per string, no network after the first model
fetch and no credential at any point. That is a deliberate choice rather than a
convenience: it removes the embedding provider from the credential story
entirely, so a reviewer with no API key still gets working entity resolution,
predicate clustering and candidate generation rather than a degraded stub.

The `Embedder` protocol exists so the resolver can be tested without downloading
a model, and so a different backend could be swapped in without touching the
ontology logic.
"""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol

import structlog

log = structlog.get_logger(__name__)

DIM = 384


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class FastEmbedEmbedder:
    """The real thing. Lazily loaded so importing this module stays cheap."""

    dim = DIM

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self.model_name = model_name
        self._model = None

    def _ensure(self):
        if self._model is None:
            from fastembed import TextEmbedding  # imported late: ~1s and 100MB

            log.info("embedder.loading", model=self.model_name)
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure()
        return [list(map(float, v)) for v in model.embed(texts)]


class HashingEmbedder:
    """A deterministic character-n-gram embedder, for tests and as a last resort.

    It is genuinely useful, not a placeholder that returns zeros: character
    trigrams capture enough surface similarity to match "Delhivery Ltd" against
    "Delhivery Limited". What it cannot do is recognise that "revenue from
    operations" and "turnover" mean the same thing, which is exactly the job the
    real embedder is there for — so if the ontology ever silently falls back to
    this, semantic merging quietly stops working and the eval should show it.
    """

    dim = DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    @staticmethod
    @lru_cache(maxsize=4096)
    def _one(text: str) -> tuple[float, ...]:
        s = re.sub(r"\s+", " ", (text or "").lower().strip())
        vec = [0.0] * DIM
        padded = f"  {s}  "
        for i in range(len(padded) - 2):
            gram = padded[i : i + 3]
            h = int.from_bytes(hashlib.blake2b(gram.encode(), digest_size=4).digest(), "big")
            vec[h % DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return tuple(v / norm for v in vec)

    def embed_one(self, text: str) -> list[float]:
        return list(self._one(text))


def build_embedder(prefer_local_model: bool = True) -> Embedder:
    """Return the best available embedder, degrading rather than failing.

    A reviewer running offline, or on a machine where the model fetch is
    blocked, still gets an ontology — a weaker one, and the log says so.
    """
    if prefer_local_model:
        try:
            e = FastEmbedEmbedder()
            e.embed(["warmup"])
            return e
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "embedder.fallback",
                reason=str(exc)[:160],
                effect="semantic predicate merging is disabled; surface matching only",
            )
    return HashingEmbedder()
