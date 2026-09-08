"""Reading the corpus run's checkpoints back into a knowledge layer.

The overnight run writes one JSONL line per page as it goes. This loads those
back, rebuilds the ontology, generates candidate pairs and classifies them — the
whole downstream half of the pipeline, from claims that already exist.

It exists because the alternative was worse. Persistence is not built yet, and
waiting for a database schema before the UI could show anything would have meant
building the interface against mocks while eight hours of real extraction sat on
disk unused. Reading the checkpoints directly means every screen is driven by
genuine claims from genuine filings from the first commit — and when the
database does land, this becomes the loader that fills it rather than wasted
work.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import structlog

from core.canon.assign import canonicalise
from core.canon.embed import Embedder, build_embedder
from core.canon.registry import Registry
from core.link.compare import compare
from core.link.pairing import Pair, generate_pairs
from core.models import Claim, Relation, Verdict

log = structlog.get_logger(__name__)

CORPUS_DIR = Path("evals/corpus")


@dataclass
class DocumentSummary:
    id: str
    filename: str
    path: str
    sha256: str
    pages_processed: int
    claims: int
    quarantined: int
    entity: str | None = None

    @property
    def stem(self) -> str:
        return Path(self.filename).stem


@dataclass
class Edge:
    """A classified relationship between two claims, ready for the UI."""

    a: Claim
    b: Claim
    verdict: Verdict
    reason: str

    @property
    def cross_document(self) -> bool:
        return self.a.evidence[0].document_id != self.b.evidence[0].document_id


@dataclass
class KnowledgeLayer:
    documents: list[DocumentSummary] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    entities: Registry | None = None
    predicates: Registry | None = None

    def by_id(self, claim_id: str) -> Claim | None:
        return next((c for c in self.claims if str(c.id) == claim_id), None)

    def document(self, doc_id: str) -> DocumentSummary | None:
        return next((d for d in self.documents if d.id == doc_id), None)

    def counts(self) -> dict[str, int]:
        out = {r.value: 0 for r in Relation}
        for e in self.edges:
            out[e.verdict.relation.value] += 1
        return out

    def stats(self) -> dict:
        grounded = len(self.claims)
        total = grounded + len(self.quarantined)
        return {
            "documents": len(self.documents),
            "claims": grounded,
            "quarantined": len(self.quarantined),
            "grounding_pass_rate": round(grounded / total, 4) if total else 0.0,
            "entities": len(self.entities.nodes) if self.entities else 0,
            "predicates": len(self.predicates.nodes) if self.predicates else 0,
            "edges": len(self.edges),
            "relations": self.counts(),
        }


def _pdf_path(filename: str) -> str:
    """Locate a document's PDF anywhere under seed/.

    Searched rather than enumerated because uploads land in seed/uploads/, and a
    hard-coded list of two folders meant an uploaded document rendered no
    evidence at all -- the claims were fine, the page simply could not be found.
    """
    root = Path("seed")
    if not root.exists():
        return ""
    direct = root / filename
    if direct.exists():
        return str(direct)
    return next((str(p) for p in root.rglob(filename) if p.is_file()), "")


def _checkpoints(directory: Path) -> list[Path]:
    """Every checkpoint, preferring an uncompressed file over its archive.

    The shipped snapshot is gzipped and committed; a live run writes plain
    JSONL beside it. Preferring the plain file means a reviewer who ingests
    their own document sees their result rather than the shipped one, and
    deleting it falls back to the snapshot rather than to an empty app.
    """
    plain = {p.stem: p for p in directory.glob("*.jsonl")}
    out = list(plain.values())
    out += [
        p for p in directory.glob("*.jsonl.gz") if Path(p.stem).stem not in plain
    ]
    return out


def _open_checkpoint(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open(encoding="utf-8")


def load_claims(directory: Path = CORPUS_DIR) -> tuple[list[Claim], list[dict], list[DocumentSummary]]:
    """Read every checkpoint. Tolerates a torn final line from a killed run."""
    claims: list[Claim] = []
    quarantined: list[dict] = []
    docs: list[DocumentSummary] = []

    if not directory.exists():
        return claims, quarantined, docs

    for path in sorted(_checkpoints(directory)):
        pages = 0
        doc_claims = 0
        doc_quarantined = 0
        filename = ""
        sha = ""
        doc_uuid: UUID | None = None

        with _open_checkpoint(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # The last line of a checkpoint killed mid-write. Expected,
                    # not exceptional — the run is designed to be interrupted.
                    continue
                pages += 1
                filename = record.get("document", path.stem)
                sha = record.get("document_sha256", "")
                for raw in record.get("claims", []):
                    try:
                        claim = Claim.model_validate(raw)
                    except Exception:  # noqa: BLE001
                        continue
                    claims.append(claim)
                    doc_claims += 1
                    if doc_uuid is None and claim.evidence:
                        doc_uuid = claim.evidence[0].document_id
                for raw in record.get("quarantined", []):
                    quarantined.append({**raw, "page": record.get("page")})
                    doc_quarantined += 1

        if pages:
            docs.append(
                DocumentSummary(
                    id=str(doc_uuid) if doc_uuid else path.stem,
                    filename=filename,
                    path=_pdf_path(filename),
                    sha256=sha,
                    pages_processed=pages,
                    claims=doc_claims,
                    quarantined=doc_quarantined,
                )
            )

    return claims, quarantined, docs


def build(directory: Path = CORPUS_DIR, embedder: Embedder | None = None) -> KnowledgeLayer:
    """Load checkpoints and run canonicalisation, pairing and comparison."""
    claims, quarantined, docs = load_claims(directory)
    if not claims:
        return KnowledgeLayer(documents=docs, quarantined=quarantined)

    # build_embedder(), not HashingEmbedder().
    #
    # This defaulted to the hashing embedder, and `build_embedder` — which
    # prefers the local ONNX bge-small model and falls back only when it cannot
    # load — was never called from anywhere. So the entire shipped knowledge
    # layer was canonicalised on character trigrams: "Delhivery Ltd" matched
    # "Delhivery Limited" fine, but "revenue from operations" and "turnover"
    # never merged, because nothing in the pipeline understood that they mean
    # the same thing.
    #
    # The HashingEmbedder docstring predicted exactly this failure and said the
    # eval should show it. The eval did not show it, because a silent fallback
    # to a working-but-weaker component is invisible unless something measures
    # the difference. That is the real lesson here, and it is why the fallback
    # now logs loudly and the capabilities endpoint reports which one is live.
    emb = embedder or build_embedder()
    entities = Registry("entity", emb)
    predicates = Registry("predicate", emb)
    canon = canonicalise(claims, entities, predicates)

    for doc in docs:
        match = next(
            (c for c in canon.claims if c.evidence and str(c.evidence[0].document_id) == doc.id),
            None,
        )
        if match:
            doc.entity = match.subject_raw

    pairs, report = generate_pairs(canon.claims)
    edges: list[Edge] = []
    for pair in pairs:
        verdict = compare(pair.a, pair.b)
        if verdict.relation is Relation.UNRELATED:
            continue
        edges.append(Edge(a=pair.a, b=pair.b, verdict=verdict, reason=pair.reason))

    # Cross-document relationships first, then by confidence: the assignment's
    # first three cases are all about facts that meet across documents, and a
    # reader should not have to hunt for them behind a page of same-page pairs.
    edges.sort(key=lambda e: (not e.cross_document, -e.verdict.confidence))

    log.info(
        "knowledge_layer.built",
        claims=len(canon.claims),
        edges=len(edges),
        pairs=report.pairs,
        exact_blocks=report.exact_blocks,
        value_blocks=report.value_blocks,
    )
    return KnowledgeLayer(
        documents=docs,
        claims=canon.claims,
        quarantined=quarantined,
        edges=edges,
        entities=entities,
        predicates=predicates,
    )


def examples(layer: KnowledgeLayer, relation: Relation, limit: int = 25) -> list[Edge]:
    """The best illustrations of one relation, cross-document first."""
    return [e for e in layer.edges if e.verdict.relation is relation][:limit]


def pair_key(pair: Pair) -> str:
    return f"{pair.a.id}:{pair.b.id}"
