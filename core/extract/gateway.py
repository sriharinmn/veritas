"""The LLM gateway.

One interface over three tiers, chosen per document rather than per request.

The point of the abstraction is not provider-independence for its own sake — it
is that this project has no money. Groq's free tier is 8K tokens per *minute*
and 200K per *day*, which is roughly one hundred-page PDF once, so bulk
extraction has to run on a local GPU while the good cloud model is reserved for
the low-volume work where its quality actually changes an outcome. The gateway
is what makes that split invisible to the rest of the pipeline.

Both real backends support genuine schema-constrained decoding — Groq's
`strict: true` and Ollama's `format` — so a malformed response is a bug, not a
weather condition. Schemas are authored to Groq's strict rules (every field
required, `additionalProperties: false`, optionality as a nullable union)
because that is the more restrictive target, and relaxing later is easy while
tightening later means rewriting every model mid-sprint.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.settings import Tier, settings

log = structlog.get_logger(__name__)


class LLMUnavailable(RuntimeError):
    """No provider could serve this request. The caller falls to a lower tier."""


class RateLimited(RuntimeError):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited; retry after {retry_after:.0f}s")
        self.retry_after = retry_after


@dataclass
class LLMResponse:
    text: str
    parsed: Any
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cached: bool = False
    # Groq reports remaining daily *requests* and remaining per-minute *tokens*.
    # There is no header for remaining daily tokens — the one limit that
    # actually binds — which is why core/route keeps its own ledger.
    rate_limit: dict[str, str] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def prompt_hash(system: str, user: str, schema: dict | None, model: str) -> str:
    """Cache key. Includes the schema and the model, so changing either
    correctly invalidates: a different schema is a different question, and a
    different model is a different answer."""
    payload = json.dumps(
        {"s": system, "u": user, "schema": schema, "m": model}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class Gateway(Protocol):
    tier: Tier
    model: str

    async def complete_json(
        self, *, system: str, user: str, schema: dict, max_tokens: int = 2048
    ) -> LLMResponse: ...


def _resolve_host(host: str) -> str:
    """Make one configured host work from inside a container and on the host.

    `host.docker.internal` is the correct address from inside a container and is
    what the compose stack needs. On the host machine it resolves only while
    Docker Desktop is running, and silently stops resolving when it is not — at
    which point every call raises LLMUnavailable and the pipeline degrades to
    its no-model fallbacks *without saying so*.

    That cost real time here: an entire corpus run was queued that would have
    spent hours producing nothing, and a run of confusing empty results was
    misread as a prompt regression before the connection error surfaced. Falling
    back to localhost costs one DNS lookup at construction and removes the whole
    class of confusion.
    """
    if "host.docker.internal" not in host:
        return host

    # A DNS check is not enough, and getting that wrong wasted an hour here.
    # Docker Desktop adds a hosts entry for host.docker.internal, so the name
    # resolves happily on the host machine — but Ollama binds to 127.0.0.1 by
    # default, so nothing is listening at that address. The name resolving and
    # the port answering are different questions, and only the second one
    # matters.
    if _port_open(host):
        return host
    fallback = host.replace("host.docker.internal", "localhost")
    log.info("ollama.host_fallback", configured=host, using=fallback)
    return fallback


def _port_open(url: str, timeout: float = 0.75) -> bool:
    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    try:
        with socket.create_connection(
            (parsed.hostname, parsed.port or 11434), timeout=timeout
        ):
            return True
    except OSError:
        return False


class OllamaGateway:
    """Local inference. Free, unlimited, no rate limits, and on this hardware it
    is *faster* than the throttled cloud tier for anything past ~30 pages."""

    tier = Tier.OLLAMA

    def __init__(
        self, host: str | None = None, model: str | None = None, *, think: bool = False
    ) -> None:
        s = settings()
        self.host = _resolve_host((host or s.ollama_host).rstrip("/"))
        self.model = model or s.ollama_model
        self.think = think

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def complete_json(
        self, *, system: str, user: str, schema: dict, max_tokens: int = 2048
    ) -> LLMResponse:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Ollama constrains decoding to the schema, so the response is valid
            # JSON of the right shape by construction rather than by hope.
            "format": schema,
            "stream": False,
            # qwen3 reasons before answering by default. Measured on this
            # hardware: a three-field extraction took 277 generated tokens and
            # 62 seconds with thinking on, and 46 tokens and 3.2 seconds with it
            # off — six times fewer tokens for identical output. Over a
            # thousand-candidate document that is the difference between a
            # corpus run of hours and one of minutes.
            #
            # Turning it off is right for *this* task specifically: the model is
            # being handed a candidate and asked to type and scope it, which is
            # reading comprehension rather than deduction. The adjudicator, which
            # weighs two claims against each other, is a different job and gets
            # its own setting.
            "think": False,
            "options": {
                "temperature": 0.0,  # extraction is not a creative task
                "num_ctx": 8192,
                "num_predict": max_tokens,
            },
        }
        if self.think:
            body.pop("think")
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=300.0) as c:
                r = await c.post(f"{self.host}/api/chat", json=body)
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"Ollama at {self.host}: {type(e).__name__}") from e

        if r.status_code != 200:
            raise LLMUnavailable(f"Ollama returned {r.status_code}: {r.text[:200]}")

        data = r.json()
        text = data.get("message", {}).get("content", "")
        return LLMResponse(
            text=text,
            parsed=_loads(text),
            provider="ollama",
            model=self.model,
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )


# How many times to wait out a 429 before giving up on the tier. Four waits at
# up to a minute each is the difference between "this document takes a while"
# and "this document silently produced nothing".
MAX_RATE_LIMIT_WAITS = 4
MAX_RATE_LIMIT_SLEEP_S = 70.0


def _header(headers: dict[str, str], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return None


def _duration(raw: str) -> float:
    """Parse Groq's reset headers: "7.66s", "1m2.5s", "120ms"."""
    raw = raw.strip()
    if raw.endswith("ms"):
        try:
            return float(raw[:-2]) / 1000
        except ValueError:
            return 0.0
    total, number = 0.0, ""
    for ch in raw:
        if ch.isdigit() or ch == ".":
            number += ch
        elif ch == "m":
            total += float(number or 0) * 60
            number = ""
        elif ch == "s":
            total += float(number or 0)
            number = ""
    if number:
        total += float(number)
    return total


