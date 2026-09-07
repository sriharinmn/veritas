"""How much a document will cost to extract, worked out before spending anything.

The router needs a number it can put next to a 200,000-token daily cap, and it
needs it *before* the first call, because discovering the answer by running out
halfway is exactly the failure this module exists to prevent.

Two decisions make the estimate trustworthy rather than a guess:

**It builds the real prompts.** The estimator imports the same system prompt,
the same batch renderer and the same batch size the extractor uses, and measures
those. A separate model of the prompt would drift away from the prompt the
moment somebody edited one and not the other, and would drift silently.

**It samples rather than materialising everything.** A 500-page filing has tens
of thousands of candidates; rendering every batch to count its characters would
cost more than the routing decision is worth. Batches are sampled evenly across
the document and extrapolated, which is accurate because batches are extremely
uniform in size — the renderer emits a fixed-width window per candidate.

The one genuinely uncertain quantity is characters-per-token. It starts from a
deliberately pessimistic prior and is replaced by the observed ratio from the
ledger as soon as real responses have reported real usage.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.extract.llm import BATCH_SIZE, DOC_CONTEXT_SYSTEM, EXTRACT_SYSTEM, _render_batch
from core.extract.spot import DocumentSpots

# Measured, not assumed. 4.0 chars/token is the usual rule of thumb for English
# and this pipeline was originally built on 3.4 as a "conservative" figure. A
# calibration run against a real Groq invoice put the actual ratio at **2.30**:
# financial pages tokenise far worse than prose, because digits, currency
# symbols, thousands separators and table gutters all fragment where English
# words do not. The supposedly cautious prior was optimistic by 45%, and the
# first estimate it produced came in 29% under the bill.
#
# 2.3 is now the prior, and the ledger replaces it with the ratio observed on
# this key as soon as there is enough evidence to be worth trusting. Lower is
# safer here: a low ratio over-estimates the bill, which can only route work
# away from a rationed provider and never into an overrun.
#
# Reproduce with:  python -m scripts.route_check <pdf> --spend 2
CHARS_PER_TOKEN_PRIOR = 2.3

# Output tokens per candidate, measured on the lean single-letter schema with
# non-measurements omitted: 49 locally on qwen3:8b, 58 on Groq's gpt-oss-120b —
# the difference is the reasoning tokens that `reasoning_effort: "low"` still
# spends, and Groq bills those against the same throttle as visible output.
#
# The higher figure is used because the estimate exists to decide whether Groq
# can afford a document, and under-charging the tier being budgeted for is the
# error that matters. The extractor caps generation at 70; budgeting for the cap
# would refuse documents that comfortably fit.
OUTPUT_TOKENS_PER_CANDIDATE = 58

# The document-context read: one call over the first pages, small output.
DOC_CONTEXT_OUTPUT_TOKENS = 120

MAX_SAMPLED_BATCHES = 12


@dataclass(frozen=True)
class TokenEstimate:
    candidates: int
    pages: int
    requests: int
    prompt_tokens: int
    completion_tokens: int
    chars_per_token: float
    calibrated: bool  # False while running on the prior

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def with_margin(self, margin: float) -> int:
        return int(self.total_tokens * margin)

    def describe(self) -> str:
        basis = "calibrated" if self.calibrated else "prior"
        return (
            f"{self.candidates:,} candidates over {self.pages} pages → "
            f"{self.requests:,} requests, ~{self.total_tokens:,} tokens "
            f"({self.prompt_tokens:,} prompt + {self.completion_tokens:,} completion, "
            f"{self.chars_per_token:.1f} chars/token, {basis})"
        )


def estimate_document(
    spots: DocumentSpots,
    *,
    chars_per_token: float | None = None,
    batch_size: int = BATCH_SIZE,
    page_budget: int | None = None,
) -> TokenEstimate:
    """Estimate the cost of extracting a document that has already been spotted.

    The spot sweep is regex-only and runs in seconds even on a 500-page filing,
    so this can be called on the real document before committing to a provider.
    """
    ratio = chars_per_token or CHARS_PER_TOKEN_PRIOR
    order = spots.pages_by_density()
    if page_budget is not None:
        order = order[:page_budget]
    pages = set(order)

    batches = _batches_for(spots, pages, batch_size)
    total_batches = len(batches)
    if total_batches == 0:
        return TokenEstimate(
            candidates=0,
            pages=len(pages),
            requests=1,
            prompt_tokens=int(len(DOC_CONTEXT_SYSTEM) / ratio),
            completion_tokens=DOC_CONTEXT_OUTPUT_TOKENS,
            chars_per_token=ratio,
            calibrated=chars_per_token is not None,
        )

    sampled = _sample(batches, MAX_SAMPLED_BATCHES)
    system_chars = len(EXTRACT_SYSTEM)
    # ~200 characters of document-context preamble precede every batch.
    preamble_chars = 200
    mean_batch_chars = sum(len(_render_batch(b)) for b in sampled) / len(sampled)
    prompt_chars = total_batches * (system_chars + preamble_chars + mean_batch_chars)

    candidates = sum(len(b) for b in batches)

    return TokenEstimate(
        candidates=candidates,
        pages=len(pages),
        requests=total_batches + 1,  # + the document-context read
        prompt_tokens=int(prompt_chars / ratio) + int(len(DOC_CONTEXT_SYSTEM) / ratio),
        completion_tokens=candidates * OUTPUT_TOKENS_PER_CANDIDATE + DOC_CONTEXT_OUTPUT_TOKENS,
        chars_per_token=ratio,
        calibrated=chars_per_token is not None,
    )


def _batches_for(spots: DocumentSpots, pages: set[int], batch_size: int) -> list[list]:
    """Reproduce the extractor's batching exactly, including what it discards.

    Dates and durations are filtered out before batching in `extract_page`; an
    estimate that counted them would over-charge macroeconomic documents badly,
    since those are dense with years.
    """
    out: list[list] = []
    for page in spots.pages:
        if page.page not in pages:
            continue
        usable = [c for c in page.candidates if c.kind not in ("date", "duration")]
        for start in range(0, len(usable), batch_size):
            out.append(usable[start : start + batch_size])
    return out


def _sample(items: list, k: int) -> list:
    """Evenly spaced, not random — the estimate must be identical run to run.

    Determinism matters here for the same reason it matters everywhere else in
    this pipeline: a routing decision that changed between two runs over the
    same document would make every downstream difference unattributable.
    """
    if len(items) <= k:
        return items
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]
