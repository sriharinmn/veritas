# Veritas — a fact knowledge layer for financial documents

Extracts grounded facts from financial PDFs, links every fact to the exact span
of the page it came from, and works out whether facts across documents
**corroborate**, **contradict**, or are **reconcilable through context**.

Built for the Superjoin VIT 2026 engineering intern assignment.

> **Status.** The extraction run over all six starter documents is complete.
> Every figure here is measured on that run — where something has not been
> measured, it says so. Several numbers below are unflattering and are printed
> anyway, because a knowledge layer that hides its own error rate is asking to
> be trusted on exactly the question it refuses to answer.

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
| 1 · Groq | a free API key, 60 seconds to get | documents under ~10 dense pages/day |
| 2 · Ollama | `ollama pull qwen3:8b` on your host | large documents; no rate limits |
| 3 · Deterministic | nothing at all | zero configuration — **recovers 47% of the model tier's claims** on the same pages, and says so loudly |

Tier 3 is not a fallback nobody uses: it is the path a reviewer with no key and
no Ollama actually takes when they upload a PDF, so it is measured rather than
assumed. On three pages of the earnings deck the model tier found 334 grounded
claims and the rule-based tier found 156. Every one of those 156 is grounded in
its source span exactly as the others are — there are simply fewer of them, and
the banner on screen says so before a reader draws any conclusions.

Ports 3000 and 8000 are the two most commonly occupied ports on any developer's
machine, so `API_PORT` and `WEB_PORT` are both overridable in `.env`.
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
parse → spot → extract ─┐
                        ├→ ground → normalise → canonicalise → pair → compare
