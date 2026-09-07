# Veritas — A Fact Knowledge Layer for Financial Documents

**Superjoin VIT 2026 · Engineering Intern Assignment — Implementation Plan**
Status: DRAFT FOR REVIEW · rev 3 · Date: 2026-09-07 · Submission deadline: 2026-09-09 morning

**rev 2 changes:** provider selection became a budget-aware per-document router (§2.1) backed by our own token ledger (§2.2), because Groq's headers do not expose the limit that actually binds; extraction became spot-first (§3.7), which buys a label-free recall denominator; schemas are authored to Groq strict-mode rules from day one (§4). Scope assumption: **build everything, freeze on the evening of the 8th, ship what is green** (§10).

**rev 3 changes:** hardware measured and written into §2; new §16 covers the gaps the engineering plan did not — assume the reviewer never runs the code, a hostile-input checklist, and `make verify` to prove the snapshot is reproducible rather than cherry-picked; new §17 is the host resource budget and safety rules.

---

## 0. The one-paragraph thesis

Most submissions to this prompt will be: *PDF → chunk → "LLM, extract facts" → embed → cosine similarity → "LLM, do these contradict?" → Neo4j → force-directed graph*. That pipeline has four holes, and every one of them is visible to a reviewer in under thirty seconds: `₹7,225 crore` and `INR 72.25 bn` are the same number but different vectors; pairwise LLM adjudication is O(n²), unauditable, and unfalsifiable; nothing stops the model citing a page that does not contain the value it just reported; and there is no way to know whether any of it is right.

**Veritas inverts the default.** A fact is not a sentence — it is a *typed tuple with an explicit scope*. Once claims are normalised into `(subject, predicate, value, scope, evidence)`, the three cases the assignment asks for stop being opinions and become **derivable properties of the data model**:

| Same subject & predicate, and… | Verdict |
|---|---|
| values agree after unit normalisation, scopes compatible | **CORROBORATION** |
| values disagree, scopes identical | **CONTRADICTION** |
| values disagree, scopes differ on exactly one axis | **RECONCILED**, and *that axis is the explanation* |

The LLM is then confined to the four jobs it is actually good at: reading a page into a tuple, judging whether two names refer to one thing, adjudicating the genuinely ambiguous residue, and writing the prose explanation. Everything else is deterministic, testable, cheap, and fast.

The assignment says outright: *"A graph database or visualization alone is not the solution. The interesting part is how facts are discovered, grounded, compared, and explained."* This plan is a direct answer to that sentence.

---

## 1. Reading the brief

### 1.1 What is actually required

- Extract meaningful numerical or semantic facts from PDFs.
- Link every fact to evidence in its source document.
- Identify corroboration, contradiction, and context-reconcilable apparent contradiction.
- Provide an API **or** UI to upload PDFs and inspect results.
- Must generalise: no hard-coded facts, filenames, schemas, or document-specific rules.
- Demonstrate four cases, three of them with source evidence + system reasoning.
- Submit: GitHub repo + ≤3 min video. README with Setup, Video, Approach, Limitations, Additional Notes.
- **No credentials in the repo.** If a paid service is needed, ship enough output for them to evaluate without our account.

### 1.2 What is being tested (read between the lines)

The JD says the job is *"turning the standards our finance team defines into evals, guardrails, and systems the product actually runs on"* and *"making a system reliable enough that a banker will stake their name on its output."*

So the graders are not scoring a demo. They are scoring **whether the candidate thinks like someone who has to ship AI that a regulated professional signs their name under.** That means the highest-value, most under-supplied things in this submission are:

1. A **grounding guarantee** — a mechanical firewall against hallucinated values.
2. An **eval harness** that measures the system's own accuracy and prints a number.
3. **Explicit trade-off documentation** (ADRs) rather than a wall of features.
4. **Honesty about failure** — case 4 sourced from real eval data, not anecdote.

Item 4 is a gift. Most candidates will invent a plausible-sounding failure. We will have a *quarantine queue* full of real ones, in the product, with counts.

### 1.3 The starter corpus

Two independent, deliberately-chosen datasets. Both are traps for naïve pipelines and both reward normalisation:

**`delhivery/`** — 3 documents, single entity, different disclosure formats and vintages:
- `01-delhivery-prospectus-2022-excerpt.pdf` (100pp — DRHP: summary financials, business, corporate history, management)
- `02-delhivery-annual-report-fy24-excerpt.pdf` (100pp — MD&A, governance, BRSR, consolidated financials + notes)
- `03-delhivery-q4-fy24-earnings-presentation.pdf` (27pp — investor deck, charts, KPI tiles)

Why this is a good trap: FY22 prospectus vs FY24 annual report vs Q4FY24 deck means **the same metric appears at three different vintages, two different scales (₹ million vs ₹ crore), two different bases (consolidated vs standalone), and with restatements.** Directors appear appointed in one and resigned in another. Registered addresses are written three different ways. The assignment's own inspiration examples are sitting in this folder.

**`india-macroeconomy/`** — 3 documents, one subject, three publishers:
- `01-india-economic-survey-2024-25-excerpt.pdf` (89pp — Government of India)
- `02-rbi-annual-report-2024-25-excerpt.pdf` (100pp — RBI)
- `03-imf-india-2025-article-iv-excerpt.pdf` (95pp — IMF)

Why this is a *better* trap: three institutions publishing overlapping macro facts with **different fiscal conventions (Indian FY Apr–Mar vs IMF calendar-year), different data vintages, different definitions of the same headline (GDP growth: real vs nominal, GVA vs GDP, market prices vs factor cost), and genuine forecast disagreement.** An IMF projection of 6.5% against an Economic Survey range of 6.3–6.8% is not a contradiction — it is *range containment* plus a *modality difference* (staff projection vs government projection). A system that flags that as a contradiction is naive. A system that explains it is not.

**Consequence for the plan:** the macro dataset is where the *strong* case-3 demos live; the Delhivery dataset is where the *clean* case-1 and case-2 demos live. Budget curation time in both.

---

## 2. Hard constraints and what they force

