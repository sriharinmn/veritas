"""The Groq gateway under rate limiting.

Worth testing precisely because it is the *normal* path, not an edge case. The
free tier allows 8,000 tokens per minute and one batch of this pipeline costs
about 5,000, so any document longer than two batches will be rate limited. An
extractor that treats 429 as an error therefore cannot extract anything at all,
which is exactly how this failed: the first real Groq document stopped after one
batch and reported no claims.

These tests use a fake transport rather than the network, so they are
deterministic and cost nothing. Sleeps are patched out — what is being tested is
the decision to wait and for how long, not the waiting.
"""

from __future__ import annotations

import json

import httpx
import pytest

from core.extract.gateway import (
    GroqGateway,
    LLMUnavailable,
    RateLimited,
    _duration,
)
from core.route.ledger import TokenLedger

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}


def _ok_response(request: httpx.Request, *, remaining="7000", reset="12s") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps({"ok": True})}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
        },
        headers={
            "x-ratelimit-remaining-tokens": remaining,
            "x-ratelimit-reset-tokens": reset,
            "x-ratelimit-remaining-requests": "900",
        },
        request=request,
    )


def _429(request: httpx.Request, retry_after="9") -> httpx.Response:
    return httpx.Response(
        429, json={"error": {"message": "rate limited"}},
        headers={"retry-after": retry_after}, request=request,
    )


@pytest.fixture
def no_sleep(monkeypatch):
    """Record what the gateway decided to wait, without waiting."""
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr("core.extract.gateway.asyncio.sleep", fake_sleep)
    return waited


def _gateway(handler, tmp_path, monkeypatch) -> GroqGateway:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("core.extract.gateway.httpx.AsyncClient", client_factory)
    return GroqGateway(api_key="gsk_test", ledger=TokenLedger(tmp_path / "ledger.jsonl"))


# ── reacting to a 429 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_429_is_waited_out_and_retried(tmp_path, monkeypatch, no_sleep):
    """The failure that produced an empty document. A 429 is not an outage."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _429(request) if calls["n"] == 1 else _ok_response(request)

    gateway = _gateway(handler, tmp_path, monkeypatch)
    resp = await gateway.complete_json(system="s", user="u", schema=SCHEMA)

    assert resp.parsed == {"ok": True}
    assert calls["n"] == 2
    assert no_sleep and 9 <= no_sleep[0] <= 10.5, "should honour retry-after"


@pytest.mark.asyncio
async def test_retry_after_is_honoured_rather_than_a_fixed_backoff(
    tmp_path, monkeypatch, no_sleep
):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _429(request, retry_after="34") if calls["n"] == 1 else _ok_response(request)

    gateway = _gateway(handler, tmp_path, monkeypatch)
    await gateway.complete_json(system="s", user="u", schema=SCHEMA)

    assert 34 <= no_sleep[0] <= 35.5


@pytest.mark.asyncio
async def test_persistent_rate_limiting_eventually_gives_up(tmp_path, monkeypatch, no_sleep):
    """Bounded, so a caller can fall to a lower tier rather than hang forever."""
    gateway = _gateway(lambda r: _429(r), tmp_path, monkeypatch)

    with pytest.raises(RateLimited):
        await gateway.complete_json(system="s", user="u", schema=SCHEMA)


# ── pacing, so the 429 never happens ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_gateway_paces_itself_when_the_window_is_nearly_spent(
    tmp_path, monkeypatch, no_sleep
):
    """Better than reacting: the response headers say how much of the minute is
    left, so a call that will not fit waits for the reset instead of provoking
    an error and burning a request on it."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok_response(request, remaining="200", reset="20s")

    gateway = _gateway(handler, tmp_path, monkeypatch)
    await gateway.complete_json(system="s", user="u", schema=SCHEMA, max_tokens=1000)
    assert not no_sleep, "nothing is known before the first response"

    await gateway.complete_json(system="s", user="u", schema=SCHEMA, max_tokens=1000)
    assert no_sleep, "200 tokens left cannot serve a 1,000-token call"
    assert 19 <= no_sleep[0] <= 21


@pytest.mark.asyncio
async def test_a_healthy_window_is_not_paced(tmp_path, monkeypatch, no_sleep):
    gateway = _gateway(lambda r: _ok_response(r, remaining="7500", reset="30s"), tmp_path,
                       monkeypatch)
    await gateway.complete_json(system="s", user="u", schema=SCHEMA, max_tokens=500)
    await gateway.complete_json(system="s", user="u", schema=SCHEMA, max_tokens=500)
    assert not no_sleep


# ── the empty-content failure ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_content_is_named_rather_than_reported_as_an_outage(
    tmp_path, monkeypatch, no_sleep
):
    """gpt-oss is a reasoning model. Left unbounded it spends the whole
    completion budget thinking and returns nothing, and Groq then rejects its
    own output. That is a budget problem on our side, not a provider failure,
    and the message should say so."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": "json_validate_failed", "failed_generation": ""}},
            request=request,
        )

    gateway = _gateway(handler, tmp_path, monkeypatch)
    with pytest.raises(LLMUnavailable, match=r"reasoning_effort|max_completion_tokens"):
        await gateway.complete_json(system="s", user="u", schema=SCHEMA)


# ── the ledger is charged from the same place ────────────────────────────────


@pytest.mark.asyncio
async def test_usage_is_booked_and_headers_reconciled(tmp_path, monkeypatch, no_sleep):
    ledger = TokenLedger(tmp_path / "ledger.jsonl")
    transport = httpx.MockTransport(lambda r: _ok_response(r))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "core.extract.gateway.httpx.AsyncClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )

    gateway = GroqGateway(api_key="gsk_test", ledger=ledger)
    await gateway.complete_json(system="s", user="u", schema=SCHEMA)

    used = ledger.usage_today("groq")
    assert used.tokens == 1500
    assert ledger.remaining("groq").requests == 900  # from the header, not our count


# ── header parsing ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [("7.66s", 7.66), ("1m2.5s", 62.5), ("120ms", 0.12), ("2m", 120.0), ("0s", 0.0)],
)
def test_reset_headers_parse(raw, seconds):
    assert _duration(raw) == pytest.approx(seconds)
