# ADR-0001 — PostgreSQL + pgvector, not a graph database

**Status:** accepted · **Date:** 2026-09-07

## Context

This system stores facts and the relationships between them. The obvious reach is
for a graph database — Neo4j, or similar — because "knowledge layer" and "graph"
sit next to each other in most people's heads.

The assignment brief pre-empts exactly that instinct:

> A graph database or visualization alone is not the solution. The interesting
> part is how facts are discovered, grounded, compared, and explained.

## Decision

PostgreSQL 16 with the `pgvector` extension. One database, one container.

The graph is three tables — `claims`, `claim_edges`, and the canonical
`entities`/`predicates` nodes — traversed with recursive CTEs where traversal is
actually needed, which is rarely and shallowly.

## Rationale

- **The hard part is not traversal.** It is unit normalisation, fiscal-period
  reconciliation, entity resolution, and grounding verification. None of those
  are graph problems. Choosing infrastructure that optimises traversal would be
  optimising the part of the system that is already easy.
- **Incremental ingest needs ACID upserts.** Adding a document must not corrupt
  the ontology if it fails halfway. Postgres gives this without ceremony.
- **One store instead of three.** JSONB + GIN for the flexible `scope`/`value`
  payloads, generated columns + B-tree for the normalised magnitude and period
  bounds, `pgvector` for approximate-nearest-neighbour over predicate and entity
  embeddings. A graph database would have meant Neo4j *plus* a vector store
  *plus* a relational store for the ledger and eval history.
- **Reviewer setup cost.** Every additional service is another thing that can
  fail during `docker compose up` on a machine we have never seen.

## What we give up

- Deep multi-hop traversal is clumsy in SQL compared with Cypher. We accept this
  because our traversals are one and two hops.
- No out-of-the-box graph visualisation. This is a cost we are content to pay,
  given the brief explicitly warns against leading with one.
- If the fact graph later grew to tens of millions of edges with genuine
  path-finding queries, this decision should be revisited.

## Alternatives considered

- **Neo4j.** Rejected: optimises the easy part, adds a service, and reads as
  having built for the demo artefact rather than for the reasoning.
- **SQLite + a separate vector index.** Rejected: concurrent worker and API
  access, and no mature in-process ANN story.
- **Postgres without pgvector, cosine in Python.** Viable at six documents,
  falls over at a hundred. Not worth the later rewrite.