| Constraint | Consequence |
|---|---|
| **Zero budget for APIs.** | No paid inference, no managed vector DB, no paid hosting. |
| **Groq free tier = 30 RPM / 1K RPD / 8K TPM / 200K TPD** (verified 2026-09-07) | Two separate walls. **8K TPM** throttles even small documents; **200K TPD** is roughly one 100-page PDF, once. Groq cannot do bulk extraction over a 500-page corpus. It is excellent for *small* documents and for adjudication. |
| **Groq exposes remaining daily *requests* and remaining per-minute *tokens* — but not remaining daily tokens** (verified 2026-09-07) | The binding constraint (TPD) is invisible in the response headers. We must keep our own token ledger in Postgres. See §2.2. |
| **Groq `strict: true` is genuine constrained decoding** on `gpt-oss-120b` / `gpt-oss-20b` — 100% schema adherence, never invalid JSON | Groq is not merely "a bigger model," it is also schema-safe. Cost: every field must be `required`, every object needs `additionalProperties: false`, and optionality must be expressed as a nullable union. This shapes the Pydantic models in §3.2 — they are authored under strict-mode rules and relaxed for other providers, never the reverse. |
| **Local hardware, measured 2026-09-07:** Ryzen 7 7840HS (8C/16T) · RTX 4060 Laptop, **8188 MiB VRAM**, driver 592.82 / CUDA 13.1 · **15.3 GB system RAM** · 362 GB free · Docker 28.5.1, Python 3.12.8, Node 22.14. Ollama **not installed**. | This is the real compute budget. Bulk extraction runs locally on Ollama: free, unlimited, no rate limits, runs overnight. |
| **The binding local constraint is 15.3 GB of system RAM — not the 8 GB of VRAM.** | Docker Desktop (WSL2) + Postgres + Next.js dev server + Ollama + a browser will thrash swap on 16 GB long before the GPU is stressed. Enforced by the resource budget in §17: capped WSL2 memory, one loaded model, and bulk jobs never run concurrently with the dev servers. |
| **Ollama runs natively on Windows, not in a container.** | GPU passthrough into Docker on Windows requires WSL2 CUDA plumbing that is fragile and would become a setup instruction the graders have to follow. Native Ollama on the host, with the containerised worker reaching it at `host.docker.internal:11434`, removes GPU-in-Docker from the project entirely — for us *and* for them. |
| **Reviewer may have no API key at all.** | The app must produce a complete, inspectable knowledge layer with **zero credentials**. Non-negotiable. |
| **Reviewer will `git clone` and run.** | `docker compose up` must be the entire setup. No GPU required on their side. |
| **~48h wall clock, agent working continuously.** | Phase the build so *every* phase boundary is a submittable state. Never be mid-refactor at the deadline. |

### 2.1 The decision this forces: a budget-aware provider router

Provider choice is **not** a static env var. It is a decision made per document, at upload, from an estimated token cost and a live budget ledger.

```
   upload → estimate_tokens(doc)  ──▶  ROUTER  ──▶ commit to ONE provider for the whole doc
                                          │
        ┌─────────────────────────────────┼──────────────────────────────────┐
        ▼                                 ▼                                  ▼
 ┌──────────────────┐          ┌──────────────────────┐        ┌───────────────────────┐
 │ TIER 1 · GROQ    │          │ TIER 2 · OLLAMA      │        │ TIER 3 · DETERMINISTIC│
 │ gpt-oss-120b     │          │ qwen3:8b · local GPU │        │ rules + ONNX embeds   │
 │ strict schema    │          │ qwen2.5vl:7b vision  │        │ zero network          │
 ├──────────────────┤          ├──────────────────────┤        ├───────────────────────┤
 │ WHEN: est ≤ ~50K │          │ WHEN: doc too large  │        │ WHEN: no provider     │
 │ tok AND ledger   │          │ for Groq's budget,   │        │ reachable at all      │
 │ has headroom     │          │ OR no Groq key,      │        │                       │
 │                  │          │ OR bulk corpus run   │        │ loud amber banner     │
 │ best quality     │          │ free · unlimited     │        │ lower recall, honest  │
 │ fastest (small)  │          │ fastest (large)      │        │ still fully grounded  │
 └──────────────────┘          └──────────────────────┘        └───────────────────────┘
```

**The crossover is the interesting part.** Groq's 8K TPM throttle means throughput on a large document is *worse* than a local 4060, which has no throttle at all:

| Document size | Groq (throttled) | Ollama on RTX 4060 | Router picks |
|---|---|---|---|
| ~10 pages | ~60s | ~3 min | **Groq** |
| ~30 pages | ~8 min (TPM-bound) | ~6 min | **Ollama** |
| ~100 pages | exceeds entire daily budget | ~20 min, unthrottled | **Ollama** |

Measuring that crossover and publishing the chart is a far better answer to the brief's *"large PDFs without significant performance issues"* brownie point than adding a queue and calling it done.

**Routing is per document, decided before extraction starts — never per request.** If one document were extracted half by Groq and half by Ollama, `provenance.extractor` would vary within a single document and the evals in §7 would go blind: a failure could not be attributed to the model rather than the router. Mid-job failover exists only as a hard-error safety net (repeated 429 after honouring `retry-after`); if it fires, the document is flagged `mixed_extractor` and the UI says so.

Embeddings **never** touch an API in any tier: `fastembed` + `BAAI/bge-small-en-v1.5` ONNX, CPU, in-container, 384-dim. This removes the embedding provider from the credential story permanently and is worth stating in the README as a deliberate choice.

### 2.2 The token ledger

Groq's response headers give `x-ratelimit-remaining-requests` (daily) and `x-ratelimit-remaining-tokens` (**per-minute window only**). There is no header for remaining tokens *today* — so the one limit that actually decides routing is not observable from the API.

Therefore we keep our own:

```sql
llm_calls(id, provider, model, prompt_hash, prompt_tokens, completion_tokens,
          created_at, document_id, pipeline_run_id, http_status)
```

- `spent_today(provider) = Σ tokens WHERE created_at >= start_of_utc_day`
- Header values are recorded on every call and used to *correct* the ledger when they disagree (headers are ground truth for the windows they do cover).
- The router applies a **safety margin** — it will not start a document unless `estimated × 1.4 ≤ remaining_daily_budget`, because estimation is approximate and a job that dies at 80% is worse than one that never started on that tier.
- Estimation is `tiktoken`-approximate over the candidate windows (§3.7) plus a measured per-call prompt overhead, not a guess.

The ledger is also what makes the whole cost story legible in the README: total tokens consumed to build the shipped snapshot, per provider, with a rupee figure of ₹0.

Same table doubles as the `llm_cache` key source, so this costs almost nothing extra to build.

### 2.3 The decision this forces: ship a pre-computed snapshot

**This is the single highest-leverage UX decision in the submission.**

`docker compose up` boots the app **already populated** with the complete knowledge layer for all six starter PDFs — every claim, every evidence span, every reconciliation edge, every eval score — restored from a committed `seed/snapshot.sql.gz` generated locally on the 4060 and committed to the repo (it is derived data, not credentials).

The reviewer opens `localhost:3000` and is *immediately* looking at grounded facts with PDF highlighting and explained contradictions. No key. No wait. No spend. A banner states plainly what they are looking at and how to go live.

Their brief says: *"include enough sample output and video footage for us to evaluate it without needing your account."* We go one better — a fully interactive system, not sample output. Then, if they add a Groq key or point at an Ollama host, upload works live and they can throw their own PDFs at it, which they said they would do.

---

## 3. Architecture

### 3.1 System diagram

