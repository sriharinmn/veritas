# Veritas — a fact knowledge layer for financial documents

Extracts grounded facts from financial PDFs, links every fact to the exact span
of the page it came from, and works out whether facts across documents
**corroborate**, **contradict**, or are **reconcilable through context**.

Built for the Superjoin VIT 2026 engineering intern assignment.

> **Status.** The extraction run over the starter corpus is still in progress at
> the time of writing, so the figures below are the live ones and will be higher
> in the shipped snapshot. Everything quoted here is measured, not estimated —
> where something has not been measured yet, it says so.

---

## The idea, in one paragraph

The obvious build is: chunk the PDF → ask an LLM to extract facts → embed →
cosine similarity → ask an LLM whether two facts contradict → draw a graph. That
pipeline has four holes visible in thirty seconds. `₹7,225 crore` and
`INR 72.25 bn` are the same number and different vectors. Pairwise LLM
adjudication is O(n²), unauditable and unfalsifiable. Nothing stops the model
citing a page that does not contain the value it just reported. And there is no
way to know whether any of it is right.

**Veritas treats a fact as a typed tuple with an explicit scope**, not a
sentence:

```
Claim(subject, predicate, value, scope, evidence)
scope = (period, basis, segment, geography, accounting, modality, vintage)
```

Once claims are normalised into that shape, the three relationships the
assignment asks for stop being an LLM's opinion and become derivable:

| Same subject and predicate, and… | Verdict |
|---|---|
| values agree after unit normalisation | **corroboration** |
| values disagree, every scope axis identical | **contradiction** |
| values disagree, exactly one axis differs | **reconciled** — and that axis *is* the explanation |
| values disagree, two or more axes differ | **ambiguous** — escalate to a model |

The model is confined to what it is genuinely good at: reading a page into a
tuple, judging whether two names refer to one thing, adjudicating the ambiguous
residue, and writing prose over a decision it did not make.

---

## Setup and run instructions

```bash
git clone <repo-url>
cd veritas
docker compose up
```

Open **http://localhost:3000**. That is the whole setup.

**No API key is required.** The repository ships a pre-computed knowledge layer
over the starter documents, so the app boots already populated — facts, evidence
highlighting, relations and eval scores are there immediately. No key, no GPU,
no waiting, no spend.

To ingest **your own** PDFs, add a provider to `.env` (copy `.env.example`):

| Tier | Needs | Best for |
|---|---|---|
| 1 · Groq | a free API key, 60 seconds to get | documents under ~17 dense pages/day |
| 2 · Ollama | `ollama pull qwen3:8b` on your host | large documents; no rate limits |
| 3 · Deterministic | nothing at all | works with zero configuration, lower recall, and says so loudly |

Ports 3000 and 8000 are the two most commonly occupied ports on any developer's
machine, so `API_PORT`, `WEB_PORT` and `DB_PORT` are all overridable in `.env`.
API docs are at `/docs`.

<details>
<summary>Running the pipeline directly</summary>

```bash
python -m scripts.smoke <pdf>          # parse → spot → extract → ground, no model
python -m scripts.pipeline seed/delhivery/*.pdf   # the whole thing, no model
python -m scripts.corpus_run           # model-tier run, resumable, GPU-guarded
python -m evals.run                    # the eval harness
```
</details>

---

## Video demo

_Pending._

---

## Approach

### Architecture

```
parse → spot → extract → ground → normalise → canonicalise → pair → compare
```

**Parse** (`core/parse/`) — PyMuPDF, with one invariant asserted on every block:
`page.text[start:end] == block.text`. Page text is *built from* the blocks rather
than extracted separately, which is what stops citation offsets drifting off the
words they point at. Tables keep their header row and per-cell geometry, and a
recovery pass reattaches column headers to table bodies the detector flattened
into plain text (`core/parse/columns.py`) — which is where the period and the
reporting basis usually live.

**Spot** (`core/extract/spot.py`) — a regex sweep finds every numeral, date,
percentage and currency amount, free and exhaustive. This is the recall
denominator: because the sweep misses nothing, every number in a document is
accounted for exactly once.

**Extract** (`core/extract/llm.py`) — the model is handed a candidate that has
already been located, with the occurrence marked in place, and asked only what
it *means*. **It never writes a number, a quote, a page or an offset.** A model
that only labels candidates cannot hallucinate a figure — the number in a claim
is a substring of the page it cites by construction. That removes the failure
class rather than filtering it.

**Ground** (`core/ground/verify.py`) — a hard gate. If the value is not literally
inside the span it cites, the claim is quarantined and never enters the graph.
No low-confidence escape hatch, because a score is something a reader talks
themselves past.

**Normalise** (`core/normalize/`) — units, scales, currencies and fiscal periods
to canonical form. `FY24`, `year ended March 31, 2024` and `2023-24` are the same
twelve months; the IMF's `CY2024` is not.

**Canonicalise** (`core/canon/`) — an ontology grown at runtime. There is no
metric enum and no company list anywhere in this codebase.

**Pair and compare** (`core/link/`) — blocked candidate generation, never
all-pairs, then the deterministic comparator above.

