# ADR-0002 — Ollama runs natively on the host, never in a container

**Status:** accepted · **Date:** 2026-09-07

## Context

Groq's free tier is 30 RPM / 1K RPD / **8K TPM / 200K TPD**. Two hundred thousand
tokens per day is roughly one hundred-page PDF, once. Bulk extraction over the
six-document starter corpus is therefore impossible on Groq, and the work has to
run on local hardware: an RTX 4060 Laptop with 8188 MiB of VRAM.

The question is whether the local model server belongs inside the Docker Compose
stack alongside everything else.

## Decision

Ollama runs **natively on the host**. It is not a service in `docker-compose.yml`.
The containerised worker reaches it at `host.docker.internal:11434`, with
`extra_hosts: host-gateway` so the same address resolves on Linux hosts too.

## Rationale

- **GPU passthrough into Docker on Windows is fragile.** It requires WSL2 with
  the CUDA toolkit plumbed through, a matching driver, and `--gpus all` support
  in Docker Desktop. Every one of those is a thing that can be subtly wrong.
- **It would become a setup instruction the graders have to follow.** The entire
  point of shipping a pre-computed snapshot is that `docker compose up` works on
  an unseen machine with no key, no GPU, and no model download. Putting a GPU
  service in the compose file undoes that, even if it is behind a profile.
- **Memory is the binding constraint on this host** — 15.3 GB shared between the
  WSL2 VM, Postgres, a Next.js dev server, a browser, and the model. Running
  Ollama natively means the model weights are resident once, not once in the
  host page cache and again inside a VM.
- **It is what a reviewer would do anyway.** Anyone running a local model on
  their own machine already has Ollama installed natively.

## What we give up

- The stack is no longer *entirely* described by `docker-compose.yml`. Mitigated
  by making Ollama strictly optional: it is Tier 2, the snapshot covers Tier 0,
  and Tier 3 works with nothing at all.
- Slightly more surface area in the README. Accepted — it is four lines, and
  aimed only at readers who want to ingest their own large documents.

## Consequences

- `OLLAMA_HOST` defaults to `http://host.docker.internal:11434`.
- The capability probe treats an unreachable Ollama as normal and unremarkable,
  not as an error.
- `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1` are set on the host so
  the text and vision models are never resident simultaneously on 8 GB of VRAM.
