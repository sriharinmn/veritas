"""The provider router — which tier extracts this document, and why.

The decision is made **once per document, before extraction starts**, never per
request. Mixing extractors inside one document would make `provenance.extractor`
vary within a single document and blind the evals: a quality difference could no
longer be attributed to the model rather than to the router.

Groq's free tier is rationed on four axes at once:

    30 requests/minute · 1,000 requests/day · 8,000 tokens/minute · 200,000/day

Working out which one binds is the whole job, and it took two wrong answers to
get there. The first was that the 8K/minute throttle makes Groq slower than the
laptop past about thirty pages. The second, after the estimator seemed to
disprove that, was the reverse: Groq 1.75x faster. Both were reasoning from a
chars-per-token prior nobody had checked.

Measured against a real invoice — `scripts/route_check.py --spend 2` — the ratio
is 2.30 chars per token, not the 3.40 assumed, because financial pages tokenise
far worse than prose. That works out to 148 prompt plus 58 completion tokens per
candidate, so Groq costs **1.544 seconds per candidate against the 4060's 1.540**.
A dead heat, within 0.3%. The speed comparison is not the interesting question
and never was.

The daily cap is. 200,000 tokens buys roughly 1,000 candidates — about **ten
dense pages, across every document, per day**. A single 100-page filing is
several days' entire allowance. So the tier split is not "cloud for speed, local
for scale": it is that the good model is rationed to roughly one chapter a day
and the laptop is not rationed at all.

Wall-clock is still compared, because the two tiers are close enough that
widening the context window or shrinking the batch would separate them, and
because a router that reports *which* constraint decided is worth more than one
that only reports its answer.

One rule is absolute: **tier 3 is never chosen for speed.** Deterministic mode
is a quality cliff, not a fast lane. It is selected only when nothing else can
run at all, and when it is selected it says so loudly enough that nobody mistakes
its output for the real thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import structlog

from core.extract.gateway import _port_open
from core.route.estimate import TokenEstimate, estimate_document
from core.route.ledger import FREE_TIER, TokenLedger
from core.settings import Tier, settings

log = structlog.get_logger(__name__)

# Measured on this host (Ryzen 7 7840HS / RTX 4060 Laptop, qwen3:8b Q4_K_M,
# think disabled, batch 24): 0.65 candidates/second sustained. Generation is the
# bottleneck and scales linearly with candidates, so this is close to a constant.
OLLAMA_SECONDS_PER_CANDIDATE = 1.54

# Round-trip for one batch against Groq's gpt-oss-120b, including network.
# Batches are issued serially by the extractor, so this multiplies.
GROQ_SECONDS_PER_REQUEST = 1.8

# Prefer the better model unless it is materially slower. Below this ratio the
# quality difference is worth the wait; above it, waiting stops being a trade
# and starts being a worse product.
SPEED_TOLERANCE = 1.5

# Held back from the daily cap so the adjudicator and the explainer can still
# run after a document has been ingested. Those are low-volume calls where a
# 120B model genuinely changes what a reader is told, and spending the whole
# allowance on bulk labelling — which the local model does perfectly well —
# would be the wrong way round.
GROQ_ADJUDICATION_RESERVE = 30_000

DETERMINISTIC_BANNER = (
    "Running in deterministic mode. No language model is reachable, so facts are "
    "extracted by rules alone: recall is materially lower and predicate labels are "
    "weaker. Every claim shown is still grounded in its source span — nothing here "
    "is invented — but this is not the system's full output."
)


@dataclass
class TierAssessment:
    tier: Tier
    available: bool
    eligible: bool
    eta_seconds: float | None
    reason: str


@dataclass
class RoutingDecision:
    tier: Tier
    estimate: TokenEstimate
    trace: list[str] = field(default_factory=list)
    assessments: list[TierAssessment] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return self.tier is Tier.DETERMINISTIC

    @property
    def banner(self) -> str | None:
        return DETERMINISTIC_BANNER if self.degraded else None

    @property
    def eta_seconds(self) -> float | None:
        for a in self.assessments:
            if a.tier is self.tier:
                return a.eta_seconds
        return None

    def explain(self) -> str:
        return "\n".join(self.trace)


def probe_ollama(host: str | None = None, model: str | None = None) -> tuple[bool, str]:
    """Is Ollama actually able to serve this model right now?

    Both halves are checked because they fail independently and the second one
    fails silently: the daemon answers happily while the model it is being asked
    for was never pulled, and the first sign of trouble is then an empty
    extraction hours later.
    """
    s = settings()
    host = host or s.ollama_host
    model = model or s.ollama_model

    if not _port_open(host):
        alt = host.replace("host.docker.internal", "localhost")
        if alt == host or not _port_open(alt):
            return False, f"nothing listening on {host}"
        host = alt

    try:
        r = httpx.get(f"{host}/api/tags", timeout=3.0)
        names = {m.get("name", "") for m in r.json().get("models", [])}
    except Exception as e:  # noqa: BLE001 — a probe must never raise
        return False, f"{host} did not answer /api/tags ({type(e).__name__})"

    if model in names or any(n.split(":")[0] == model.split(":")[0] for n in names):
        return True, f"{model} available at {host}"
    return False, f"{model} is not pulled (have: {', '.join(sorted(names)) or 'nothing'})"


def _groq_eta(est: TokenEstimate) -> float:
    """Wall-clock on Groq, which is the maximum of three independent throttles."""
    by_tokens = est.total_tokens / FREE_TIER["tokens_per_minute"] * 60
    by_requests = est.requests / FREE_TIER["requests_per_minute"] * 60
    by_latency = est.requests * GROQ_SECONDS_PER_REQUEST
    return max(by_tokens, by_requests, by_latency)


def _ollama_eta(est: TokenEstimate) -> float:
    return est.candidates * OLLAMA_SECONDS_PER_CANDIDATE


def route_document(
    spots,
    *,
    ledger: TokenLedger | None = None,
    page_budget: int | None = None,
    force: Tier | None = None,
) -> RoutingDecision:
    """Choose the tier for one document. Costs nothing and calls no model."""
    s = settings()
    ledger = ledger or TokenLedger()

    observed = ledger.observed_chars_per_token("groq")
    est = estimate_document(spots, chars_per_token=observed, page_budget=page_budget)

    trace = [f"estimate: {est.describe()}"]
    assessments: list[TierAssessment] = []

    if force is not None:
        trace.append(f"tier forced to {force.value} by the caller — no assessment performed")
        return RoutingDecision(tier=force, estimate=est, trace=trace)

    # ── tier 1 · Groq ────────────────────────────────────────────────────────
    remaining = ledger.remaining("groq")
    groq_eta = _groq_eta(est)
    needed = est.with_margin(s.router_safety_margin)
    spendable = max(0, remaining.tokens - GROQ_ADJUDICATION_RESERVE)

    if not s.groq_api_key:
        groq = TierAssessment(Tier.GROQ, False, False, groq_eta, "no GROQ_API_KEY configured")
    elif needed > spendable:
        groq = TierAssessment(
            Tier.GROQ, True, False, groq_eta,
            f"needs ~{needed:,} tokens (estimate × {s.router_safety_margin} margin) but only "
            f"{spendable:,} are spendable today — {remaining.tokens:,} left on the daily cap "
            f"less {GROQ_ADJUDICATION_RESERVE:,} reserved for adjudication",
        )
    elif est.requests > remaining.requests:
        groq = TierAssessment(
            Tier.GROQ, True, False, groq_eta,
            f"needs {est.requests:,} requests, {remaining.requests:,} left today "
            f"(per the {remaining.requests_source})",
        )
    else:
        groq = TierAssessment(
            Tier.GROQ, True, True, groq_eta,
            f"affordable: ~{needed:,} of {spendable:,} spendable tokens, "
            f"{est.requests:,} of {remaining.requests:,} requests",
        )
    assessments.append(groq)
    trace.append(f"groq: {'eligible' if groq.eligible else 'not eligible'} — {groq.reason}")
    if groq.available:
        trace.append(f"groq eta ≈ {_fmt(groq_eta)} (8K tokens/min is the binding throttle)")

    # ── tier 2 · Ollama ──────────────────────────────────────────────────────
    ok, why = probe_ollama()
    ollama_eta = _ollama_eta(est)
    ollama = TierAssessment(Tier.OLLAMA, ok, ok, ollama_eta if ok else None, why)
    assessments.append(ollama)
    trace.append(f"ollama: {'eligible' if ok else 'not eligible'} — {why}")
    if ok:
        trace.append(f"ollama eta ≈ {_fmt(ollama_eta)} at 0.65 candidates/sec, measured")

    # ── the choice ───────────────────────────────────────────────────────────
    if groq.eligible and ollama.eligible:
        if groq_eta > ollama_eta * SPEED_TOLERANCE:
            trace.append(
                f"→ ollama: groq is affordable but {groq_eta / max(ollama_eta, 1):.1f}x slower "
                f"here — at {est.total_tokens / max(est.candidates, 1):.0f} tokens per candidate "
                f"the 8,000/minute throttle costs more than the local GPU's generation does. "
                f"The 4060 is not rate limited."
            )
            chosen = Tier.OLLAMA
        else:
            trace.append(
                f"→ groq: affordable, and faster here "
                f"({_fmt(groq_eta)} against {_fmt(ollama_eta)} locally). The larger model is "
                f"also the better extractor, so there is no trade to make."
            )
            chosen = Tier.GROQ
    elif groq.eligible:
        trace.append("→ groq: the only model tier available")
        chosen = Tier.GROQ
    elif ollama.eligible:
        trace.append("→ ollama: the only model tier available")
        chosen = Tier.OLLAMA
    else:
        trace.append(
            "→ deterministic: no model tier is reachable. This is a fallback, never a "
            "preference — the UI says so prominently and the evals score it separately."
        )
        chosen = Tier.DETERMINISTIC

    assessments.append(TierAssessment(Tier.DETERMINISTIC, True, True, 0.0, "always available"))

    log.info(
        "route.decided",
        tier=chosen.value,
        candidates=est.candidates,
        tokens=est.total_tokens,
        requests=est.requests,
    )
    return RoutingDecision(tier=chosen, estimate=est, trace=trace, assessments=assessments)


def _fmt(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def plan_pages(spots, *, tier: Tier, seconds_budget: float | None = None) -> list[int]:
    """Page order, densest first, optionally trimmed to a wall-clock budget.

    Never a hard page cap: the order alone means a reader watching a 500-page
    filing sees the financial statements within seconds and the signature pages
    last, and a soft budget with a visible "keep going" control is a better
    answer than refusing the document.
    """
    order = spots.pages_by_density()
    if seconds_budget is None or tier is Tier.DETERMINISTIC:
        return order

    per_candidate = OLLAMA_SECONDS_PER_CANDIDATE if tier is Tier.OLLAMA else 0.3
    spent, kept = 0.0, []
    by_number = {p.page: p for p in spots.pages}
    for number in order:
        cost = len(by_number[number].candidates) * per_candidate
        if spent + cost > seconds_budget and kept:
            break
        kept.append(number)
        spent += cost
    return kept


__all__ = [
    "RoutingDecision",
    "TierAssessment",
    "DETERMINISTIC_BANNER",
    "route_document",
    "probe_ollama",
    "plan_pages",
]