### Measured, on the starter corpus

| | |
|---|---|
| grounded claims | **6,486** and rising |
| grounding pass rate | **100.0%** |
| spot conversion | **75.6%** of signal candidates became claims |
| unit tests | **233** green |
| cost to build | **₹0** |

Relations: 5,312 corroboration · 11,278 contradiction · 13,367 reconciled ·
30,882 ambiguous.

### Decisions and trade-offs

Full records are in [`docs/adr/`](docs/adr/). The ones that shaped the most code:

**Postgres + pgvector, not a graph database.** The brief warns that a graph DB is
not the solution, and it is right: the hard part here is normalisation and
grounding, not traversal. The graph is three tables and a recursive CTE.

**Local GPU for bulk, Groq for adjudication — and not for the reason I first
assumed.** I built the router expecting Groq's 8K-tokens-per-minute throttle to
make it slower than the laptop GPU on large documents. Writing the estimator
disproved that: at this pipeline's measured ratio of 68 prompt + 49 completion
tokens per candidate, Groq costs 0.88s per candidate and the RTX 4060 costs 1.54s.
**Groq is the faster extractor, by about 1.75×, and it is also the better model.**

It still cannot do the bulk work, because the binding limit is the daily one.
200,000 tokens a day buys roughly 1,700 candidates — about **seventeen dense
pages, across every document, per day.** A single 100-page filing exceeds a full
day's allowance several times over. So the split is not "cloud for speed, local
for scale"; it is that the good model is rationed to about one chapter a day and
the laptop is unrationed. The router still compares wall-clock, because the
speed crossover is real and sits at 205 tokens per candidate — widening the
context window would reach it — and it reports which of the four limits actually
decided (`core/route/router.py`).

**Ollama runs natively, never in a container.** GPU passthrough on Windows is
fragile and would become a setup step the graders have to follow.

**Throughput was measured before it was optimised.** Prefill runs at
1,900–4,700 tok/s and generation at ~40, so output tokens per candidate is the
only lever that matters. Single-letter schema fields and omitting
non-measurements took 72 tokens per candidate to 49; disabling qwen3's thinking
mode was worth 6×. Larger batches do not help.

**A contradiction requires positive evidence.** Corroboration and contradiction
do not carry the same burden. Saying two figures agree is mild; saying they
contradict is an accusation placed in front of somebody who will act on it. The
comparator was inferring "no axis differs" from two claims that both had *no
resolved period*, and reporting a conflict — 52% of contradictions were of that
kind. They now escalate as ambiguous instead. Contradictions fell 26,933 → 9,000
without suppressing anything.

### AI tools used

Built with Claude Code (Opus). Extraction and document-context reading run on
`qwen3:8b` locally via Ollama; Groq's `gpt-oss-120b` is wired for the
adjudication path. Embeddings are local ONNX (`bge-small-en-v1.5`) and never
require a key or the network.

---

## The four required cases

_Curated views pending the end of the corpus run — the macroeconomic documents
that carry case 3 are still being processed._ The **Reconciliation** screen
already shows live examples of each relation with both source pages, both
highlighted spans, the seven scope axes, and the reasoning trace.

---

## Limitations and next steps

Written from measurement, not from memory.

**The contradiction count is still too high at 11,278.** The cause is known
rather than mysterious: predicate labels are weak on pages that give the model
little context, so unrelated figures land on one ontology node and every pair
inside it reads as a conflict. The next fix is a labelled precision measurement
to size it, not a guess.

**Period attribution is the weakest field** — only 53.5% of claims resolve to
real dates. Everything downstream depends on it, and it is the single highest-value
thing left to improve.

**Chart-only facts extract poorly.** Slide decks store numbers as bar labels in
images with scrambled reading order; predicates from those pages are noticeably
worse. The vision path exists but is not yet wired into the corpus run.

**Not yet measured:** extraction precision and recall against a hand-labelled
golden set; the relation confusion matrix, in particular contradictions
predicted where reconciliation was correct; full mode against deterministic mode
on the same pages.

**Not yet built:** persistence (the knowledge layer is rebuilt from JSONL
checkpoints on demand), the LLM adjudicator for the ambiguous residue, the
budget-aware provider router, and incremental re-ingest.

**Only pairwise sums** are attempted in the arithmetic coherence check, so a
total made of three or more parts counts as unsatisfied. The figure is a floor,
not an accuracy score.

---

## Additional notes

- **No credentials in this repository.** `.env.example` only; `gitleaks` runs in
  CI. The committed snapshot is derived data.
- **Bugs worth reading about.** Several are documented in commit messages
  because the finding mattered more than the fix: a parser that glued adjacent
  table columns into numbers that never existed; unit markers leaking across 200
  characters to turn a share count into 9.3 trillion; a predicate literally named
  `million` that produced 8,895 fabricated contradictions; and an Ollama host
  that failed silently and nearly wasted an eight-hour run.
- **Scale.** ~6,000 lines of Python, ~2,000 of TypeScript, 233 tests,
  20+ commits.
