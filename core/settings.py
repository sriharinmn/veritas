"""Runtime configuration and provider capability detection.

The important idea here is that provider availability is *discovered at runtime*,
not asserted by configuration. A reviewer who clones this repo has no API key and
no Ollama host, and the application must come up cleanly, say so plainly, and
still be useful. See plan.md sections 2.1 and 5.2.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Tier(StrEnum):
    """Extraction tiers, in descending order of capability.

    Selected per document rather than per request: mixing extractors within one
    document would make `provenance.extractor` vary inside a single document and
    blind the evals, which could then no longer attribute a failure to the model
    rather than to the router.
    """

    GROQ = "groq"
    OLLAMA = "ollama"
    DETERMINISTIC = "deterministic"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://veritas:veritas@db:5432/veritas"
    log_level: str = "INFO"

    # Tier 1 — Groq. Free tier: 30 RPM / 1K RPD / 8K TPM / 200K TPD.
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    # Tier 2 — Ollama, native on the host, never in a container. See ADR-0002.
    ollama_host: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen3:8b"
    ollama_vision_model: str = "qwen2.5vl:7b"

    # Embeddings are local ONNX and never require a key or the network.
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # Optional alternatives.
    gemini_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Router budgets. The safety margin exists because token estimation is
    # approximate, and a job that dies at 80% is worse than one that never
    # started on that tier.
    groq_daily_token_budget: int = 200_000
    router_safety_margin: float = 1.4

    storage_dir: str = "/app/storage"
    version: str = Field(default="0.1.0")


@lru_cache
def settings() -> Settings:
    return Settings()
