"""The evolving ontology.

The brief forbids document-specific schemas, so there is no metric enum and no
company list anywhere in this codebase. These tests check that the ontology
grows correctly from what documents actually say — and, more importantly, that
it errs in the safe direction when it is unsure.
"""

from __future__ import annotations

import pytest

from core.canon.embed import HashingEmbedder, cosine
from core.canon.registry import Action, Node, Registry, normalise_label


@pytest.fixture
def entities() -> Registry:
    return Registry("entity", HashingEmbedder())


@pytest.fixture
def predicates() -> Registry:
    return Registry("predicate", HashingEmbedder())


# ── normalisation ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Delhivery Limited", "Delhivery Ltd"),
        ("Delhivery Ltd.", "delhivery limited"),
        ("Delhivery Private Limited", "Delhivery Pvt Ltd"),
        ("ACME Corporation", "ACME Corp"),
    ],
)
def test_corporate_suffixes_do_not_distinguish_entities(a, b):
    """Generic company-naming vocabulary, not knowledge about any one document."""
    assert normalise_label(a, strip_suffixes=True) == normalise_label(b, strip_suffixes=True)


def test_suffix_stripping_does_not_apply_to_predicates():
    """"cost of goods" and "cost" are different metrics; the suffix rule is for
    company names only and must not bleed into metric labels."""
    assert normalise_label("total income", strip_suffixes=False) == "total income"


# ── resolution ───────────────────────────────────────────────────────────────


def test_the_same_name_resolves_to_the_same_node(entities):
    a = entities.resolve("Delhivery Limited")
    b = entities.resolve("Delhivery Limited")
    assert a.node_id == b.node_id
    assert b.action is Action.EXACT
    assert len(entities.nodes) == 1


def test_a_company_written_two_ways_resolves_to_one_node(entities):
    """The brief's own example: differently written names for the same thing."""
    a = entities.resolve("Delhivery Limited")
    b = entities.resolve("Delhivery Ltd")

    assert a.node_id == b.node_id
    assert len(entities.nodes) == 1
    assert entities.nodes[a.node_id].alias_count == 2


def test_genuinely_different_entities_stay_separate(entities):
    a = entities.resolve("Delhivery Limited")
    b = entities.resolve("Reserve Bank of India")
    assert a.node_id != b.node_id
    assert len(entities.nodes) == 2


def test_a_referring_expression_is_refused_rather_than_made_an_entity(entities):
    """"the Company" names whatever the document is about. Creating a node for it
    would merge every issuer in the corpus into one entity."""
    with pytest.raises(ValueError, match="referring expression"):
        entities.resolve("the Company")


def test_an_empty_label_is_refused(entities):
    with pytest.raises(ValueError):
        entities.resolve("   ")


# ── the asymmetry that matters ───────────────────────────────────────────────


def test_when_unsure_and_alone_the_ontology_keeps_labels_separate():
    """A wrong merge is worse than a duplicate.

    Merging two metrics that were never the same makes the system report
    confident contradictions between figures that were never comparable. A
    duplicate merely misses links — quiet, visible, fixable. The tie-break is
    therefore not neutral.
    """
    r = Registry("predicate", HashingEmbedder(), merge_threshold=0.99, create_threshold=0.10)
    r.resolve("revenue from operations")
    d = r.resolve("revenue from contracts with customers")

    assert d.action is Action.AMBIGUOUS_DEFAULTED
    assert len(r.nodes) == 2
    assert d.needs_review
    assert "wrong merge" in d.describe()


def test_ambiguous_decisions_form_a_work_queue_not_an_error_list():
    r = Registry("predicate", HashingEmbedder(), merge_threshold=0.99, create_threshold=0.10)
    r.resolve("revenue from operations")
    r.resolve("revenue from contracts with customers")
    assert len(r.pending_review()) == 1


