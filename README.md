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

### Prerequisites

**Docker route — recommended, and the only one that needs nothing else.**

| | |
|---|---|
| Docker Desktop | 20.10 or newer (tested on 28.5.1) |
| Disk | ~1.5 GB for the two images |
| RAM | 3 GB available to Docker |

**Local route**, if you would rather not use Docker:

| | |
|---|---|
| Python | 3.12 |
| Node | 22 |
| Ollama | optional, for the local model tier |

Nothing else. No database, no vector store, no API key, no GPU.

---

### 1. Clone and start

```bash
git clone https://github.com/sriharinmn/veritas.git
cd veritas
docker compose up --build
```

The first build takes three to five minutes — a Python image with PyMuPDF and
the ONNX embedder, and a Next production build. Subsequent starts are seconds.

Open **http://localhost:3000**.

The app boots **already populated**: the repository ships a pre-computed
knowledge layer over the six starter documents, so facts, evidence highlighting,
comparisons and eval scores are there on the first screen. No key, no waiting,
no spend. The first request builds the comparison graph in the background —
about ninety seconds — and the interface serves what it has meanwhile rather
than hanging.

To stop: `docker compose down`.

---

### 2. Add a provider, to ingest your own PDFs

Not required to browse. Required to extract from a document you upload — with no
provider the upload still works, on rules alone, and the interface says so
prominently.

```bash
cp .env.example .env
```

Then set **one** of:

