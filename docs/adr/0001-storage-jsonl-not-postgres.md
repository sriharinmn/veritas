# ADR-0001 — Storage: append-only JSONL, not Postgres, and not a graph database

**Status:** superseded in part, 2026-09-08. Originally accepted 2026-09-06 as
"PostgreSQL + pgvector, not a graph database". The reasoning against a graph
database still holds. The decision *for* Postgres was reversed after it was
built and measured, and this record has been rewritten to say so rather than
leaving a document that quietly disagrees with the code.

## Context

The brief says plainly that "a graph database or visualization alone is not the
solution", and it is right: the hard part here is normalising a value so two
documents can be compared at all, and grounding a claim in the span it came
from. Traversal is the easy part.

So the question was never graph-versus-relational. It was what a knowledge layer
of this size actually needs from a store.

## The original decision

PostgreSQL 16 with `pgvector`: one container, ACID upserts for incremental
ingest, JSONB for the flexible scope payload, generated columns for normalised
magnitudes, and approximate-nearest-neighbour over predicate and entity
embeddings. The "graph" is three tables and a recursive CTE.

That reasoning is sound and would be right at a larger scale.

## What actually happened

The database was declared in `docker-compose.yml`, the `api` and `worker`
services waited on its health check, `DATABASE_URL` was threaded through to
both, and `sqlalchemy`, `alembic`, `psycopg` and `pgvector` were all listed as
dependencies.

**Nothing ever opened a connection.** The only reference to `DATABASE_URL`
anywhere in the codebase was a worker splitting the string to put the host in a
log line. The knowledge layer was built from append-only JSONL checkpoints from
the first day, because the corpus run had to be resumable and killable before
any schema existed — and that turned out to be enough:

| | |
|---|---|
| documents | 6 |
| claims | 11,180 |
| relations | 115,294 |
| on disk, gzipped | 0.8 MB |
| full rebuild | 101 seconds |
| incremental add | proportional to the new document |

The 101-second rebuild was the one real argument for a database, and it was
answered without one: `extend()` canonicalises only the claims that arrived,
into the registries already in memory, and pairs them against what is already
known. A test asserts it produces the same relation counts as a full rebuild.

## Decision

Remove the database, the worker, and the four unused dependencies. The store is
`evals/corpus/*.jsonl`, append-only, one line per page, with a gzipped snapshot
committed so a fresh clone boots populated.

## Why removing it is better than leaving it

Shipping a Postgres that nothing connects to is worse than shipping none. A
reader who opens the compose file has to work out for themselves that the
container does nothing, and every minute they spend on that is a minute of
doubt about everything else. Unused infrastructure is not free optionality; it
is a claim about the system that is not true.

## Consequences

**Good.** `docker compose up` is two services instead of four and has no
health-check ordering to get wrong. There is no schema to migrate, and a
checkpoint is readable with `zcat`. The upload path and the overnight corpus run
write byte-identical artefacts, so neither needs special handling anywhere.

**Bad, and accepted.** There is no concurrent writer story: two processes
appending to one checkpoint would interleave lines. The corpus run and the API
never do this, but nothing enforces it. There are no transactions, so a killed
process can leave a torn final line — the loader tolerates exactly that, by
design, and the migrations that repaired document ids and signs all had to
handle it too. Queries are linear scans over an in-memory list, which is fine at
eleven thousand claims and would not be at ten million.

**The threshold.** This choice stops being right at roughly the point where the
layer no longer fits in memory, or where more than one process needs to write.
Postgres remains the correct next step, and `core/store/checkpoints.py` is
already the shape of a loader that would fill it — `load_claims` becomes the
importer rather than wasted work.
