"""Tests for the provider router.

The behaviours worth pinning down are the ones that cost money or produce a
wrong answer silently: that a cached response is never charged, that the
provider's own count beats ours, that a large document goes local even when a
key is present, and that tier 3 is never selected as a speed optimisation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from core.extract.gateway import LLMResponse
from core.extract.spot import Candidate, DocumentSpots, PageSpots
from core.route.estimate import CHARS_PER_TOKEN_PRIOR, estimate_document
from core.route.ledger import FREE_TIER, TokenLedger
from core.route.router import (
    GROQ_ADJUDICATION_RESERVE,
    GROQ_SECONDS_PER_REQUEST,
    OLLAMA_SECONDS_PER_CANDIDATE,
    _groq_eta,
    _ollama_eta,
    plan_pages,
    route_document,
)
from core.settings import Tier


# ── fixtures ─────────────────────────────────────────────────────────────────


WINDOW_TEXT = "Revenue from operations {t} crore for the year ended March 31, 2024"


def make_candidate(page: int, text: str = "7,225", kind: str = "money") -> Candidate:
    window = WINDOW_TEXT.format(t=text)
    return Candidate(
        id=uuid4(),
        page=page,
        block_id=uuid4(),
        block_kind="text",
        kind=kind,
        text=text,
        char_start=window.index(text),
        char_end=window.index(text) + len(text),
        window=window,
        window_start=0,
        tight=f"₹{text} crore",
    )


def make_spots(pages: dict[int, int]) -> DocumentSpots:
    """pages maps page number → how many candidates that page carries."""
    return DocumentSpots(
        pages=[
            PageSpots(page=n, candidates=[make_candidate(n) for _ in range(count)])
            for n, count in pages.items()
        ]
    )


@pytest.fixture
def ledger(tmp_path) -> TokenLedger:
    return TokenLedger(tmp_path / "ledger.jsonl")


# ── the ledger ───────────────────────────────────────────────────────────────


def test_a_fresh_ledger_reports_the_whole_free_tier(ledger):
    remaining = ledger.remaining("groq")
    assert remaining.tokens == FREE_TIER["daily_tokens"]
    assert remaining.requests == FREE_TIER["daily_requests"]
    assert not remaining.exhausted


def test_usage_accumulates_across_separate_writes(ledger):
    ledger.record("groq", prompt_tokens=1_000, completion_tokens=500)
    ledger.record("groq", prompt_tokens=2_000, completion_tokens=250)

    used = ledger.usage_today("groq")
    assert used.tokens == 3_750
    assert used.requests == 2
    assert ledger.remaining("groq").tokens == FREE_TIER["daily_tokens"] - 3_750


def test_a_second_ledger_object_reads_what_the_first_one_wrote(tmp_path):
    """The ledger has to survive process restarts, or an overnight run that is
    killed and resumed would believe it had a full allowance every time."""
    path = tmp_path / "ledger.jsonl"
    TokenLedger(path).record("groq", prompt_tokens=5_000, completion_tokens=1_000)
    assert TokenLedger(path).usage_today("groq").tokens == 6_000


def test_providers_are_accounted_separately(ledger):
    ledger.record("groq", prompt_tokens=1_000)
    ledger.record("ollama", prompt_tokens=900_000)
    assert ledger.usage_today("groq").tokens == 1_000
    assert ledger.remaining("groq").tokens == FREE_TIER["daily_tokens"] - 1_000


def test_yesterdays_spend_does_not_count_against_today(ledger):
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text(
        json.dumps({"t": "usage", "at": yesterday, "provider": "groq", "pt": 199_000, "ct": 0,
                    "req": 1, "chars": 0}) + "\n",
        encoding="utf-8",
    )
    assert ledger.remaining("groq").tokens == FREE_TIER["daily_tokens"]


def test_a_cached_response_is_never_charged(ledger):
    """Re-runs are guaranteed free and byte-identical. If the ledger charged
    for cache hits, the second run of an unchanged pipeline would consume a
    day's budget without making a single call."""
    cached = LLMResponse(
        text="{}", parsed={}, provider="groq", model="gpt-oss-120b",
        prompt_tokens=4_000, completion_tokens=1_000, cached=True,
    )
    ledger.record_response(cached)
    assert ledger.usage_today("groq").tokens == 0


