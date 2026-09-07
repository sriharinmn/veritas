# CLAUDE.md — Veritas

A fact knowledge layer for financial documents. Superjoin VIT 2026 engineering intern assignment.
**Read `plan.md` first** — it is the authoritative design document. This file is the operational companion.

---

## Build status

Update this table at every phase boundary. It is the first thing to read after `plan.md`.

| Phase | Scope | Status |
|---|---|---|
| P0 | Scaffold, compose, CI, CLAUDE.md, README skeleton, ADR-0001 | **in progress** |
| P1 | Parse + provenance + normalisation library (150 tests) | not started |
| P2 | LLM gateway, doc-context, spot-first extraction, grounding verifier | not started |
| P3 | Entity + predicate canonicalisation, evolving ontology | not started |
| P4 | Blocking, deterministic comparator, adjudicator, edges | not started |
| P5 | API + jobs + SSE + Next.js UI with PDF.js highlighting | not started |
| P5b | Provider router + token ledger | not started |
| P6 | Eval harness + golden set + Evals tab | not started |
| P7 | Full corpus run, curate four cases, build snapshot | not started |
| P8 | README, 10 ADRs, video, polish | not started |

---

## The one thing to understand

A fact is **not a sentence**. It is a typed tuple with an explicit scope:

```
Claim(subject, predicate, value, scope, evidence)
```

Once claims are normalised, the assignment's three required relations become *derivable*, not opinions:

- same subject + predicate, scopes identical, values agree → **CORROBORATION**
- same subject + predicate, scopes identical, values disagree → **CONTRADICTION**
- same subject + predicate, scopes differ on exactly one axis → **RECONCILED**, and that axis *is* the explanation

`Scope` has seven axes: `period, basis, segment, geography, accounting, modality, vintage`.
The LLM never decides a relation that the comparator can decide deterministically.

**If you are about to write code that asks an LLM whether two facts contradict, stop.** That is the comparator's job. The LLM adjudicates only the `AMBIGUOUS` residue (scopes differ on 2+ axes), and it receives the deterministic trace as context.

---

## Commands

```bash
make up          # docker compose up -d          (db, api, worker, web)
make down        # stop everything
make logs        # tail all services
make seed        # restore seed/snapshot.sql.gz into the db
make test        # pytest + vitest
make eval        # run the eval harness, write evals/reports/<ts>.json
make verify      # re-run pipeline on one doc, diff vs the committed snapshot
make ollama      # check ollama health + pull required models
make fmt         # ruff format + fix, prettier
```

App at `http://localhost:3000`, API at `http://localhost:8000`, OpenAPI at `/docs`.

---

## Host environment (measured 2026-09-07)

- Ryzen 7 7840HS (8C/16T) · **RTX 4060 Laptop, 8188 MiB VRAM** · driver 592.82 / CUDA 13.1
- **15.3 GB system RAM — this is the binding constraint, not VRAM**
- 362 GB free · Docker 28.5.1 · Python 3.12.8 · Node 22.14

### Non-negotiable host rules

- **Ollama runs natively on Windows, never in Docker.** The worker reaches it at `host.docker.internal:11434`. No GPU passthrough into containers — it is fragile on Windows and would become a setup step the graders have to follow.
- `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`. Text and vision models are never resident simultaneously.
- WSL2 is capped in `~/.wslconfig` (6 GB) so Docker cannot balloon into the host.
- The bulk corpus run never runs concurrently with the dev servers.
- Batch jobs poll `nvidia-smi` between documents and pause 60s above 85 °C.
- No driver updates, no overclocking, no BIOS/registry/power-plan changes.
- Long jobs run in the background, are individually killable, and log to a file.

---

## Provider tiers

Selected **per document, before extraction starts** — never per request (mixing extractors within one document blinds the evals).

| Tier | When | Model |
|---|---|---|
| 1 · Groq | key present AND `est_tokens × 1.4 ≤ remaining_daily` | `openai/gpt-oss-120b`, `strict: true` |
| 2 · Ollama | doc too large for Groq's budget, or no key, or bulk corpus run | `qwen3:8b` (text), `qwen2.5vl:7b` (vision) |
| 3 · Deterministic | nothing reachable | rules + local ONNX embeddings |

Groq free tier: **30 RPM / 1K RPD / 8K TPM / 200K TPD.** The 8K TPM throttle is what actually binds on medium documents; the 200K TPD cap is what rules out bulk corpus runs. Groq's headers expose remaining *daily requests* and remaining *per-minute tokens* — **there is no header for remaining daily tokens**, which is why `llm_calls` exists as our own ledger.

Embeddings never touch an API in any tier: `fastembed` + `BAAI/bge-small-en-v1.5`, ONNX, CPU, in-container, 384-dim.

---

## Schema rule (one-way door, decided P0)

All Pydantic models used for LLM structured output are authored to **Groq strict-mode rules**:

- every field `required`
- `additionalProperties: false` on every object
- optionality expressed as a nullable union, never an omitted field

Strict is the most restrictive target. Authoring for it and relaxing for other providers works; authoring loosely and tightening later means rewriting every model mid-sprint.

---

## Conventions

- **Grounding is a hard gate.** A claim whose value is not literally present in its cited span goes to `quarantine`, never to `claims`. No exceptions, no "low confidence" escape hatch.
- **Extraction is spot-first.** A free regex sweep finds every numeral/date/percentage/currency token; the LLM only *types and scopes* what the sweep hands it. This buys a recall denominator that needs no labels. A separate block-level semantic pass handles non-numeric facts.
- **Nothing document-specific.** No hard-coded filenames, entity names, metric names, or schemas. The ontology is grown at runtime. If a fix only works for Delhivery, it is not a fix.
- **Every LLM call is cached** on `(provider, model, prompt_hash)`. Re-runs must be free and byte-identical.
- Conventional commits, small and frequent. Feature branches → PR → main, even solo.
- Python: ruff, mypy, `core/` has no web dependencies and is importable standalone.
- Secrets: `.env.example` only. gitleaks runs in CI.

## Large documents

Never cap, never block. Spot-sweep the whole document first (regex, seconds even at 500 pages), then process pages in **descending candidate density** so fact-dense pages complete first, streaming claims over SSE as they land. Checkpoint per page so the job is resumable and killable. A soft token/time budget with a visible "keep going" control — not a hard page limit.

---

## Where things live

```
core/        the library — no web deps, importable, testable standalone
  parse/     pymupdf blocks, tables, bbox, vlm fallback
  normalize/ units, currency, scale, fiscal periods, indian numerals
  extract/   llm gateway, versioned prompts, structured schemas
  ground/    the verifier + quarantine
  canon/     entity + predicate resolution, evolving ontology
  link/      blocking, comparator, adjudicator, explainer
  route/     provider router + token ledger
  store/     sqlalchemy models, alembic migrations, queries
api/         FastAPI app, routers, SSE
web/         Next.js 15 app
evals/       golden set (yaml), runners, reports/
seed/        starter PDFs + snapshot.sql.gz
docs/adr/    architecture decision records
```
