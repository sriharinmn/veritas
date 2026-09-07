"""The evolving ontology.

Predicates and entities are not enumerated anywhere in this codebase. There is
no list of metrics, no list of companies, no schema written in advance — because
the brief rules that out: *"it should not rely on hard-coded facts, filenames,
schemas, or document-specific rules."* A system with a fixed metric enum works
beautifully on Delhivery and returns nothing useful on the next filing.

So the ontology is grown at runtime. A new label arrives, and:

    exact or alias match                → resolve to that node
    cosine >= MERGE_THRESHOLD           → merge, record the alias
    cosine <  CREATE_THRESHOLD          → create a new node
    in between                          → ambiguous; ask the model, and if no
                                          model is available, CREATE

That last default is the important one. **A wrong merge is worse than a
duplicate.** Merging "revenue from operations" into "total income" fabricates
relationships between metrics that were never the same thing, and the system
then reports confident contradictions between figures that were never
comparable. A duplicate node merely misses some links — quiet, visible in the
ontology view, and fixable. The costs are not symmetric, so the tie-break is not
neutral.

Every decision is recorded with its similarity and its reasoning, so the
ontology view can show a reader exactly why two labels were treated as one
thing, and so a threshold change can be replayed against the recorded history
rather than guessed at.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

import structlog

from core.canon.embed import Embedder, HashingEmbedder, cosine

log = structlog.get_logger(__name__)

NodeKind = Literal["entity", "predicate"]

# Tuned against the golden set; their effect is an eval metric, not a vibe.
MERGE_THRESHOLD = 0.92
CREATE_THRESHOLD = 0.72

# Corporate and statutory suffixes carry no distinguishing information, so
# "Delhivery Ltd" and "Delhivery Limited" must normalise to the same key. This
# is generic company-naming vocabulary, not knowledge about any one document.
_SUFFIXES = (
    "limited", "ltd", "private", "pvt", "public", "plc", "incorporated", "inc",
    "corporation", "corp", "company", "co", "llp", "llc", "gmbh", "sa", "nv",
)
_SUFFIX_RE = re.compile(rf"\b(?:{'|'.join(_SUFFIXES)})\b\.?", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

# Referring expressions never name a distinct entity; resolving them is the
# document-context pass's job, not the registry's.
_ANAPHORA = {
    "the company", "the group", "the issuer", "the bank", "we", "us", "our company",
    "it", "the corporation", "the firm", "the entity",
}


class Action(StrEnum):
    EXACT = "exact"
    ALIAS = "alias"
    MERGED = "merged"
    CREATED = "created"
    LLM_MERGED = "llm_merged"
    LLM_CREATED = "llm_created"
    AMBIGUOUS_DEFAULTED = "ambiguous_defaulted_to_new"


def normalise_label(text: str, *, strip_suffixes: bool = False) -> str:
    s = _PUNCT_RE.sub(" ", (text or "").lower())
    if strip_suffixes:
        s = _SUFFIX_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


@dataclass
class Node:
    id: UUID
    kind: NodeKind
    label: str
    aliases: set[str] = field(default_factory=set)
    embedding: list[float] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    @property
    def alias_count(self) -> int:
        return len(self.aliases)


@dataclass
class Decision:
    """One resolution, kept so the ontology can explain itself."""

    query: str
    kind: NodeKind
    node_id: UUID | None
    node_label: str
    action: Action
    similarity: float = 0.0
    nearest_label: str | None = None
    reasoning: str | None = None

    @property
    def needs_review(self) -> bool:
        return self.action is Action.AMBIGUOUS_DEFAULTED

    def describe(self) -> str:
        match self.action:
            case Action.EXACT | Action.ALIAS:
                return f"{self.query!r} is a known name for {self.node_label!r}"
            case Action.MERGED | Action.LLM_MERGED:
                return (
                    f"{self.query!r} merged into {self.node_label!r} "
                    f"(similarity {self.similarity:.2f})"
                    + (f" — {self.reasoning}" if self.reasoning else "")
                )
            case Action.AMBIGUOUS_DEFAULTED:
                return (
                    f"{self.query!r} is {self.similarity:.2f} similar to "
                    f"{self.nearest_label!r} — too close to separate confidently and too "
                    f"far to merge, and no adjudicator was available. Kept separate, "
                    f"because a wrong merge invents relationships and a duplicate only "
                    f"misses them."
                )
            case _:
                nearest = (
                    f"; nearest existing was {self.nearest_label!r} at {self.similarity:.2f}"
                    if self.nearest_label
                    else ""
                )
                # A refusal to merge is as informative as a merge, and often more
                # so — it is the record of why two similar-looking metrics are
                # genuinely different things.
                why = f" — {self.reasoning}" if self.reasoning else ""
                return f"{self.query!r} created as a new {self.kind}{nearest}{why}"


# Given (query, candidate_node) return True to merge, with a reason.
Adjudicator = Callable[[str, "Node"], tuple[bool, str]]


class Registry:
    """An in-memory ontology for one kind of node.

    Deliberately storage-agnostic: it holds nodes and answers questions about
    them, and persistence is somebody else's problem. That keeps the resolution
    logic — which is the part with the interesting judgement in it — testable
    without a database.
    """

    def __init__(
        self,
        kind: NodeKind,
        embedder: Embedder | None = None,
        *,
        merge_threshold: float = MERGE_THRESHOLD,
        create_threshold: float = CREATE_THRESHOLD,
    ) -> None:
        self.kind = kind
        self.embedder = embedder or HashingEmbedder()
        self.merge_threshold = merge_threshold
        self.create_threshold = create_threshold
        self.nodes: dict[UUID, Node] = {}
        self.decisions: list[Decision] = []
        self._by_key: dict[str, UUID] = {}

    # ── lookup ───────────────────────────────────────────────────────────────

    def _key(self, label: str) -> str:
        return normalise_label(label, strip_suffixes=self.kind == "entity")

    def _nearest(self, vector: list[float]) -> tuple[Node | None, float]:
        best: Node | None = None
        best_sim = 0.0
        for node in self.nodes.values():
            sim = cosine(vector, node.embedding)
            if sim > best_sim:
                best, best_sim = node, sim
        return best, best_sim

    # ── resolution ───────────────────────────────────────────────────────────

    def resolve(
        self, label: str, *, adjudicator: Adjudicator | None = None, example: str | None = None
    ) -> Decision:
        raw = (label or "").strip()
        if not raw:
            raise ValueError("cannot resolve an empty label")

        # Checked before suffix stripping, which would otherwise defeat the guard:
        # "the Company" strips to "the" and would sail straight through as a
        # perfectly ordinary new entity — merging every issuer in the corpus.
        if self.kind == "entity" and normalise_label(raw) in _ANAPHORA:
            raise ValueError(
                f"{label!r} is a referring expression, not an entity name. Resolving it "
                f"is the document-context pass's job."
            )

        key = self._key(raw)
        if not key:
            raise ValueError(f"label {label!r} normalises to nothing")

        if (existing := self._by_key.get(key)) is not None:
            node = self.nodes[existing]
            action = Action.EXACT if self._key(node.label) == key else Action.ALIAS
            self._remember(node, raw, example)
            return self._record(Decision(raw, self.kind, node.id, node.label, action, 1.0))

        vector = self.embedder.embed([raw])[0]
        nearest, sim = self._nearest(vector)

        if nearest is not None and sim >= self.merge_threshold:
            self._remember(nearest, raw, example)
            self._by_key[key] = nearest.id
            return self._record(
                Decision(raw, self.kind, nearest.id, nearest.label, Action.MERGED, sim, nearest.label)
            )

        if nearest is not None and sim >= self.create_threshold:
            if adjudicator is not None:
                merge, reason = adjudicator(raw, nearest)
                if merge:
                    self._remember(nearest, raw, example)
                    self._by_key[key] = nearest.id
                    return self._record(
                        Decision(
                            raw, self.kind, nearest.id, nearest.label,
                            Action.LLM_MERGED, sim, nearest.label, reason,
                        )
                    )
                node = self._create(raw, vector, example)
                return self._record(
                    Decision(
                        raw, self.kind, node.id, node.label,
                        Action.LLM_CREATED, sim, nearest.label, reason,
                    )
                )
            node = self._create(raw, vector, example)
            return self._record(
                Decision(
                    raw, self.kind, node.id, node.label,
                    Action.AMBIGUOUS_DEFAULTED, sim, nearest.label,
                )
            )

        node = self._create(raw, vector, example)
        return self._record(
            Decision(
                raw, self.kind, node.id, node.label, Action.CREATED, sim,
                nearest.label if nearest else None,
            )
        )

    # ── mutation ─────────────────────────────────────────────────────────────

    def _create(self, label: str, vector: list[float], example: str | None) -> Node:
        node = Node(id=uuid4(), kind=self.kind, label=label, embedding=vector)
        node.aliases.add(label)
        if example:
            node.examples.append(example)
        self.nodes[node.id] = node
        self._by_key[self._key(label)] = node.id
        return node

    def _remember(self, node: Node, alias: str, example: str | None) -> None:
        node.aliases.add(alias)
        if example and len(node.examples) < 5:
            node.examples.append(example)

    def _record(self, decision: Decision) -> Decision:
        self.decisions.append(decision)
        if decision.needs_review:
            log.info(
                "ontology.ambiguous",
                kind=self.kind,
                query=decision.query,
                nearest=decision.nearest_label,
                similarity=round(decision.similarity, 3),
            )
        return decision

    # ── reporting ────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for d in self.decisions:
            counts[d.action.value] = counts.get(d.action.value, 0) + 1
        return {
            "nodes": len(self.nodes),
            "labels_seen": len(self.decisions),
            "aliases": sum(n.alias_count for n in self.nodes.values()),
            **counts,
        }

    def pending_review(self) -> list[Decision]:
        """Pairs a model should adjudicate when one becomes available.

        This is a work queue, not an error list: each entry is a place where the
        ontology chose safety over completeness and could be improved.
        """
        return [d for d in self.decisions if d.needs_review]