def test_a_live_response_is_charged_and_reconciled(ledger):
    live = LLMResponse(
        text="{}", parsed={}, provider="groq", model="gpt-oss-120b",
        prompt_tokens=4_000, completion_tokens=1_000,
        rate_limit={"x-ratelimit-remaining-requests": "812"},
    )
    ledger.record_response(live, prompt_chars=14_000)

    used = ledger.usage_today("groq")
    assert used.tokens == 5_000
    assert used.prompt_chars == 14_000
    assert ledger.remaining("groq").requests == 812


def test_the_providers_own_request_count_beats_ours(ledger):
    """The header knows about calls from other machines and other processes.
    Our count only knows about this one."""
    for _ in range(3):
        ledger.record("groq", prompt_tokens=10)
    assert ledger.remaining("groq").requests == FREE_TIER["daily_requests"] - 3

    ledger.reconcile("groq", {"x-ratelimit-remaining-requests": "17"})
    remaining = ledger.remaining("groq")
    assert remaining.requests == 17
    assert remaining.requests_source == "provider"


def test_the_per_minute_token_header_is_not_mistaken_for_daily_headroom(ledger):
    """x-ratelimit-remaining-tokens is a per-minute window. Reading it as daily
    headroom would be a 25x error in the optimistic direction."""
    ledger.reconcile("groq", {"x-ratelimit-remaining-tokens": "7500"})
    assert ledger.remaining("groq").tokens == FREE_TIER["daily_tokens"]
    assert ledger.remaining("groq").tokens_source == "ledger"


def test_a_torn_final_line_does_not_break_accounting(ledger):
    ledger.record("groq", prompt_tokens=1_000)
    with ledger.path.open("a", encoding="utf-8") as f:
        f.write('{"t": "usage", "at": "2026-09-')  # killed mid-write
    assert ledger.usage_today("groq").tokens == 1_000


def test_chars_per_token_is_withheld_until_there_is_enough_evidence(ledger):
    ledger.record("groq", prompt_tokens=100, prompt_chars=350)
    assert ledger.observed_chars_per_token("groq") is None

    ledger.record("groq", prompt_tokens=10_000, prompt_chars=35_000)
    observed = ledger.observed_chars_per_token("groq")
    assert observed == pytest.approx(3.5, abs=0.05)


# ── the estimator ────────────────────────────────────────────────────────────


def test_the_estimate_scales_with_candidates():
    small = estimate_document(make_spots({1: 10}))
    large = estimate_document(make_spots({1: 10, 2: 240, 3: 240}))
    assert large.candidates == 490
    assert large.total_tokens > small.total_tokens * 10


def test_batching_matches_the_extractor():
    """25 candidates is two requests at batch size 24, plus the context read."""
    est = estimate_document(make_spots({1: 25}))
    assert est.requests == 3


def test_dates_are_not_charged_for():
    """extract_page discards dates and durations before batching. Charging for
    them would over-estimate macroeconomic documents badly — those are dense
    with years."""
    spots = DocumentSpots(
        pages=[
            PageSpots(
                page=1,
                candidates=[make_candidate(1) for _ in range(5)]
                + [make_candidate(1, "2024", kind="date") for _ in range(50)],
            )
        ]
    )
    assert estimate_document(spots).candidates == 5


def test_an_empty_document_still_costs_the_context_read():
    est = estimate_document(make_spots({}))
    assert est.candidates == 0
    assert est.requests == 1
    assert est.total_tokens > 0


def test_the_page_budget_trims_the_estimate():
    spots = make_spots({1: 100, 2: 100, 3: 100})
    assert estimate_document(spots, page_budget=1).candidates == 100
    assert estimate_document(spots, page_budget=3).candidates == 300


def test_the_estimate_is_identical_across_runs():
    spots = make_spots({1: 200, 2: 150, 3: 90})
    first = estimate_document(spots)
    second = estimate_document(spots)
    assert first == second


def test_calibration_is_reported_honestly():
    assert not estimate_document(make_spots({1: 5})).calibrated
    assert estimate_document(make_spots({1: 5}), chars_per_token=3.9).calibrated
    assert estimate_document(make_spots({1: 5})).chars_per_token == CHARS_PER_TOKEN_PRIOR