def test_an_adjudicator_can_merge_the_ambiguous_band():
    """This is the only place a model touches the ontology, and it is handed both
    labels plus examples rather than being asked to invent a taxonomy."""
    seen: list[tuple[str, str]] = []

    def adjudicator(query: str, node: Node) -> tuple[bool, str]:
        seen.append((query, node.label))
        return True, "Both name the top line under Ind AS 115."

    r = Registry("predicate", HashingEmbedder(), merge_threshold=0.99, create_threshold=0.10)
    first = r.resolve("revenue from operations")
    second = r.resolve("revenue from contracts with customers", adjudicator=adjudicator)

    assert second.action is Action.LLM_MERGED
    assert second.node_id == first.node_id
    assert len(r.nodes) == 1
    assert seen == [("revenue from contracts with customers", "revenue from operations")]
    assert "Ind AS 115" in second.describe()


def test_an_adjudicator_can_also_refuse_to_merge():
    def adjudicator(query: str, node: Node) -> tuple[bool, str]:
        return False, "Total income includes other income; revenue does not."

    r = Registry("predicate", HashingEmbedder(), merge_threshold=0.99, create_threshold=0.10)
    r.resolve("revenue from operations")
    d = r.resolve("total income", adjudicator=adjudicator)

    assert d.action is Action.LLM_CREATED
    assert len(r.nodes) == 2
    assert "other income" in d.describe()


# ── auditability ─────────────────────────────────────────────────────────────


def test_every_decision_is_recorded_with_its_reasoning(entities):
    entities.resolve("Delhivery Limited")
    entities.resolve("Delhivery Ltd")
    entities.resolve("Reserve Bank of India")

    assert len(entities.decisions) == 3
    for d in entities.decisions:
        assert d.describe()
        assert d.action in set(Action)


def test_decisions_read_as_explanations_for_a_reader(entities):
    """The ontology view renders these. A reader has to be able to see why two
    labels were treated as one thing."""
    entities.resolve("Delhivery Limited")
    d = entities.resolve("Delhivery Ltd")
    text = d.describe()
    assert "Delhivery Ltd" in text
    assert "Delhivery Limited" in text


def test_stats_report_the_shape_of_the_ontology(entities):
    entities.resolve("Delhivery Limited")
    entities.resolve("Delhivery Ltd")
    entities.resolve("Reserve Bank of India")

    s = entities.stats()
    assert s["nodes"] == 2
    assert s["labels_seen"] == 3
    assert s["aliases"] == 3


# ── embeddings ───────────────────────────────────────────────────────────────


def test_the_fallback_embedder_still_captures_surface_similarity():
    """It is a working embedder, not a placeholder returning zeros — which is
    what lets the ontology function with no model download at all."""
    e = HashingEmbedder()
    a, b, c = e.embed(["Delhivery Limited", "Delhivery Ltd", "Reserve Bank of India"])
    assert cosine(a, b) > cosine(a, c)
    assert cosine(a, a) == pytest.approx(1.0, abs=1e-9)


def test_embeddings_are_deterministic():
    """Re-running the pipeline must produce byte-identical output, and an
    embedder that drifted would silently break that guarantee."""
    e = HashingEmbedder()
    assert e.embed(["revenue from operations"]) == e.embed(["revenue from operations"])


def test_cosine_handles_degenerate_input():
    assert cosine([], []) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine([1.0], [1.0, 2.0]) == 0.0


def test_a_number_is_not_a_predicate():
    """The ontology grew nodes called "0.10" and "0.09".

    A predicate is the name of a property — what the value measures. A model
    that returns "0.10" has copied a neighbouring cell instead of naming
    anything, and the node it creates then collects every unrelated figure that
    happens to round the same way. The vocabulary screen showed these sitting
    among "security deposits" and "contract assets", which is where they were
    noticed.

    Rejected at extraction rather than filtered in the ontology: a claim whose
    predicate is a number has no property to compare, so it is not a claim.
    """
    from core.extract.llm import _is_a_property

    assert not _is_a_property("0.10")
    assert not _is_a_property("1,266")
    assert not _is_a_property("(452)")
    assert not _is_a_property("2024")
    assert not _is_a_property("%")
    assert _is_a_property("revenue from operations")
    assert _is_a_property("EBITDA margin")
    assert _is_a_property("Ind AS 116 lease liability")
