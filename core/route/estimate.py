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

# Financial pages tokenise worse than prose: digits, currency symbols and
# thousands separators fragment where English words do not. 4.0 is the usual
# rule of thumb for English; 3.4 is the conservative figure used here, and
# conservative means over-estimating the bill, which can only route work away
# from a rationed provider and never into an overrun.
CHARS_PER_TOKEN_PRIOR = 3.4

# Measured on this project, lean single-letter schema with non-measurements
# omitted: 49 output tokens per candidate. The extractor caps generation at 70
# per candidate; the estimate uses the measured mean, not the cap, because
# budgeting for the cap would refuse documents that would comfortably fit.
OUTPUT_TOKENS_PER_CANDIDATE = 49

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