# ── the decision ─────────────────────────────────────────────────────────────


def test_a_large_document_goes_local_because_groq_cannot_afford_it(monkeypatch, ledger):
    """The point of the router, and not the reason one first assumes.

    Groq is the *faster* extractor here — 0.88s per candidate against the
    4060's 1.54s. It loses anyway, because 200,000 tokens a day buys about
    1,700 candidates and a real filing has far more than that. Affordability,
    not speed, is what sends bulk work to the laptop."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    _clear_settings_cache()
    monkeypatch.setattr("core.route.router.probe_ollama", lambda *a, **k: (True, "qwen3:8b ready"))

    spots = make_spots({n: 240 for n in range(1, 21)})  # 4,800 candidates
    decision = route_document(spots, ledger=ledger)

    assert decision.tier is Tier.OLLAMA
    assert "spendable" in decision.explain()
    assert not decision.degraded


def test_a_medium_document_goes_to_groq_because_it_is_faster(monkeypatch, ledger):
    """The corrected intuition, pinned down so it cannot quietly regress: while
    the work fits in the daily budget, the cloud tier is both quicker and
    better, and there is no trade to make."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    _clear_settings_cache()
    monkeypatch.setattr("core.route.router.probe_ollama", lambda *a, **k: (True, "qwen3:8b ready"))

    spots = make_spots({n: 60 for n in range(1, 12)})  # 660 candidates, ~77k tokens
    decision = route_document(spots, ledger=ledger)

    assert decision.tier is Tier.GROQ
    assert "faster here" in decision.explain()


def test_a_small_document_goes_to_groq(monkeypatch, ledger):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    _clear_settings_cache()
    monkeypatch.setattr("core.route.router.probe_ollama", lambda *a, **k: (True, "qwen3:8b ready"))

    decision = route_document(make_spots({1: 12, 2: 8}), ledger=ledger)
    assert decision.tier is Tier.GROQ


def test_an_exhausted_budget_falls_to_ollama(monkeypatch, ledger):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    _clear_settings_cache()
    monkeypatch.setattr("core.route.router.probe_ollama", lambda *a, **k: (True, "qwen3:8b ready"))
    ledger.record("groq", prompt_tokens=195_000)

    decision = route_document(make_spots({1: 12}), ledger=ledger)
    assert decision.tier is Tier.OLLAMA
    assert "spendable" in decision.explain()


def test_budget_is_reserved_for_adjudication(monkeypatch, ledger):
    """A small document must not be allowed to spend the last of the allowance,
    or the adjudicator and explainer have nothing left to run on."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    _clear_settings_cache()
    monkeypatch.setattr("core.route.router.probe_ollama", lambda *a, **k: (True, "ready"))
    ledger.record("groq", prompt_tokens=FREE_TIER["daily_tokens"] - GROQ_ADJUDICATION_RESERVE)

    decision = route_document(make_spots({1: 5}), ledger=ledger)
    assert decision.tier is Tier.OLLAMA
    assert "reserved for adjudication" in decision.explain()


def test_no_key_and_no_ollama_is_deterministic_and_says_so(monkeypatch, ledger):
    monkeypatch.setenv("GROQ_API_KEY", "")
    _clear_settings_cache()
    monkeypatch.setattr(
        "core.route.router.probe_ollama", lambda *a, **k: (False, "nothing listening")
    )

    decision = route_document(make_spots({1: 20}), ledger=ledger)
    assert decision.tier is Tier.DETERMINISTIC
    assert decision.degraded
    assert decision.banner and "deterministic mode" in decision.banner


def test_deterministic_is_never_chosen_for_speed(monkeypatch, ledger):
    """Tier 3 is a quality cliff, not a fast lane. Even a document that would
    take hours on the local GPU stays on the local GPU."""
    monkeypatch.setenv("GROQ_API_KEY", "")
    _clear_settings_cache()
    monkeypatch.setattr("core.route.router.probe_ollama", lambda *a, **k: (True, "ready"))

    decision = route_document(make_spots({n: 240 for n in range(1, 40)}), ledger=ledger)
    assert decision.tier is Tier.OLLAMA
    assert decision.eta_seconds > 3_600


def test_no_key_but_ollama_present_uses_ollama(monkeypatch, ledger):
    """The reviewer's likely setup, and the one the corpus run uses."""
    monkeypatch.setenv("GROQ_API_KEY", "")
    _clear_settings_cache()
    monkeypatch.setattr("core.route.router.probe_ollama", lambda *a, **k: (True, "ready"))

    decision = route_document(make_spots({1: 30}), ledger=ledger)
    assert decision.tier is Tier.OLLAMA
    assert "no GROQ_API_KEY" in decision.explain()