```
┌────────────┐   upload    ┌──────────────────────────────────────────────────┐
│  Next.js   │────────────▶│  FastAPI  /documents /claims /edges /evals /sse  │
│  + PDF.js  │◀────SSE─────│                                                  │
└────────────┘   progress  └───────────────────────┬──────────────────────────┘
                                                   │ enqueue
                                                   ▼
   ┌───────────────────────────────────────────────────────────────────────────┐
   │                          INGEST PIPELINE (worker)                         │
   │                                                                           │
   │  1 PARSE      PyMuPDF → blocks{text, bbox, page, char_span} + tables      │
   │               low-text page? → render 200dpi → VLM → text + approx bbox   │
   │                                                                           │
   │  2 DOC CTX    one pass/doc: entity, doc type, pub date, fiscal calendar,  │
   │               reporting currency + scale ("₹ in millions"), basis default │
   │               ── inherited by every claim in the doc ──                   │
   │                                                                           │
   │  3 SPOT       regex sweep → every numeral, date, %, currency token, with  │
   │               its window. FREE and exhaustive - the recall denominator.   │
   │                                                                           │
   │  4 EXTRACT    (a) numeric pass - LLM types + scopes each spotted window   │
   │               (b) semantic pass - LLM reads blocks for non-numeric facts  │
   │               → RawClaim[] (subject, predicate, value, scope, quote)      │
   │                                                                           │
   │  5 GROUND     ★ deterministic verifier: is the value literally in the     │
   │               cited span? does the span exist at that offset? bbox on the │
   │               right page? ── FAIL → QUARANTINE, never enters the graph    │
   │                                                                           │
   │  6 NORMALISE  units/scale/currency → canonical magnitude; fiscal periods  │
   │               → (start, end, convention); modality; basis; segment        │
   │                                                                           │
   │  7 CANONICAL  entity resolution + predicate resolution against an         │
   │               EVOLVING ONTOLOGY (embed → ANN → merge/create/LLM-decide)   │
   │                                                                           │
   │  8 PAIRING    blocking keys: (predicate_cluster, subject) + vector ANN +  │
   │               numeric-value index. Never O(n²).                           │
   │                                                                           │
   │  9 COMPARE    deterministic comparator first → CORROBORATE / CONTRADICT / │
   │               RECONCILED(axis) / UNRELATED / AMBIGUOUS                    │
   │               only AMBIGUOUS escalates to the LLM adjudicator             │
   │                                                                           │
   │ 10 EXPLAIN    LLM writes the human sentence, constrained to cite the      │
   │               deterministic reasoning trace it was given                  │
   └───────────────────────────────┬───────────────────────────────────────────┘
                                   ▼
   ┌───────────────────────────────────────────────────────────────────────────┐
   │  PostgreSQL 16 + pgvector                                                 │
   │  documents · pages · blocks · claims · quarantine · entities · aliases    │
   │  predicates · ontology_decisions · claim_edges · llm_cache · eval_runs    │
   └───────────────────────────────────────────────────────────────────────────┘
```

### 3.2 The core data model

```python
# The whole design is in this one structure. Everything else serves it.

class Claim:
    id:          UUID
    subject:     EntityRef        # → canonical entity node
    predicate:   PredicateRef     # → node in the evolving ontology
    value:       TypedValue
    scope:       Scope
    evidence:    list[Evidence]   # >= 1, always
    confidence:  float
    provenance:  Provenance       # extractor model, prompt version, run id

class TypedValue:                 # tagged union
    kind: Literal["money","quantity","ratio","date","text","entity","bool"]
    # money:    amount·Decimal, currency·ISO4217, scale·(unit|lakh|crore|mn|bn)
    # quantity: magnitude·Decimal, unit·str      → normalised via pint
    # ratio:    magnitude·Decimal, basis·(pct|bps|x)
    # canonical_magnitude: Decimal   ← ALWAYS populated; this is what we compare

class Scope:                      # ★ THE MOST IMPORTANT OBJECT IN THE SYSTEM ★
    period:      TemporalScope    # instant(as_of) | interval(start, end)
                                  # fiscal(label="FY24", convention="IN_APR_MAR")
    basis:       consolidated | standalone | segment | None
    segment:     str | None       # "Express Parcel", "Supply Chain Services"
    geography:   str | None
    accounting:  IND_AS | IFRS | US_GAAP | None
    modality:    reported | restated | estimated | projected | guidance | forecast
    vintage:     date             # publication date of the asserting document

class Evidence:
    document_id: UUID
    page:        int              # 1-indexed physical page in the PDF
    char_span:   tuple[int, int]  # offsets into the page's extracted text
    bbox:        list[Rect]       # normalised 0..1, may span lines → PDF.js overlay
    quote:       str              # verbatim, byte-for-byte from the page
    block_id:    UUID
```

**Why `Scope` is the whole ballgame:** a "contradiction" in financial documents is almost always a scope mismatch in disguise. Revenue differs → different period, or consolidated vs standalone, or restated. Growth differs → GVA vs GDP, real vs nominal. A director is active here and resigned there → different vintage. An address differs → same place, different formatting. **Every one of the assignment's own inspiration examples is a scope axis.** Model scope explicitly and the system explains itself for free.

### 3.3 The comparator (deterministic core)

```
compare(a, b):
    if a.subject   != b.subject:        → UNRELATED
    if a.predicate != b.predicate:      → UNRELATED  (unless alias-linked)

    diffs = scope_axes_that_differ(a.scope, b.scope)   # period | basis | segment |
                                                       # geography | accounting | modality
    agree = values_agree(a, b, tol)     # unit-normalised, relative tolerance,
                                        # rounding-aware (7,225 cr vs 72,251 mn),
                                        # interval containment for ranges

    if diffs == {}   and     agree:     → CORROBORATION           (independent restatement)
    if diffs == {}   and not agree:     → CONTRADICTION           (hard conflict)
    if len(diffs)==1 and not agree:     → RECONCILED(axis=diffs[0])
    if len(diffs)==1 and     agree:     → CORROBORATION(cross-scope, weaker)
    if len(diffs)>=2:                   → AMBIGUOUS  ──▶ LLM adjudicator
```

Three properties worth defending in the README:
- **Explainable by construction** — the verdict carries the axis that produced it.
- **Cheap** — no model call for the overwhelming majority of pairs.
- **Testable** — pure function, hundreds of unit cases, no flakiness.

The LLM adjudicator only sees the residue, and it receives the deterministic trace as context; its output schema forces it to pick from the same verdict enum and either name an axis or state explicitly that no scope axis explains the difference.

### 3.4 The grounding guarantee (the guardrail)

Every claim must cite a verbatim quote at a char span. Before insert:

1. `page_text[span[0]:span[1]] == quote` (exact, or equal after whitespace normalisation).
2. The claim's raw numeral string appears inside `quote`.
3. `bbox` resolves on the stated page and overlaps the span's rendered rects.
4. Scale/currency, if not literally in the quote, must be inherited from a recorded doc-context or table-header rule — and *that inheritance is itself recorded as a second Evidence entry.*

Fail any check → the claim is written to `quarantine` with a reason code, **never** to `claims`. The UI surfaces the quarantine queue with counts by reason.

This is roughly 150 lines of code and it buys: a hallucination firewall, a headline metric for the README (`grounding pass rate: 94.2%`), and a real, data-sourced answer to assignment case 4.

### 3.5 The evolving ontology (brownie point, done properly)

Predicates are **not** an enum — that would violate "no document-specific schemas."

```
new predicate string "Revenue from contracts with customers"
   → embed (bge-small, local)
   → ANN over existing predicate nodes
   → cos >= 0.92          → MERGE into existing node, record alias
   → cos <  0.72          → CREATE new node
   → 0.72 <= cos < 0.92   → LLM adjudicates, given both node definitions plus 3
                            example claims each → decision written to
                            `ontology_decisions` (auditable, replayable, diffable)
```

Identical machinery for entities and aliases (`Delhivery Ltd` ≡ `Delhivery Limited` ≡ `the Company`) and for the addresses example in the brief. Thresholds live in config, are tuned against the golden set, and their effect is an eval metric — not a vibe.

The UI has an **Ontology** view showing nodes, alias counts, and the LLM's merge decisions with reasoning. A reviewer clicking through that understands immediately that the schema is grown, not written.