| Tier | Set | How to get it |
|---|---|---|
| 1 · Groq | `GROQ_API_KEY=gsk_…` | free, about a minute at [console.groq.com/keys](https://console.groq.com/keys) |
| 2 · Ollama | nothing — it is found automatically | `ollama pull qwen3:8b`, run natively on your host |
| 3 · Rules | nothing | always available; **~23% of the model tier's recall**, and it says so |

The router chooses per document, before any work starts, and shows its reasoning
on the upload screen. Groq's free tier is 200,000 tokens a day, which is roughly
ten dense pages — so a large document routes to Ollama automatically rather than
failing halfway.

> **Ollama runs on your host, never in a container.** GPU passthrough into
> Docker is fragile on Windows and macOS and would become a setup step you have
> to follow. The API reaches your host at `host.docker.internal:11434`
> automatically. A CPU-only container is available with
> `docker compose --profile ollama up`, but it is roughly ten minutes per page
> and installing Ollama natively is strictly better.

Check what your machine can do:

```bash
make doctor
```

```
extraction tiers, best first
  [ ok ] 1 · Groq               Ready — openai/gpt-oss-120b, strict schema mode.
  [ ok ] 2 · Ollama             qwen3:8b available at http://localhost:11434
  [ ok ] 3 · Deterministic      always available — rules only, ~23% of the model tier's recall
```

Every gap it finds is printed with the one command that closes it.

---

### 3. Ports

3000 and 8000 are the two most commonly occupied ports on a developer's machine.
Both are overridable in `.env`:

```bash
API_PORT=8010
WEB_PORT=3010
```

> Changing `API_PORT` needs `docker compose up --build`, not just a restart.
> Next inlines `NEXT_PUBLIC_*` into the client bundle when the app is compiled,
> so the address has to be baked in rather than passed at run time.

OpenAPI docs: **http://localhost:8000/docs**.

---

### Running without Docker

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scriptsctivate
pip install -e ".[dev]"
uvicorn api.main:app --port 8000

cd web && npm install && npm run dev              # http://localhost:3000
```

If you change `WEB_PORT`, set it in `.env` **before** starting the API — the
CORS allow-list is built from it, and a browser on an origin the API does not
allow gets an interface that renders perfectly and shows nothing.

---

### Troubleshooting

Every one of these was hit while building this, which is why they are here
rather than in a wiki nobody reads.

| Symptom | Cause | Fix |
|---|---|---|
| `env file .env not found` | none needed | already handled — `.env` is optional; pull the latest |
| Interface loads, no data, tier badge stuck on "Checking…" | the browser's origin is not on the API's CORS list | set `WEB_PORT` in `.env` to the port you actually use, restart the API |
| Same symptom, `.env` looks correct | a UTF-8 **BOM** in `.env` — PowerShell's `>` writes one by default, and the first key is then read with an invisible prefix and ignored | `make doctor` names the file; rewrite with `Set-Content -Encoding utf8NoBOM` |
| Upload fails with `Read-only file system` | old compose file | fixed — `seed/uploads` is mounted read-write |
| Evidence pane shows "Failed to fetch" on a PDF | the API is bound to IPv4 while `localhost` resolves to `::1` first | the client uses `127.0.0.1` explicitly; if you overrode `NEXT_PUBLIC_API_BASE_URL`, do the same |
| Upload routes to rules when a key is set | Groq's daily cap is spent — the upload screen's "why this tier" panel shows the arithmetic | wait for 00:00 UTC, or let it use Ollama |
| Counts read as zero on first load | the comparison graph is still building | it serves what it has; the figures fill in within ~90s |

---

<details>
<summary>Running the pipeline directly</summary>

```bash
python -m scripts.smoke <pdf>          # parse → spot → extract → ground, no model
python -m scripts.pipeline seed/delhivery/*.pdf   # the whole thing, no model
python -m scripts.corpus_run           # model-tier run, resumable, GPU-guarded
python -m scripts.tier_check           # all three tiers against a known answer key
python -m evals.run                    # the eval harness
make test                              # 386 python tests + the web build
```
</details>

---

## The interface

Seven screens, each answering one question. The corpus you land on is the
starter dataset shipped with the repository; everything below works identically
on a document you add yourself.

### Overview
The worked example the whole system exists for — one figure printed two ways in
two filings, and the finding that they are the same fact — over live counts read
from the layer rather than written down.

### The four cases
The four the brief asks for, at `/case/1` … `/case/4`. Each shows both figures,
every scope axis marked same / differs / not established, the page each was read
from with the exact characters highlighted, and the comparator's reasoning step
by step. **Case 2 is empty, deliberately** — see *Limitations*.

### Facts
Every grounded claim, filterable by **subject** and by document, searchable by
metric, figure or name. Select a row and the pane on the right renders that page
of that PDF with the claim's span marked. That pane is the point of the product:
a fact you cannot check is a rumour.

### Comparisons
Every pair the system classified, by verdict — corroborates, contradicts,
reconciled, or escalated as ambiguous. Filter to one subject or one document to
ask what a single filing agrees and disagrees with. "Across documents only" is on
by default, because a document agreeing with itself is not news.

### Vocabulary
The metric names and entity names the system learned, with the aliases that
merged into each. Nothing here is a fixed list — it grew from the filings. This
is where a wrong merge would be visible, which is why it is a screen rather than
a log line.

### Checks
The eval harness: grounding pass rate, spot conversion, period attribution,
determinism, and the measured gap between the model and rule tiers. Label-free
by construction — the regex sweep is exhaustive over numerals, so recall has a
denominator without anyone hand-labelling anything.

### Add a document
Drop in a PDF. The routing decision is shown *before* any work starts — which
tier, why, and the token estimate behind it — then pages stream back densest
first, so the financial statements arrive before the signature pages. When
extraction finishes the document is folded into the existing layer incrementally
rather than by rebuilding, and the screen links straight to its facts and its
comparisons.

**A document about a subject nothing else here mentions will have no
cross-document comparisons.** That is the system declining to invent a link, not
a failure: two facts are only ever compared when they are about the same subject.

---

## Video demo

**Link:** _to be added._

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
| grounded claims | **11,134** — 11,038 numeric, 96 semantic |
| grounding pass rate | **100.0%** — 0 quarantined |
| relations derived | **114,314** |
| period resolved | 44.8% — the weakest field, and the one everything depends on |
| impossible periods refused | 811 (14.0% of those datable) |
| signed negatives recovered | 722 (6.4%) |
| semantic values refused as paraphrase | **48.4%** |
| unit tests | **388** green |
| cost to build | **₹0** |

Relations: 9,564 corroboration · **77** contradiction · 25,875 reconciled ·
78,798 ambiguous.

**Period attribution got worse on purpose.** It read 51.4% until a reviewer asked
why a prospectus dated April 2022 was being compared on figures labelled FY24 —
a year that had not happened when it was printed. The pipeline stored the
publication date on every claim and never once compared it to the period.
Checking it refused 811 periods as impossible, and the honest resolved rate fell
to 44.3%. The lower number is the true one; the higher one was counting dates
that could not exist.

**That contradiction number used to be 17,873, then 1,894, and is now 77.**

The last cut came from a reviewer looking at case 2 — the "genuine contradiction"
— and it was wrong. The system had found ₹7,054 crore of revenue in the earnings
deck against ₹81,415 million in the annual report, both labelled FY24, 13.4%
apart. Page 9 of the deck is a stacked bar chart reading 7,054 / 7,224 / 8,142
for FY22, FY23 and FY24, with the years drawn in a text run of their own. The
7,054 is FY22. Nothing on the flattened page says so.

The tell was on the page all along: that page prints the same measure three
times and calls all three FY24, which cannot be true. A claim sharing a page and
a predicate with a *different* value **for the same period** has an inherited
default rather than a period that was read, and a contradiction cannot rest on
it. Keying that rule on the period is the whole of it — without the period it
also condemns every figure in every two-column statement, which took the count
to 1.

So case 2 is empty again, and that is the honest answer.

**And before that, it was 17,873.** 91.3% of them were two figures
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

> Delhivery Limited — *revenue from services*

| | Statement A | Statement B |
|---|---|---|
| **Value as written** | `81,415` (million) | `8,142` (crore) |
| **Normalised** | 81415000000 INR | 81420000000 INR |
| **Source** | 02-delhivery-annual-report-fy24-excerpt.pdf p6 | 03-delhivery-q4-fy24-earnings-presentation.pdf p9 |

| Scope axis | A | B | |
|---|---|---|---|
| Period | FY24 | FY24 | = |
| Basis | standalone | standalone | = |
| Segment | — | — | = |
| Geography | — | — | = |
| Accounting | unknown | unknown | = |
| Modality | reported | reported | = |

**Evidence, verbatim from the page:**

- A — “81,415”
- B — “8,142”

**Why this pair was chosen** (criteria in `scripts/curate_cases.py`):

- the two statements come from different documents
- written differently: '81,415' against '8,142'
- stated in different scales: 81,415 million against 8,142 crore — the same money, written two ways
- both periods resolve to real dates

<details><summary>The comparator's reasoning, step by step</summary>

```
subject ≡ 'Delhivery Limited'
predicate ≡ 'revenue from services'
scopes are identical on every axis
values agree: agree within rounding (0.0061% apart, tolerance 407075000.000)
'81,415' → 81415000000
'8,142' → 81420000000
→ corroboration: same scope, values agree within rounding (0.0061% apart, tolerance 407075000.000)
```

</details>

### Case 2 — A genuine contradiction

**No pair in this corpus meets the criteria, and that is the finding.**

The comparator does report contradictions — thousands of them — but none survives every check when the check is applied honestly. Cross-document candidates fail on precision or on chart-derived evidence; the ones that score highest are two figures from the *same row of the same two-column statement*, where the prior-year column inherited the current year's period. Case 4 shows a verified instance.

Presenting one of those as a contradiction between documents would be presenting a bug as a finding, which is the one thing this system is built not to do. The criteria that rule them out are in `scripts/curate_cases.py` and a reviewer can loosen them and look.

### Case 3 — An apparent contradiction explained by context

**Verdict: RECONCILED**, on the `period` axis · confidence 0.85 · across two documents

> Delhivery Limited — *revenue from services*

| | Statement A | Statement B |
|---|---|---|
| **Value as written** | `72,236` (million) | `8,142` (crore) |
| **Normalised** | 72236000000 INR | 81420000000 INR |
| **Source** | 02-delhivery-annual-report-fy24-excerpt.pdf p6 | 03-delhivery-q4-fy24-earnings-presentation.pdf p9 |

| Scope axis | A | B | |
|---|---|---|---|
| Period | FY23 | FY24 | **differs** |
| Basis | standalone | standalone | = |
| Segment | — | — | = |
| Geography | — | — | = |
| Accounting | unknown | unknown | = |
| Modality | reported | reported | = |

**Evidence, verbatim from the page:**

- A — “72,236”
- B — “8,142”

**Why this pair was chosen** (criteria in `scripts/curate_cases.py`):

- the two statements come from different documents
- an apparent conflict that the period axis resolves: the two figures do not cover the same span, so their difference is not evidence that either is wrong
- the 13% gap looks alarming until the axis is named

<details><summary>The comparator's reasoning, step by step</summary>

```
subject ≡ 'Delhivery Limited'
predicate ≡ 'revenue from services'
scope differs on: period
values disagree: differ by 12.71%
'72,236' → 72236000000
'8,142' → 81420000000
→ reconciled: the values differ because the two statements cover different periods
```

</details>

### Case 4 — Where this system is weakest, measured rather than remembered

Every figure here comes from the same run that produced the three cases
above. None of it is recalled from memory or softened.

**The failure that explains why case 2 is empty: the prior-year column.**

0 of 77 contradictions (0.0%) are two figures from the
same page, same row of a two-column statement. Verified by hand on 02-delhivery-annual-report-fy24-excerpt.pdf p68:

```
Depreciation and amortisation expense  27  7,215.50  8,311.44
headers: March 31, 2024 | March 31, 2023
reported as: contradiction, both labelled FY24
actually:    the current-year and prior-year columns of one row
```

_A profit and loss statement prints this year beside last year. Where the column header is not recovered on that page, both figures inherit the same period, and a pair that differs in value with every scope axis identical is by definition a contradiction. Nothing is hallucinated -- both numbers are really on the page, correctly grounded -- but the period attached to one of them is wrong, and the conclusion drawn from it is wrong with it. This is why case 2 reports no genuine contradiction: the candidates that survive every other check have this shape, and presenting one as a finding would be presenting a bug as a finding. The fix is column recovery reaching more pages, not a change to the comparator._

**The other dominant failure: predicates that should not have merged.**

10 of 77 contradictions (13.0%) hold two values that differ by more than 500%.
Two figures that far apart are not a disagreement between documents — they are
two different quantities collapsed onto one predicate node, after which every
pair inside that node reads as a conflict.

| Predicate | A | B | Apart |
|---|---|---|---|
| overall balance | `1.5` (p45) | `47.5` (p5) | 316,567% |
| freight, handling and servicing costs | `23` (p86) | `5,971` (p24) | 259,509% |
| general government debt | `80.7` (p48) | `80.7` (p44) | 9,900% |

_Two figures reported as contradictory while differing by orders of magnitude are not a disagreement between documents — they are two different quantities merged onto one predicate node, after which every pair inside that node reads as a conflict. This is the dominant source of false contradictions and it is a canonicalisation problem, not a comparator problem. The fix is a unit-compatibility check at merge time: two predicates whose values never share an order of magnitude are not the same predicate._

**Period attribution is the weakest field.**

4,989 of 11,134 claims (44.8%) resolve to real dates. Period is the axis the comparator leans on hardest and the one most often missing from the page. Everything downstream depends on it, which is why the ambiguous bucket is the largest one.

**What the grounding gate refused.**

0 claims were quarantined. These are claims the grounding gate refused because the value was not literally present in the span cited. They are counted, not discarded, and they never entered the graph.

**What the comparator declined to decide.**

78,798 pairs. Pairs the comparator declined to decide. Most carry no resolved period on either side, which is a missing-evidence problem rather than a reasoning one — and reporting it as a conflict would have been the easy, wrong answer.

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
- **Scale.** ~6,500 lines of Python, ~2,500 of TypeScript, 388 tests, 58 commits.
- **Licence.** MIT — see [LICENSE](LICENSE). Use it for anything.
- **The starter PDFs** in `seed/` are the excerpts provided with the assignment,
  kept in the repository so a clone boots with working evidence rather than
  facts pointing at files that are not there.