def test_forcing_a_tier_skips_assessment(ledger):
    decision = route_document(make_spots({1: 10}), ledger=ledger, force=Tier.DETERMINISTIC)
    assert decision.tier is Tier.DETERMINISTIC
    assert "forced" in decision.explain()


def test_the_decision_always_carries_its_reasoning(monkeypatch, ledger):
    monkeypatch.setenv("GROQ_API_KEY", "")
    _clear_settings_cache()
    monkeypatch.setattr("core.route.router.probe_ollama", lambda *a, **k: (True, "ready"))

    decision = route_document(make_spots({1: 40}), ledger=ledger)
    assert len(decision.trace) >= 3
    assert any("estimate:" in line for line in decision.trace)
    assert any(line.startswith("→") for line in decision.trace)


# ── eta model ────────────────────────────────────────────────────────────────


def test_groq_eta_is_dominated_by_the_per_minute_token_throttle():
    """Not by request latency and not by the 30/minute request limit — at batch
    size 24 the tokens run out first, which is why the estimator bothers to
    count prompt characters at all."""
    est = estimate_document(make_spots({n: 100 for n in range(1, 20)}))
    assert _groq_eta(est) > est.requests * GROQ_SECONDS_PER_REQUEST
    assert _groq_eta(est) > est.requests / FREE_TIER["requests_per_minute"] * 60


def test_the_two_model_tiers_are_within_a_few_percent_on_real_documents():
    """The measurement that settled it, pinned to the figures actually billed.

    Not derived from synthetic candidates — those carry a short context window
    and make Groq look better than it is. These are the numbers from a real
    invoice over two pages of the earnings deck: 38,569 prompt + 15,160
    completion tokens for 261 candidates.

    1.544s per candidate on Groq against 1.540s on the 4060 is a dead heat, and
    the point of asserting it is that neither tier can be described as the fast
    one. If a prompt change moves this materially, the router's whole speed
    comparison needs revisiting and this test should be what says so."""
    billed_tokens, candidates = 53_729, 261
    per_candidate = billed_tokens / candidates

    groq_seconds = per_candidate / (FREE_TIER["tokens_per_minute"] / 60)
    assert groq_seconds == pytest.approx(OLLAMA_SECONDS_PER_CANDIDATE, rel=0.05)

    # And the daily cap, which is the constraint that actually decides anything.
    affordable = FREE_TIER["daily_tokens"] / per_candidate
    assert 900 < affordable < 1_100  # ~10 dense pages, across every document, per day


def test_ollama_eta_is_linear_in_candidates():
    one = _ollama_eta(estimate_document(make_spots({1: 100})))
    two = _ollama_eta(estimate_document(make_spots({1: 100, 2: 100})))
    assert two == pytest.approx(one * 2, rel=0.01)


# ── page planning ────────────────────────────────────────────────────────────


def test_pages_are_planned_densest_first():
    spots = make_spots({1: 5, 2: 200, 3: 40})
    assert plan_pages(spots, tier=Tier.OLLAMA) == [2, 3, 1]


def test_a_time_budget_trims_but_never_empties():
    spots = make_spots({1: 200, 2: 200, 3: 200})
    planned = plan_pages(spots, tier=Tier.OLLAMA, seconds_budget=1.0)
    assert planned == [1]  # the densest page always runs, budget or not


def test_no_budget_means_every_page():
    spots = make_spots({1: 200, 2: 200, 3: 200})
    assert len(plan_pages(spots, tier=Tier.OLLAMA)) == 3


# ── helpers ──────────────────────────────────────────────────────────────────



def _clear_settings_cache() -> None:
    from core.settings import settings

    settings.cache_clear()