### 3.6 Incremental ingest (brownie point)

- Content-hash per document and per page → re-uploading a known doc is a no-op.
- A new document extracts *only its own* claims, then compares them against **existing blocks only**. No global rebuild. Cost is O(new claims × block size), not O(n²).
- `llm_cache` keyed on `(provider, model, prompt_hash)` → every re-run of the pipeline is free and deterministic. This also makes "re-extract with a better model and diff the results" a one-command operation, which is an eval superpower.
- Edges are versioned by `pipeline_run_id`, so adding document #7 can *change* an existing verdict (new evidence resolves an old contradiction) and the change is visible rather than silent.

### 3.7 Spot-first extraction, and why it matters more than the tokens

The obvious extraction loop sends every block of prose to the LLM and asks "what facts are here?". Spot-first inverts it:

```
  page text
    └─▶ regex sweep (free, deterministic, exhaustive)
          finds: 1,847 value candidates — numerals, dates, percentages,
                 currency tokens, each with a ±N-char window and its bbox
    └─▶ NUMERIC PASS   LLM is handed a candidate + window and asked only to
                       TYPE and SCOPE it. Never "find facts" — "describe this one."
    └─▶ SEMANTIC PASS  separate, block-level, for facts with no numeral:
                       directorships, addresses, business descriptions, status changes
```

**The token saving is the boring benefit** (roughly 2–3×, because pages with no candidates are skipped entirely and prose is sent as narrow windows rather than whole blocks). It is what makes Tier 1 viable for documents two to three times larger than it otherwise would be.

**The real benefit is that it produces a denominator.** Recall is normally unmeasurable without labels: you cannot count the facts a model failed to notice. But a regex sweep *is* exhaustive over numerals by construction, so:

```
1,847 value candidates spotted
  → 1,203 became grounded claims        (65.1% conversion)
  →   312 correctly rejected as non-facts (page numbers, note refs, dates in prose)
  →   198 quarantined by the grounding verifier, by reason code
  →   134 dropped silently  ← THESE ARE THE BUGS
```

That last bucket is measurable recall loss, per document, **without a single hand-written label** — and it is a permanent, self-refreshing source of case-4 material. With a golden set of only 60–80 items (§7), this denominator is worth more than the labels are.

**The honest cost, stated plainly:** spot-first biases the system toward numeric facts, and the brief asks for *"numerical **or semantic** facts."* That is exactly why the semantic pass is a separate, block-level pass with its own budget rather than something folded into the numeric one. Two passes, two budgets, two eval tracks. If the semantic pass is weak, the eval split in §7 will say so out loud instead of hiding it inside an aggregate F1.

Second cost: a numeral's meaning often lives outside its window — a column header three rows up, a scale note in a caption. The window alone is not enough context. Mitigation is already in the design: the doc-context pass (§3.1 step 2) and the retained table header path (§4) are injected alongside every candidate window, so the LLM sees `header_path + doc_context + window`, not a bare number.

---

## 4. Tech stack, with the reason for each choice

| Layer | Choice | Why this and not the obvious alternative |
|---|---|---|
| **PDF parsing** | **PyMuPDF (fitz)** — text blocks with bbox + char offsets, `find_tables()`, per-page text-density check | Docling/Unstructured give better layout fidelity but add ~2GB of torch to the image and minutes of CPU inference per document. Reviewer experience (`docker compose up` → usable in seconds) outweighs the marginal table gain. Docling stays behind an optional flag; the trade-off is an ADR. |
| **Scanned-page fallback** | render 200dpi → **qwen2.5vl:7b** via Ollama (local), or the reviewer's vision model | Only triggers on low-text pages, so it costs nothing on the starter corpus — but it means the system does not silently return zero facts on a scanned filing. |
| **Table handling** | PyMuPDF table finder → markdown serialisation + **per-cell bbox retained** | Lets a number extracted from a table cell be highlighted *exactly*, not "somewhere on page 47." This is what makes the evidence pane feel real. |
| **Bulk LLM** | **Ollama · qwen3:8b** on the RTX 4060 | Free, unlimited, no rate limit, native JSON-schema constrained decoding, fits 8GB at Q4. The only way to process 500+ pages at zero cost. |
| **Adjudication LLM** | **Groq · gpt-oss-120b / llama-3.3-70b** (free tier) | ~200 calls per corpus fits inside 200K TPD comfortably. Sub-second latency. This is also the path a reviewer's own key takes. |
| **Provider abstraction** | thin `LLMGateway`: `ollama` \| `groq` \| `gemini` \| `openai` \| `anthropic` \| `none` | Swappable with one env var. Also the mechanism for degraded mode. Deliberately not over-engineered: one interface, ~120 lines. |
| **Provider router** | per-document tier selection from `estimate_tokens(doc)` + the token ledger (§2.1, §2.2) | The alternative — a static `LLM_PROVIDER` env var — makes the reviewer's 8-page upload take 3 minutes locally when Groq would do it in 60s, and makes a 100-page upload fail outright against Groq's daily cap. The router is ~250 lines and it is the thing that makes "upload any PDF" actually true. Explicitly a **post-P5 addition**: the single-provider path is the always-working default underneath it. |
| **Schema authoring** | Pydantic models written to Groq **strict-mode** rules: all fields `required`, `additionalProperties: false`, optionality as nullable unions | Strict is the most restrictive target. Authoring for it and relaxing for other providers works; authoring loosely and tightening later means rewriting every model mid-sprint. One-way door, taken deliberately on day one. |
| **Embeddings** | **fastembed · bge-small-en-v1.5** (ONNX, CPU, in-container) | Zero credentials, zero network, 384-dim, ~10ms per document on CPU. Removes embeddings from the API-key story entirely. |
| **Database** | **PostgreSQL 16 + pgvector** | One container instead of three. ACID upserts matter for incremental ingest. JSONB + GIN for the flexible scope/value payloads, generated columns + B-tree for normalised magnitude and period bounds, pgvector for ANN. The "graph" is three tables and a recursive CTE. |
| **Not Neo4j** | deliberate omission | The brief warns a graph DB is not the solution. Reaching for one signals optimising for the demo artefact rather than the reasoning. Stated as an ADR, not left implicit. |
| **API** | **FastAPI** + Pydantic v2 | Pydantic *is* the schema layer — the same models validate LLM structured output, DB payloads, and the OpenAPI contract. One definition, three uses. |
| **Jobs** | Postgres-backed queue + worker process | Avoids a Redis container for a workload of ~10 jobs. SSE streams progress to the UI. |
| **Frontend** | **Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui + react-pdf (PDF.js)** | PDF.js is the only realistic way to render the source page with a bbox overlay. This is the demo's centrepiece. |
| **Testing** | pytest (+ recorded LLM fixtures), Vitest, one Playwright smoke test | Normalisation and comparator are pure functions → fast, deterministic, high-count unit tests. |
| **CI** | GitHub Actions: ruff, mypy/tsc, unit, mocked-LLM integration, **gitleaks** | Green badge in the README. Gitleaks directly answers "keep credentials out of the repository." |
| **Packaging** | Docker Compose (`db`, `api`, `worker`, `web`, optional `ollama` profile) + `Makefile` | `docker compose up` is the whole setup instruction. |
| **Public link (optional)** | **Hugging Face Spaces**, Docker SDK, free CPU tier, snapshot baked in, read-only | Genuinely free. Not required by the submission text (repo + video only), so it is a stretch goal, never a dependency. |

