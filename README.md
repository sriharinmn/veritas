# Veritas — a fact knowledge layer for financial documents

> **Status: in development.** This README is written alongside the build rather
> than at the end of it, so sections marked _pending_ are honestly pending.
> Numbers appear here only once they have been measured.

Extracts grounded facts from financial PDFs, links every fact to the exact span
of the page it came from, and works out whether facts across documents
**corroborate**, **contradict**, or are **reconcilable through context**.

Built for the Superjoin VIT 2026 engineering intern assignment.

---

## Setup and run instructions

```bash
git clone <repo-url>
cd veritas
docker compose up
```

Open **http://localhost:3000**. That is the whole setup.

**You do not need an API key.** The repository ships a pre-computed knowledge
layer over the six starter PDFs, so the app boots already populated — facts,
evidence highlighting, contradictions, reconciliations and eval scores are all
there immediately. No key, no GPU, no waiting, no spend.

To ingest **your own** PDFs, add a provider to `.env` (see `.env.example`):

| Tier | What it needs | Best for |
|---|---|---|
| 1 · Groq | a free API key, 60 seconds to get | documents under ~20 pages |
| 2 · Ollama | `ollama pull qwen3:8b` on your host | large documents; no rate limits |
| 3 · Deterministic | nothing at all | works with zero configuration, lower recall, says so loudly |

The API is at **http://localhost:8000**, with OpenAPI docs at `/docs`.

<details>
<summary>Useful commands</summary>

```bash
docker compose up -d              # start
docker compose logs -f            # follow logs
docker compose down               # stop
docker compose exec api pytest    # tests
```
</details>

---

## Video demo

_pending — P8._

---

## Approach

_Full write-up pending. `plan.md` is the working design document and
`docs/adr/` holds the decision records._

The short version: **a fact is not a sentence, it is a typed tuple with an
explicit scope.**

```
Claim(subject, predicate, value, scope, evidence)
```

where `scope` carries seven axes — `period, basis, segment, geography,
accounting, modality, vintage`. Once claims are normalised into that shape, the
three relations the assignment asks for stop being an LLM's opinion and become
properties you can derive:

| Same subject and predicate, and… | Verdict |
|---|---|
| values agree after unit normalisation, scopes compatible | **corroboration** |
| values disagree, scopes identical | **contradiction** |
| values disagree, scopes differ on exactly one axis | **reconciled** — and that axis *is* the explanation |

The LLM is confined to what it is genuinely good at: reading a page into a tuple,
judging whether two names refer to one thing, adjudicating the ambiguous
residue, and writing the prose explanation. Everything else is deterministic,
cheap, and unit-testable.

Two consequences worth calling out early:

- **Grounding is a hard gate.** A claim whose value is not literally present in
  the span it cites is quarantined and never enters the graph. Not flagged —
  quarantined. The pass rate is published rather than assumed.
- **Extraction is spot-first.** A free regex sweep finds every numeral, date and
  currency token; the LLM only types and scopes what the sweep hands it. That
  buys a recall *denominator* — you can count what the system failed to notice —
  without a single hand-written label.

### The four required cases

_pending — P7. Each will appear here inline, with cropped evidence._

1. A fact corroborated across documents, expressed differently — _pending_
2. A genuine or likely contradiction — _pending_
3. An apparent contradiction explained by context — _pending_
4. An extraction or reasoning failure, and how it is handled — _pending_

---

## Limitations and next steps

_pending — written from measurement at P6, not from memory._

---

## Additional notes

- **Credentials:** none in this repository. `.env.example` only; `gitleaks` runs
  in CI. The committed snapshot is derived data.
- **Cost to build:** ₹0. Bulk extraction ran on a local RTX 4060; adjudication
  used Groq's free tier. Total token spend is reported by the ledger.
- **Architecture decisions** are in `docs/adr/`, one file per decision, each
  stating what was rejected and what it cost.