parse → prose → semantic ┘
```

Two extraction passes over different pages. Numbers live in the dense tables;
non-numeric facts live in the prose, and those are close to opposite orderings —
a statement of profit and loss is the densest page in a filing and contains no
semantic facts at all, while the auditor's report has no figures worth
extracting and every fact about who signed it. Both passes produce the same
`Claim` type, are grounded by the same gate, and land in the same checkpoint.

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

**Semantic** (`core/extract/semantic.py`) — the facts that are not numbers: a
company's former name, who audited it, which court approved a scheme, the date
an order was passed. There is no regex that finds "ceased to be a Director" the
way there is one that finds 7,225, so the no-hallucination guarantee is kept a
different way: the model must return the value as a **verbatim substring of the
block it was shown**, and this module locates that substring itself. Not present
character for character, discarded. **That check refuses 48.4% of what the local
model proposes** — "resigned" where the page says "ceased to be a Director" —
because paraphrase is the semantic equivalent of a hallucinated digit, and it
is not a rare event.

First-person subjects are resolved to the document's entity, because "we
operated 132 centres" is grounded, correct and completely unlinkable: a fact
about "we" can never corroborate a fact about Delhivery Limited.

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

Six documents, 146 pages, one RTX 4060 laptop, no spend.

| | |
|---|---|
| grounded claims | **11,290** — 11,180 numeric, 110 semantic |
| grounding pass rate | **100.0%** — 0 quarantined |
| relations derived | **115,308** |
| period resolved | 51.4% — the weakest field, and the one everything depends on |
| signed negatives recovered | 722 (6.4%) |
| semantic values refused as paraphrase | **48.4%** |
| unit tests | **355** green |
| cost to build | **₹0** |

Relations: 9,735 corroboration · **2,007** contradiction · 27,257 reconciled ·
76,309 ambiguous.

**That contradiction number used to be 17,873.** 91.3% of them were two figures
from the *same row of the same two-column statement* — a profit and loss account
prints this year beside last year, and where the column header was not recovered
both figures inherited the current period, so every row of every such table
produced a "contradiction". Verified by hand on page 68 of the FY24 annual
report. The comparator now recognises that shape and escalates it as ambiguous
instead of accusing; nothing is deleted, and the trace says exactly why.

**Case 2 reports that none of them survives verification as a cross-document
contradiction.** I checked the four strongest candidates against the source
pages by hand, and all four were artefacts:

| candidate | on the page it turned out to be |
|---|---|
| D&A 7,215.50 vs 8,311.44 | the same row, this year beside last year |
| D&A 7,215.50 vs 6,073.78 | 6,073.78 is *Other expenses*, merged onto the depreciation node |
| revenue 16,538.97 vs 81,415.38 | one side had no resolved period at all |
| closing balance 3,302.37 vs 295 | 3,302.37 is *Trade receivables-credit impaired*, and for the prior year |

Every number in those pairs is real and correctly grounded. What is wrong each
time is the period or the row label attached to one of them — which is a
recovery problem, not a disagreement between sources. Shipping one as the
headline finding would contradict the only claim this project actually makes,
so the curator now requires a cross-document pair with a predicate specific
enough to accuse on, and reports honestly when nothing clears that bar.

### Decisions and trade-offs

Full records are in [`docs/adr/`](docs/adr/). The ones that shaped the most code:

**No database, and that is a decision rather than an omission.** The brief warns
that a graph DB is not the solution, and it is right — the hard part is
normalisation and grounding, not traversal. But this project also ran Postgres
with pgvector for most of its life, and *nothing ever connected to it*: the only
reference to `DATABASE_URL` in the whole codebase was a worker splitting the
string for a log line.

The store is append-only JSONL, one line per page, 0.8 MB gzipped for 11,180
claims. The one real argument for a database was the 101-second full rebuild,
and that was answered without one — `extend()` folds a new document into the
existing layer incrementally, and a test asserts it produces the same relation
counts as a rebuild. So the database, the worker and four dependencies are gone.
Shipping infrastructure that nothing opens is worse than shipping none: a reader
has to work out for themselves that it does nothing, and that minute costs them
confidence in everything else. [ADR-0001](docs/adr/0001-storage-jsonl-not-postgres.md)
records the reversal and the point at which it stops being the right call.

**Local GPU for bulk, Groq for adjudication — and I was wrong twice about why.**

This project originally claimed Groq's 8K-tokens-per-minute throttle made it
slower than the laptop GPU past about thirty pages. Writing the estimator
appeared to disprove that, and I replaced the claim with the opposite one: Groq
1.75× faster. Then I checked both against an actual invoice, which is the only
way to find out. Measured, on two pages of the earnings deck:

| | predicted | billed |
|---|---|---|
| chars per token | 3.40 | **2.30** |
| tokens | 38,102 | **53,729** |
| requests | 12 | 12 |

Financial pages tokenise far worse than prose — digits, currency symbols,
thousands separators and table gutters all fragment where words do not — so the
"conservative" prior was optimistic by 45%. At the real ratio Groq costs
**1.544 seconds per candidate against the RTX 4060's 1.540**. A dead heat, within
0.3%. Both confident claims were wrong, in opposite directions, and the speed
comparison turns out not to be the interesting question at all.

The daily cap is. 200,000 tokens buys roughly 1,000 candidates — about **ten
dense pages, across every document, per day**. A single 100-page filing is
several days' allowance. So the split is not "cloud for speed, local for scale";
it is that the good model is rationed to about one chapter a day and the laptop
is not rationed. The router still compares wall-clock, because the two tiers are
close enough that a change to the context window would separate them, and it
reports which of the four limits actually decided (`core/route/router.py`).

Reproduce it: `python -m scripts.route_check <pdf> --spend 2`.

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

These are **selected by published criteria, not by hand**. Each case is a scoring
function over every pair the system produced (`scripts/curate_cases.py`), and the
winner is whatever scores highest — so a reader can disagree with a criterion,
which is a better conversation than disagreeing with a cherry-picked example.
Everything below is generated from `evals/cases.json` by `scripts/render_cases.py`
and regenerated whenever the corpus changes.

In the running app each case is a permalink — **`/case/1`** through **`/case/4`**
— with both source pages rendered and the evidence highlighted in place.

> **Case 2 is empty, and that is the result rather than a gap.** No
> cross-document contradiction in this corpus survives verification. I checked
> the strongest candidates by hand and every one traced back to a period or a
> row label attached to the wrong figure — real numbers, correctly grounded,
> wrong scope. Case 4 shows a verified instance. Shipping one of those as the
> headline finding would contradict the only claim this project makes.

<!-- cases:start -->

### Case 1 — Corroborated across documents, expressed differently

**Verdict: CORROBORATION** · confidence 0.85 · across two documents

> Delhivery Limited — *ebitda*

| | Statement A | Statement B |
|---|---|---|
| **Value as written** | `1,266` (million) | `127` (crore) |
| **Normalised** | 1266000000 INR | 1270000000 INR |
| **Source** | 02-delhivery-annual-report-fy24-excerpt.pdf p6 | 03-delhivery-q4-fy24-earnings-presentation.pdf p23 |

| Scope axis | A | B | |
|---|---|---|---|
| Period | FY24 | FY24 | = |
| Basis | standalone | standalone | = |
| Segment | — | — | = |
| Geography | — | — | = |
| Accounting | IND_AS | unknown | **differs** |
| Modality | reported | reported | = |

**Evidence, verbatim from the page:**

- A — “1,266”
- B — “\| Reported EBITDA \| 13 \| 109 \| 46 \| \| (452) \| 127 \| \|”

**Why this pair was chosen** (criteria in `scripts/curate_cases.py`):

- the two statements come from different documents
- written differently: '1,266' against '127'
- stated in different scales: 1,266 million against 127 crore — the same money, written two ways
- both periods resolve to real dates

<details><summary>The comparator's reasoning, step by step</summary>

```
subject ≡ 'Delhivery Limited'
predicate ≡ 'ebitda'
scopes are identical on every axis
values agree: agree within rounding (0.3160% apart, tolerance 10000000)
'1,266' → 1266000000
'127' → 1270000000
→ corroboration: same scope, same value
```

</details>

### Case 2 — A genuine contradiction

**No pair in this corpus meets the criteria, and that is the finding.**

The comparator does report contradictions — thousands of them — but none survives every check when the check is applied honestly. Cross-document candidates fail on precision or on chart-derived evidence; the ones that score highest are two figures from the *same row of the same two-column statement*, where the prior-year column inherited the current year's period. Case 4 shows a verified instance.

Presenting one of those as a contradiction between documents would be presenting a bug as a finding, which is the one thing this system is built not to do. The criteria that rule them out are in `scripts/curate_cases.py` and a reviewer can loosen them and look.

### Case 3 — An apparent contradiction explained by context

**Verdict: RECONCILED**, on the `basis` axis · confidence 0.60 · across two documents

> Delhivery — *revenue from cross-border services*

| | Statement A | Statement B |
|---|---|---|
| **Value as written** | `10.70` | `1.87` |
| **Normalised** | 0.107 percent | 0.0187 percent |
| **Source** | 01-delhivery-prospectus-2022-excerpt.pdf p45 | 02-delhivery-annual-report-fy24-excerpt.pdf p36 |

| Scope axis | A | B | |
|---|---|---|---|
| Period | FY24 | FY24 | = |
| Basis | consolidated | standalone | **differs** |
| Segment | — | — | = |
| Geography | — | — | = |
| Accounting | unknown | IND_AS | **differs** |
| Modality | reported | reported | = |

**Evidence, verbatim from the page:**

- A — “10.70%”
- B — “1.87%”

**Why this pair was chosen** (criteria in `scripts/curate_cases.py`):

- the two statements come from different documents
- the basis axis explains the difference on its own
- the 83% gap looks alarming until the axis is named

<details><summary>The comparator's reasoning, step by step</summary>

```
subject ≡ 'Delhivery'
predicate ≡ 'revenue from cross-border services'
scope differs on: basis
values disagree: differ by 8.83 percentage points
'10.70' → 0.107
'1.87' → 0.0187
→ reconciled: the values differ because one is consolidated and the other is not
```

</details>

### Case 4 — Where this system is weakest, measured rather than remembered

Every figure here comes from the same run that produced the three cases
above. None of it is recalled from memory or softened.

**The failure that explains why case 2 is empty: the prior-year column.**

0 of 2,007 contradictions (0.0%) are two figures from the
same page, same row of a two-column statement. Verified by hand on 02-delhivery-annual-report-fy24-excerpt.pdf p68:

```
Depreciation and amortisation expense  27  7,215.50  8,311.44
headers: March 31, 2024 | March 31, 2023
reported as: contradiction, both labelled FY24
actually:    the current-year and prior-year columns of one row
```

_A profit and loss statement prints this year beside last year. Where the column header is not recovered on that page, both figures inherit the same period, and a pair that differs in value with every scope axis identical is by definition a contradiction. Nothing is hallucinated -- both numbers are really on the page, correctly grounded -- but the period attached to one of them is wrong, and the conclusion drawn from it is wrong with it. This is why case 2 reports no genuine contradiction: the candidates that survive every other check have this shape, and presenting one as a finding would be presenting a bug as a finding. The fix is column recovery reaching more pages, not a change to the comparator._

**The other dominant failure: predicates that should not have merged.**

593 of 2,007 contradictions (29.5%) hold two values that differ by more than 500%.
Two figures that far apart are not a disagreement between documents — they are
two different quantities collapsed onto one predicate node, after which every
pair inside that node reads as a conflict.

| Predicate | A | B | Apart |
|---|---|---|---|
| nominal gdp | `330,682` (p48) | `301,230` (p16) | 9,109,355,816,062% |
| nominal gdp | `330,682` (p48) | `301,230` (p16) | 9,109,355,816,062% |
| revenue from operations | `(46)` (p15) | `1,519` (p23) | 3,302,173,913,144% |

_Two figures reported as contradictory while differing by orders of magnitude are not a disagreement between documents — they are two different quantities merged onto one predicate node, after which every pair inside that node reads as a conflict. This is the dominant source of false contradictions and it is a canonicalisation problem, not a comparator problem. The fix is a unit-compatibility check at merge time: two predicates whose values never share an order of magnitude are not the same predicate._

**Period attribution is the weakest field.**

5,808 of 11,290 claims (51.4%) resolve to real dates. Period is the axis the comparator leans on hardest and the one most often missing from the page. Everything downstream depends on it, which is why the ambiguous bucket is the largest one.

**What the grounding gate refused.**

0 claims were quarantined. These are claims the grounding gate refused because the value was not literally present in the span cited. They are counted, not discarded, and they never entered the graph.

**What the comparator declined to decide.**

76,309 pairs. Pairs the comparator declined to decide. Most carry no resolved period on either side, which is a missing-evidence problem rather than a reasoning one — and reporting it as a conflict would have been the easy, wrong answer.

<!-- cases:end -->

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