---

## 5. Answering the two open questions directly

### 5.1 Docker vs. a public link

**Docker is the primary deliverable; a public link is a bonus.** The submission instructions ask for a GitHub repo and a ≤3 min video — not a hosted URL. And they said they may test with *their own* PDFs, which a read-only hosted demo would serve worse than a local run anyway.

So `docker compose up` is the canonical path, and we remove every reason it could fail for them:
- Pre-computed snapshot → **works with no API key, instantly**.
- No GPU required on their machine.
- Pinned image digests; no model downloads at their build time.
- A single `make demo` that also opens the browser.

If Phase 8 has room, the same image goes onto a free HF Space in read-only mode and the URL goes in the README as "try it without cloning." Zero cost, zero risk, pure upside.

### 5.2 The no-API-key problem

The instinct to fall back to a keyless mode with a very visible warning is right — and worth pushing further, because there are *two different reviewers* to serve:

**Reviewer A: clones, runs, looks around, never gets a key.**
→ The snapshot. They see the full-quality knowledge layer over all six starter PDFs immediately: facts, highlighted evidence, contradictions, reconciliations, eval scores. A neutral info banner explains it is a pre-computed snapshot produced by the same pipeline, naming the model and run date. This is the *majority* case and it must be excellent.

**Reviewer B: clones, runs, and uploads their own PDF.**
→ Startup does capability detection and the UI states its mode unambiguously:

| Mode | Trigger | Banner |
|---|---|---|
| **FULL · TIER 1** | `GROQ_API_KEY` set **and** estimated cost fits the day's remaining ledger | green · "Full extraction — groq/gpt-oss-120b · est. 34K tokens · 166K left today" |
| **FULL · TIER 2** | Groq unavailable/over budget, `OLLAMA_HOST` reachable | green · "Full extraction — local qwen3:8b · this document is too large for the Groq free tier, so it is running locally. No throttle, slower per page." |
| **DEGRADED** | no provider reachable | **amber, persistent, pinned to top of viewport** · "DETERMINISTIC MODE — no LLM configured. Extraction uses rule-based patterns only; recall is materially lower and semantic facts are not extracted. Set `GROQ_API_KEY` (free, 60 seconds) or run `make ollama` for full extraction." plus a *"What's the difference?"* link showing the same page extracted both ways, side by side |
| **SNAPSHOT** | viewing seeded documents | neutral · "Pre-computed — generated 2026-09-08, qwen3:8b + gpt-oss-120b" |

Degraded mode is not a stub. It really runs: number + unit + period regex anchored to the *learned* predicate lexicon carried in the snapshot's ontology, local ONNX embeddings for candidate generation, and the full deterministic comparator. It produces a genuinely working, lower-recall knowledge layer with zero network access. The honest side-by-side of full vs degraded output is *itself* a strong artefact — it demonstrates exactly what the LLM is buying us, quantified.

`.env.example` ships with a comment pointing at `console.groq.com/keys` and a note that the free tier is ample for interactive use but not for bulk ingest — which is precisely why the snapshot exists.

---

## 6. The four required cases

Each becomes a permalinked view in the app (`/case/1` … `/case/4`) plus a README section. Candidate shapes are identified from the corpus below; **final selection happens after the first full pipeline run** — this plan commits to the *shape*, not to facts not yet verified against the documents.

**Case 1 — Corroborated across documents, expressed differently.**
Target shape: a Delhivery FY24 metric asserted in the annual report (₹ million, consolidated, audited, inside a financial statement table) and in the Q4FY24 earnings deck (₹ crore, KPI tile, rounded). Different units, different scales, different document genres, different rounding — same normalised value. The demo shows both PDF pages side by side with the numbers highlighted and the comparator's normalisation trace: `72,251 mn INR → 7,225.1 cr INR ≡ 7,225 cr INR (Δ 0.001% < tol)`.
Backup: an India macro headline (e.g. real GDP growth for a stated fiscal year) asserted by two of the three institutions under matching definitions.