class GroqGateway:
    """Groq's free tier. Fast and schema-strict, and very tightly rationed.

    Reserved for small documents and for the low-volume adjudication and
    explanation calls, where the quality difference against an 8B local model
    actually changes what a reader is told.
    """

    tier = Tier.GROQ
    BASE = "https://api.groq.com/openai/v1"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        ledger=None,
    ) -> None:
        s = settings()
        self.api_key = api_key or s.groq_api_key
        self.model = model or s.groq_model
        # Booked here rather than by the caller because this is the only place
        # that sees both the usage figures and the rate-limit headers, and a
        # ledger the caller can forget to update is a ledger that is wrong.
        if ledger is None:
            from core.route.ledger import TokenLedger

            ledger = TokenLedger()
        self.ledger = ledger
        # The per-minute window, learned from response headers. None until the
        # first response tells us where we stand.
        self._remaining_tokens: int | None = None
        self._reset_at: float = 0.0

    def _note_window(self, limits: dict[str, str]) -> None:
        remaining = _header(limits, "x-ratelimit-remaining-tokens")
        reset = _header(limits, "x-ratelimit-reset-tokens")
        if remaining is not None:
            try:
                self._remaining_tokens = int(float(remaining))
            except ValueError:
                self._remaining_tokens = None
        if reset is not None:
            self._reset_at = time.monotonic() + _duration(reset)

    async def complete_json(
        self, *, system: str, user: str, schema: dict, max_tokens: int = 2048
    ) -> LLMResponse:
        """Make one call, waiting out the per-minute throttle rather than dying on it.

        The free tier allows 8,000 tokens per minute and one batch of this
        pipeline costs about 5,000, so a document longer than two batches will
        be rate limited — not as an edge case but as the normal path. An
        extractor that treats 429 as an error therefore cannot extract anything
        at all on Groq, which is precisely how this failed: the first document
        stopped after one batch and reported no claims.

        Two mechanisms, in order of preference:

        **Pace proactively.** Every response reports how many tokens are left in
        the current minute and when the window resets. If the next call will not
        fit, wait for the reset before making it. Nothing is wasted and no error
        is provoked.

        **React if overtaken.** A 429 still happens when another process shares
        the key, so honour `retry-after` and try again.
        """
        for attempt in range(MAX_RATE_LIMIT_WAITS + 1):
            await self._await_token_window(max_tokens)
            try:
                return await self._call(
                    system=system, user=user, schema=schema, max_tokens=max_tokens
                )
            except RateLimited as e:
                if attempt >= MAX_RATE_LIMIT_WAITS:
                    raise
                wait = min(e.retry_after + 0.5, MAX_RATE_LIMIT_SLEEP_S)
                log.info("groq.rate_limited", waiting_s=round(wait, 1), attempt=attempt + 1)
                await asyncio.sleep(wait)
        raise LLMUnavailable("unreachable")

    async def _await_token_window(self, needed: int) -> None:
        """Sleep until the per-minute token window can afford the next call."""
        if self._remaining_tokens is None:
            return
        # The estimate is prompt-blind, so be generous: it is better to wait a
        # second too long than to burn a request on a certain 429.
        if self._remaining_tokens > needed * 2:
            return
        wait = max(0.0, self._reset_at - time.monotonic())
        if wait <= 0:
            return
        log.info("groq.pacing", waiting_s=round(wait, 1), remaining=self._remaining_tokens)
        await asyncio.sleep(min(wait + 0.25, MAX_RATE_LIMIT_SLEEP_S))
        self._remaining_tokens = None

    async def _call(
        self, *, system: str, user: str, schema: dict, max_tokens: int
    ) -> LLMResponse:
        if not self.api_key:
            raise LLMUnavailable("No GROQ_API_KEY configured.")

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "schema": schema, "strict": True},
            },
            "temperature": 0.0,
            "max_completion_tokens": max_tokens,
            # gpt-oss is a reasoning model, and on a labelling task that is a
            # liability rather than a feature. Left at its default it spends the
            # whole completion budget on reasoning tokens and returns empty
            # content — whereupon Groq rejects its own output with
            # `json_validate_failed` and the page silently yields nothing.
            #
            # This is the same failure the local tier had, where `think: False`
            # was worth 6x. Reasoning tokens are billed against the 8K/minute
            # throttle exactly like visible ones, so an unbounded think is also
            # the most expensive way to produce nothing.
            #
            # "low" rather than "none": a short deliberation measurably helps on
            # ambiguous table cells, and 53 tokens of it is affordable.
            "reasoning_effort": "low",
        }
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=120.0) as c:
                r = await c.post(
                    f"{self.BASE}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"Groq: {type(e).__name__}") from e

        limits = {k: v for k, v in r.headers.items() if k.lower().startswith("x-ratelimit")}
        self._note_window(limits)

        if r.status_code == 429:
            raise RateLimited(float(r.headers.get("retry-after", "60")))
        if r.status_code == 400 and "json_validate_failed" in r.text:
            # Worth naming rather than lumping in with transport errors: it means
            # the model produced no content within its token budget, which is a
            # budget or reasoning-effort problem on our side, not an outage.
            raise LLMUnavailable(
                "Groq returned no parseable content within max_completion_tokens "
                "— raise the budget or lower reasoning_effort."
            )
        if r.status_code != 200:
            raise LLMUnavailable(f"Groq returned {r.status_code}: {r.text[:200]}")

        data = r.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        response = LLMResponse(
            text=text,
            parsed=_loads(text),
            provider="groq",
            model=self.model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            rate_limit=limits,
        )
        # The prompt character count is what lets the estimator calibrate its
        # chars-per-token ratio against reality instead of staying on a prior.
        self.ledger.record_response(response, prompt_chars=len(system) + len(user))
        return response


class NullGateway:
    """Tier 3. Refuses every call so callers fall through to the rule-based path.

    This exists so that "no provider configured" is an ordinary, well-typed
    state rather than an exception thrown from somewhere deep in the pipeline.
    A reviewer with no API key is the expected case, not an error case.
    """

    tier = Tier.DETERMINISTIC
    model = "deterministic"

    async def complete_json(
        self, *, system: str, user: str, schema: dict, max_tokens: int = 2048
    ) -> LLMResponse:
        raise LLMUnavailable(
            "No LLM provider is configured. Extraction is running in deterministic mode."
        )


def _loads(text: str) -> Any:
    """Parse a constrained-decoding response.

    Both backends guarantee valid JSON, so this should never need to work hard —
    but a model that has been told to think out loud will occasionally wrap its
    answer in prose, and losing a whole page of extraction to that would be a
    silly way to fail.
    """
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : start + (end - start + 1)])
        except json.JSONDecodeError:
            return None
    return None


def build_gateway(tier: Tier) -> Gateway:
    match tier:
        case Tier.GROQ:
            return GroqGateway()
        case Tier.OLLAMA:
            return OllamaGateway()
        case _:
            return NullGateway()