**Case 2 — Genuine or likely contradiction.**
Target shape: same subject, same predicate, same period, same basis — different values. Strongest candidates: a figure the FY24 annual report **restates** relative to the 2022 prospectus without either being labelled as restated, or a director/KMP tenure fact where the prospectus lists someone active and the later document does not (the brief's own example). Rendered as CONTRADICTION with both evidence panes plus an explicit list of the scope axes that were checked and found *identical* — showing the reasoning that rules out reconciliation matters as much as the flag itself.

**Case 3 — Apparent contradiction explained by context.**
This is where the macro dataset earns its place. Target shape: IMF vs Economic Survey vs RBI on the same headline where the discrepancy is fully explained by one axis — fiscal convention (IMF calendar year vs Indian FY Apr–Mar), modality (staff projection vs government projection vs central bank estimate), definition (GVA vs GDP, real vs nominal), or vintage (a January-2025 estimate vs a May-2025 revision). Rendered as `RECONCILED · axis = period_convention`, with the normalised periods printed under both raw values.
Delhivery backup: consolidated vs standalone, or a full-year figure against a nine-month figure (`9MFY25`) — the brief's literal example.

**Case 4 — A real extraction or reasoning failure.**
Sourced from the quarantine queue and the eval confusion matrix, not invented. Expected candidates:
- **Scale inheritance across a page break** — a table header reads "(₹ in millions)" on page 47 and the table continues onto page 48 without repeating it. The doc-context pass helps but will not catch every case. *Handled:* claims whose scale is inherited across a page boundary carry a `scale_inferred` flag and reduced confidence, shown in the UI; ungroundable ones are quarantined.
- **Footnote-qualified figures** — "*excluding one-time items" attached by superscript, spatially distant from the number. Likely to produce a false CONTRADICTION today. *Handled / next:* footnote-marker detection linking superscripts to note text so the qualifier lands in `Scope`.
- **Chart-only facts in the earnings deck** — a value existing solely as a bar label inside an image. *Handled:* the VLM path reads it, but with weaker bbox precision; flagged `evidence_precision: low`.

We will report the *measured rate* of each, not merely its existence. That is the difference between "I found a bug" and "I measured my system."

---

## 7. Evaluation harness

`make eval` produces a JSON report, writes a row to `eval_runs`, and prints a table. Six layers, cheapest first:

1. **Normalisation unit tests** (~150 pure cases, no LLM, under a second)
   `₹1,23,456.78 crore` · `Rs. 72,251 mn` · `USD 1.2bn` · `FY 2023-24` · `9MFY25` · `Q4FY24` · `H1CY25` · `+35 bps` · `1.4x` · `(2,345)` negative-in-parentheses · `NIL` · `—`. Indian digit grouping is a genuine trap and deserves its own block of tests.
2. **Grounding fidelity** (deterministic, 100% of claims, no LLM)
   `grounded / (grounded + quarantined)`, broken out by reason code. Headline README metric.
3. **Spot conversion — a label-free recall proxy** (deterministic, whole corpus, no LLM)
   Because the regex sweep in §3.7 is exhaustive over numerals by construction, it gives a denominator nothing else can: `spotted → claimed / rejected / quarantined / silently dropped`. The silently-dropped bucket is measurable recall loss on *every* document, including documents the graders upload themselves, with zero labelling. Tracked per document and as a corpus trend.
4. **Extraction quality against a hand-labelled golden set**
   60–80 claims labelled by hand across 3 pages per document (~2 hours of unglamorous, decisive work). Precision / recall / F1 on the tuple `(subject, predicate, canonical_value, period)`, with per-field partial credit reported separately so we can see *which field* fails most. Prior: `period` will be the weakest field, and saying so with a number is worth more than a perfect-looking demo.
5. **Relation classification** against ~30 hand-labelled claim pairs
   A 4×4 confusion matrix over `{corroborate, contradict, reconciled, unrelated}`. The cell that matters is *contradiction predicted where reconciliation was correct* — the false-alarm rate is what would destroy a banker's trust.
6. **Determinism / regression**
   Re-run with `llm_cache` primed → output must be byte-identical. Then diff against the previously committed report and print the deltas. Prevents "I improved the prompt and silently broke period extraction."

The **Evals tab** renders the latest report plus score history. A reviewer who sees a self-measuring system reads the rest of the repo differently.

---

## 8. UI design

Five surfaces. Dark, dense, financial-terminal register — not a pastel SaaS landing page.

1. **Documents** — drop zone; per-document ingest progress over SSE (parse → extract → ground → canonicalise → link); claim and quarantine counts; the detected doc context (entity, period, currency, scale, basis) rendered as chips, so the reviewer can *see* the system read the document's own conventions.
2. **Explorer** — virtualised claim table. Facets: entity, predicate, period, basis, modality, confidence, grounded/quarantined. Click a claim → the right pane renders that PDF page with the bbox highlighted and the quote outlined. **This is the "linked to evidence" requirement, made physical.**
3. **Reconciliation** *(the money screen)* — a claim cluster with two or more evidence panes side by side, each showing its own highlighted source page, plus:
   - verdict badge: `CORROBORATES` / `CONTRADICTS` / `RECONCILED · period` / …
   - normalised values printed under the raw ones
   - a **reasoning trace** rendered as steps: `subject ≡ (alias, 1.00)` → `predicate ≡ (ontology #42)` → `scope diff: {period}` → `values differ 8.3% > tol` → `verdict: RECONCILED(period)` → then the LLM's prose explanation, clearly labelled as the generated layer sitting on top of a deterministic decision.
   Showing the *machine's* steps rather than only an LLM paragraph is what "explained" means in the brief.
4. **Ontology** — predicate and entity nodes, alias counts, and the LLM merge decisions with their reasoning. Proves the schema is grown, not hard-coded.
5. **Evals** — score cards, history, confusion matrix, and the quarantine queue grouped by failure reason. Case 4 lives here, inside the product.

*(A force-graph view is a stretch item only. The brief warns against leading with it; it becomes a secondary tab if it costs under an hour.)*

---

## 9. Repository and engineering practice

They said *"use git meaningfully."* That is a scored line.

```
veritas/
├── README.md                  # Setup · Video · Approach · Limitations · Notes
├── plan.md                    # this document
├── docs/adr/                  # 0001..0008 architecture decision records
├── docker-compose.yml · Makefile · .env.example
├── api/            FastAPI app, routers, SSE, OpenAPI
├── core/           the actual library — importable, testable, no web deps
│   ├── parse/      pymupdf blocks, tables, bbox, vlm fallback
│   ├── normalize/  units, currency, scale, fiscal periods, indian numerals
│   ├── extract/    llm gateway, versioned prompts, structured schemas
│   ├── ground/     the verifier + quarantine
│   ├── canon/      entity + predicate resolution, evolving ontology
│   ├── link/       blocking, comparator, adjudicator, explainer
│   └── store/      sqlalchemy models, alembic migrations, queries
├── web/            Next.js 15 app
├── evals/          golden set (yaml), runners, reports/*.json
├── seed/           snapshot.sql.gz + the six starter PDFs
└── .github/workflows/ci.yml
```

- **Conventional commits**, small and frequent, one concern each. The history should read as a build log, not three 4,000-line dumps.
- **Feature branches → PRs → merge**, even solo. A reviewer skimming the PR list sees the thinking.
- **Ten ADRs**, ~200 words each, stating the decision, the alternatives, and what was given up. The cheapest possible way to answer "clear engineering decisions and trade-offs" — and almost nobody will do it:
  1. Postgres + pgvector over a graph database
  2. Deterministic comparator with LLM escalation, over pure-LLM adjudication
  3. Scope-first fact model over free-text facts
  4. PyMuPDF over Docling / Unstructured
  5. Local Ollama for bulk, Groq for adjudication (the free-tier reality)
  6. Committed snapshot + degraded mode for zero-credential evaluation
  7. Grounding verifier as a hard gate, not a confidence score
  8. Evolving-ontology thresholds, and how they were tuned
  9. Spot-first extraction: buying a recall denominator at the cost of a numeric bias
  10. Per-document provider routing, and why not per-request
- **`.env.example` only.** Gitleaks in CI. The snapshot contains derived data, no keys.
- **README** structured to their four required headings exactly, with eval numbers in the Approach section and a genuinely unflinching Limitations section.

---

## 10. Build phases

Sequenced so that **every phase boundary is a submittable state.** If everything after P5 fell over, the submission would still satisfy the brief.

| # | Phase | Est. | Exit criterion (submittable state) |
|---|---|---|---|
| **P0** | Scaffold: compose, Postgres + pgvector, FastAPI hello, Next.js shell, CI green, ADR-0001 | 2h | `docker compose up` works end to end |
| **P1** | Parse + provenance + **normalisation library and its 150 tests**. No LLM. | 5h | Any PDF → blocks with bbox and char spans; every normalisation test green |
| **P2** | LLM gateway (ollama / groq / none, strict-mode schemas) · doc-context pass · **spot-first extraction (§3.7)** · numeric + semantic passes · **grounding verifier + quarantine** | 6h | One PDF → grounded claims in Postgres, quarantine populated, spot-conversion and grounding rates printed |
| **P3** | Entity + predicate canonicalisation, evolving ontology, alias tables, ANN | 4h | Six PDFs share one ontology; merge decisions auditable |
| **P4** | Blocking · deterministic comparator · LLM adjudicator · edges · explanations | 5h | Corroboration / contradiction / reconciliation edges exist, each with a trace |
| **P5** | API + jobs + SSE + **Next.js UI with PDF.js evidence highlighting** | 6h | Upload a fresh PDF in the browser, watch it ingest, click a fact, see it highlighted |
| **P5b** | **Provider router + token ledger** (§2.1, §2.2): cost estimation, per-document tier selection, 429 handling with `retry-after`, mocked-429 test, mode banners | 3h | Upload an 8-page PDF → routes to Groq, finishes in ~1 min. Upload a 100-page PDF → routes to local, says why. Kill the key → degraded mode, says why. |
| **P6** | Eval harness + hand-labelled golden set + Evals tab | 4h | `make eval` prints the table; scores committed |
| **P7** | Full corpus run on the 4060 · curate the four cases · build and commit the snapshot | 3h | `docker compose up` with no key shows all four cases |
| **P8** | README + ten ADRs + video + optional HF Space + polish | 3h | Submitted |

**≈41h of work against ~48h of wall clock.** The margin is thin and deliberately so: the working assumption is that *everything* here gets built, and the 8th-night review decides what actually ships rather than pre-emptively cutting scope now.

P2 and P5 are where reality bites (structured-output quality on an 8B local model; PDF.js coordinate-space conversion). Both have a stated fallback — a stricter retry-with-repair loop for the former, page-level rather than span-level highlighting for the latter. Groq strict mode removes this risk entirely on Tier 1, which is another reason the router earns its place.

### The 8th-night gate
On the evening of the 8th, stop building and freeze. Whatever is green at that moment is what gets demoed, and the README's Limitations section is rewritten from the *actual* state rather than the intended one. Nothing half-finished ships with a hopeful description attached — an honest "not built yet" reads far better to this particular set of graders than a feature that breaks on their PDF. The cut order in §11 is the pre-committed decision so that the 8th-night version of me does not have to make it tired.

### Overnight parallelism
The corpus extraction run (P7) is hours of GPU time. Kick it off the moment P4 lands and let it run on the 4060 while P5 and P6 are being built. This is why the "laptop stays on" detail actually matters to the schedule.

---

## 11. Risks, and the pre-committed response to each

| Risk | Likelihood | Response |
|---|---|---|
| **qwen3:8b produces malformed or shallow structured output** | high | Ollama native JSON-schema constrained decoding; a repair loop (validate → feed the error back → retry ≤2); per-block rather than per-page extraction so one bad block cannot poison a page. If quality is unacceptable, fall back to smaller blocks plus a two-step prompt (spot candidates, then type them), and escalate the worst blocks to Groq within budget. |
| **PDF.js bbox alignment is off** | medium | PyMuPDF and PDF.js share the PDF user-space coordinate system; normalise to 0..1 against `page.rect` at extraction time, multiply by the rendered viewport. Ship a visual test page early in P5 rather than discovering this at hour 40. Fallback: highlight the whole line or block. |
| **Table numbers lose their column header** | high | Table extraction retains the header row and cell coordinates, and the header path is injected into that block's extraction prompt as context. The header path also lands in `Scope.segment` where applicable. |
| **Scale or currency stated once, fifty pages away** | high | The doc-context pass exists precisely for this. Anything inherited across a page break is flagged `scale_inferred` with reduced confidence. Reported as a known limitation with a measured rate. |
| **Groq free tier exhausted mid-demo** | medium | The snapshot means the demo never depends on live inference. The router degrades to Ollama, then to deterministic mode, and the banner says *why* it degraded rather than silently getting worse. |
| **Router mis-estimates and dies at 80% of a document** | medium | This is the router's characteristic failure and the reason for the 1.4× safety margin in §2.2. On repeated 429 after honouring `retry-after`, the document is finished on the next tier and flagged `mixed_extractor` — visible in the UI and excluded from eval aggregates. Tested with a mocked 429 before it ever runs live. **The router is post-P5 precisely so that cutting it costs nothing.** |
| **Spot-first under-serves semantic facts** | high | Acknowledged in §3.7 as a designed-in bias, not an accident. Mitigated by a separate block-level semantic pass with its own budget, and made *visible* by reporting numeric and semantic F1 separately in §7 rather than hiding both inside one aggregate. If the semantic pass is weak, the eval table says so. |
| **Ontology over-merges (two real metrics collapse into one)** | medium | Conservative high threshold, LLM adjudication in the middle band, every decision recorded and reversible. Over-merge rate is an eval metric — and a wrong merge is itself a legitimate case-4 candidate. |
| **Time overrun** | medium | Phase boundaries are submittable. Pre-committed cut order, in this sequence: HF Space → graph view → **provider router (P5b)** → Ontology tab → macro dataset (ship Delhivery only) → VLM fallback. **Never cut:** the grounding verifier, spot-conversion metrics, the evals, the four cases. |

---

## 12. Video storyboard (≤3:00)

| Time | Content |
|---|---|
| 0:00–0:20 | The problem in one sentence, over the architecture diagram. State the thesis: facts as typed tuples with explicit scope; deterministic comparison, LLM only where judgement is required. |
| 0:20–0:45 | Drag in a PDF **they have not seen** (a public filing outside the starter set). Ingest progress streams; facts appear. Proves generalisation, which is an explicit grading criterion. |
| 0:45–1:15 | **Case 1** — corroboration. Two evidence panes, different units, normalisation trace. |
| 1:15–1:45 | **Case 2** — contradiction. Both panes, plus the scope axes checked and found identical. |
| 1:45–2:20 | **Case 3** — reconciled by context. The axis is named. This is the segment that wins the assignment. |
| 2:20–2:50 | **Case 4** — the Evals tab. Real numbers: grounding pass rate, F1, confusion matrix. Open the quarantine queue, show a real failure, say what I would fix. |
| 2:50–3:00 | One line on what I would build next. |

Recorded in one take against the seeded environment, from a written script. No dead air on loading — the snapshot means nothing is computed live except the single fresh upload.

---

## 13. What I will explicitly say does not work

Drafted now so the README's Limitations section is written from measurement rather than memory. Blanks filled with real numbers after P6:

- Multi-page tables where the scale header does not repeat: measured failure rate `__%`.
- Footnote-qualified figures: qualifiers are not yet parsed into `Scope`; produces false contradictions at rate `__%`.
- Facts existing only inside chart images: recovered via the VLM path, with degraded bbox precision.
- Narrative and causal facts ("growth was driven by X") are extracted at far lower precision than numeric facts — the system is tuned for the latter, and the F1 split will show it.
- Ontology thresholds are tuned on six documents; they will need re-tuning at a hundred.
- The comparator's tolerance is a single global constant; it should be per-predicate (a 0.5% delta means something very different for GDP growth than for headcount).
- Degraded mode extracts roughly `__%` of what full mode does — measured, shown side by side.
- Semantic (non-numeric) facts are extracted at materially lower F1 than numeric ones — a designed-in consequence of spot-first extraction (§3.7), reported as a separate number rather than averaged away.
- The router's cost estimate is `tiktoken`-approximate; it carries a 1.4× safety margin, which means it is deliberately conservative and will sometimes send a document to local inference that Groq could in fact have handled.
- Ten ADRs, one repository, six documents, and forty-eight hours. None of the thresholds in this system have been tuned on a corpus large enough to trust them at scale, and the plan says so rather than implying otherwise.

---

## 14. Open questions

1. **Name.** `Veritas` is a placeholder. Keep it, or something less Latin?
2. **Fresh PDF for the video.** Ingesting one document the graders have never seen would prove generalisation on camera. A recent Indian DRHP or an RBI bulletin would work — happy to pick one, unless you would rather keep the demo to the starter set.
3. **Ollama models.** The plan assumes `qwen3:8b` for text and `qwen2.5vl:7b` for the vision fallback. If models are already pulled locally, name them and they get benchmarked first rather than pulling ~9GB.
4. **HF Space.** Worth the ~1h in P8, or better spent on a larger golden set?
5. **Repo visibility.** Public from the first commit, or private until submission and then flipped?
6. **Upload size cap.** The brief sets no page limit, and the router (§2.1) makes any size *technically* survivable. But a 500-page DRHP on local inference is ~90 minutes. Do we (a) accept it and stream honest progress, (b) cap uploads at ~150 pages with a clear message, or (c) accept it but extract a page range by default with a "process all" override? Leaning (a) — a progress bar that tells the truth is more impressive than a limit — but it is a real product call.

---

## 15. Why this should land

Against their stated criteria:

| They said | This plan's answer |
|---|---|
| "A thoughtful and creative approach" | Scope as a first-class object; contradictions become derivable rather than opined. |
| "Useful facts grounded in the PDFs" | A hard grounding gate — nothing ungrounded ever enters the graph — and the pass rate is published. |
| "Sensible handling of ambiguity, context, uncertainty" | Six explicit scope axes, a `RECONCILED(axis)` verdict class, an `AMBIGUOUS` escalation path, confidence flags on inferred scale. |
| "Generalises beyond the starter documents" | Zero hard-coded facts, filenames, or schemas; the ontology is grown at runtime; demonstrated on camera with an unseen PDF. |
| "Clear engineering decisions and trade-offs" | Eight ADRs. Every stack choice above carries its rejected alternative. |
| "We do not expect perfect extraction" | We publish exactly how imperfect it is, per failure mode, with numbers — including a recall figure derived without labels. |
| Brownie point: "large PDFs without significant performance issues" | Not answered with a queue. Answered with a measured throughput crossover between a throttled frontier API and a local GPU, and a router that acts on it. |
| JD: "evals, guardrails, systems the product runs on" | The eval harness and the grounding verifier are not bolted on — they are load-bearing. |

The intended reaction is not "nice demo." It is **"this person has shipped something a professional had to trust before."**

---

## 16. Reviewer simulation — the gaps the rest of this plan does not cover

Everything above optimises the *system*. This section optimises the *evaluation of the system*, which is a different problem, and it is where good engineers routinely lose marks.

### 16.1 Assume the reviewer never runs the code

A seven-person pre-seed startup reading a stack of intern submissions will watch the video and skim the README. Running `docker compose up` is the best case, not the base case. Consequences:

- **The four required cases live in the README, inline, with cropped evidence screenshots** — not only inside the running app. Someone must be able to grade the submission from the README alone.
- **The README is written from P0 onward, not at P8.** A skeleton lands with the scaffold and each phase fills its section while the reasoning is fresh. A README composed at hour 45 by a tired person systematically undersells the work it describes.
- Every headline number (grounding pass rate, spot conversion, F1, tokens spent, ₹0) appears above the fold.

### 16.2 Hostile input checklist

The JD's actual test is *"reliable enough that a banker will stake their name on its output."* Reliability is mostly about what happens on inputs we did not imagine. Before submission, upload each of these and confirm a clear, non-crashing outcome:

| Input | Required behaviour |
|---|---|
| Password-protected PDF | Named error at upload: "encrypted, cannot read" — not a 500 |
| Scanned / image-only PDF | Routes to the VLM path, or says plainly that OCR is unavailable in this mode |
| Corrupt bytes, or a `.docx` renamed `.pdf` | Rejected at the parse boundary with a readable message |
| 1-page PDF | Works; does not divide by zero anywhere in the stats |
| 500-page PDF | Router sends it local, streams honest progress, does not time out the request |
| A PDF with no facts at all (e.g. a scanned photo) | Returns zero claims and says so — never invents one |
| Non-English or mixed-script document | Degrades visibly rather than emitting garbage claims |
| The same PDF uploaded twice | Content-hash no-op, states "already ingested" |
| Two PDFs uploaded concurrently | Both complete; the ledger and ontology stay consistent |

*Discovering that the demo crashes on the graders' own PDF is the single most likely way this submission fails.* An hour spent here is worth more than any additional feature.

### 16.3 Prove the snapshot is not cherry-picked

The pre-computed snapshot (§2.3) is the plan's strongest UX move and also its most obvious credibility risk: a sceptical reviewer should wonder whether the shipped results were hand-tuned.

**`make verify`** answers it: re-runs the full pipeline over one starter document from scratch and diffs the result against the committed snapshot, printing the delta. If the diff is empty, the snapshot is reproducible by construction and the reviewer can confirm it in one command. This is cheap to build — the `llm_cache` and `pipeline_run_id` machinery already exists — and it converts the snapshot from "trust me" into "check me."

### 16.4 Cross-platform reality

The graders are as likely to be on a Mac as on Windows. `docker compose up` must work on both, which the snapshot guarantees (no GPU, no models, no keys). The `ollama` profile is documented as *optional and host-native*, with a plain note that on a machine without an NVIDIA GPU it will run on CPU and be slow — and that they do not need it, because the snapshot is already there.

### 16.5 Video discipline

Three minutes for seven beats is tight, and a rushed demo reads as a rushed project. The storyboard in §12 gets **rehearsed against a stopwatch before recording**, with a written script. If it runs over, the fresh-PDF ingest compresses to 15 seconds — the four required cases are contractual, everything else is not.

---

## 17. Host safety and resource budget

An explicit answer to "do not wreck the laptop." The honest risk ranking is not the intuitive one.

**Rank 1 — memory pressure (real).** 15.3 GB of RAM shared between Docker Desktop's WSL2 VM, Postgres, a Next.js dev server, Ollama, and a browser is genuinely tight, and swap thrashing on Windows is what would actually make the machine unusable for an hour. Mitigations, all applied from P0:

- `.wslconfig` caps the WSL2 VM (`memory=6GB`, `processors=8`, `swap=8GB`) so Docker cannot balloon into the host.
- Ollama runs natively rather than in a container, so the model is not held in memory twice.
- `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_KEEP_ALIVE=5m` — one model resident, no concurrent inference.
- The bulk corpus run (P7) never runs concurrently with the dev servers. It is a separate, sequenced job.
- Postgres gets a modest `shared_buffers`; this workload is thousands of rows, not millions.

**Rank 2 — thermals (manageable).** Hours of sustained GPU load on a laptop will hold the 4060 in the 75–85 °C band. That is within NVIDIA's operating spec and the card throttles long before anything is at risk — laptop GPUs are designed for exactly this. Still, the batch runner polls `nvidia-smi` between documents and pauses 60 s if the GPU exceeds 85 °C, so the machine is never pinned at the thermal ceiling unattended. Fans will be audible; nothing is being damaged.

**Rank 3 — VRAM exhaustion (avoided by design).** 8188 MiB total. `qwen3:8b` at Q4_K_M is ≈5.2 GB plus KV cache, which leaves headroom at a 16K context. The text model and the vision model are **never loaded simultaneously** — Ollama swaps them, which costs a few seconds and buys certainty. If benchmarking shows pressure, the fallback is `qwen3:4b`, decided by measurement rather than optimism.

**Rank 4 — BSOD (effectively not a risk).** Blue screens come from kernel-mode driver faults, not from user-space inference. The realistic worst case under GPU load is a TDR — the display driver resets, the screen blinks, the job dies, Windows recovers. Annoying, not damaging, and made unlikely by staying inside the VRAM budget.

**Pre-committed operating rules for this build:**

- No GPU-passthrough-into-Docker experiments.
- No graphics driver updates, no overclocking or undervolting, no BIOS or registry changes, no power-plan edits without asking first.
- Every long-running job runs in the background, is individually killable, and logs to a file rather than only to a terminal.
- Disk: models plus images plus snapshot ≈ 15–20 GB against 362 GB free. Not a constraint.

---

*End of plan. Review comments welcome inline — P0 does not start until this is signed off.*
