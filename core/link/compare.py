"""The deterministic comparator — the heart of the system.

This is the module that makes the assignment's three required relations
*derivable* rather than a matter of an LLM's opinion:

    same subject and predicate, and…
      scopes identical, values agree         → CORROBORATION
      scopes identical, values disagree      → CONTRADICTION
      scopes differ on exactly one axis      → RECONCILED, and that axis is why
      scopes differ on two or more axes      → AMBIGUOUS, escalate to the model

Three properties follow, and all three are worth more than a cleverer approach
would be:

- **Explainable by construction.** The verdict carries the axis that produced
  it, so the UI can show the machine's reasoning steps rather than only a fluent
  paragraph. "Explained" in the brief means the reader can follow the decision.
- **Cheap.** No model call for the overwhelming majority of pairs.
- **Testable.** A pure function over two claims. Hundreds of cases, no flakiness.

The LLM sees only the residue, and it receives this trace as context.
"""

from __future__ import annotations

from decimal import Decimal

from core.models import Claim, Relation, TypedValue, ValueKind, Verdict

# Relative tolerance for magnitudes whose precision cannot be inferred.
# Deliberately a single global constant for now, and named as a known limitation:
# 0.5% means something very different for a GDP growth rate than for a headcount,
# and this should eventually be per-predicate.
DEFAULT_REL_TOL = Decimal("0.005")

# Ratios are compared in absolute percentage points, not relatively. A growth
# rate of 6.5% against 6.4% is 1.5% apart relatively but 0.1pp apart in the way
# an economist means it, and these documents round rates to a single decimal.
RATIO_ABS_TOL = Decimal("0.0006")  # 0.06 percentage points


def _significant_digits(raw: str) -> int:
    """How precisely the document actually stated this number.

    "7,225 crore" asserts four significant digits. "72,251 million" asserts
    five. Comparing them at full precision would call them different; comparing
    at the precision of the coarser one — which is all its author claimed — shows
    they agree. Rounding is not disagreement.
    """
    digits = "".join(ch for ch in raw if ch.isdigit())
    stripped = digits.lstrip("0")
    if not stripped:
        return 1
    # Trailing zeros before a decimal point are not necessarily significant, but
    # assuming they are is the conservative choice: it tightens the tolerance
    # rather than loosening it, so it can only make the comparator stricter.
    return len(stripped)


def _rounding_tolerance(value: Decimal, raw: str) -> Decimal:
    """One unit in the last place the document actually wrote down."""
    if value == 0:
        return Decimal("0")
    sig = _significant_digits(raw)
    exponent = value.copy_abs().adjusted() - sig + 1
    return Decimal(10) ** exponent


def values_agree(a: TypedValue, b: TypedValue) -> tuple[bool, str]:
    """Do these two values assert the same thing? Returns the reason either way.

    The reason string goes straight into the verdict trace, so it has to read as
    an explanation rather than as a debug message.
    """
    if a.kind is not b.kind:
        # Money and quantity are genuinely different kinds of assertion; a
        # revenue figure and a parcel count are not comparable even if the
        # numbers happen to coincide.
        if {a.kind, b.kind} != {ValueKind.MONEY, ValueKind.QUANTITY}:
            return False, f"different value kinds ({a.kind} vs {b.kind})"

    if a.kind is ValueKind.MONEY and b.kind is ValueKind.MONEY:
        if a.currency and b.currency and a.currency != b.currency:
            return False, f"different currencies ({a.currency} vs {b.currency}), not converted"

    if a.kind is ValueKind.DATE or b.kind is ValueKind.DATE:
        same = a.date_value == b.date_value
        return same, "same date" if same else f"{a.date_value} vs {b.date_value}"

    if a.kind is ValueKind.TEXT or b.kind is ValueKind.TEXT:
        x, y = (a.text_value or a.raw).strip().lower(), (b.text_value or b.raw).strip().lower()
        same = x == y
        return same, "identical text" if same else "different text"

    if a.kind is ValueKind.BOOL or b.kind is ValueKind.BOOL:
        same = a.bool_value == b.bool_value
        return same, "same" if same else f"{a.bool_value} vs {b.bool_value}"

    # Range containment. A point estimate inside a stated range is agreement:
    # an IMF projection of 6.5% against an Economic Survey range of 6.3-6.8% is
    # a corroboration, and reporting it as a conflict would be a false alarm of
    # exactly the kind that destroys trust in the output.
    contained, why = _range_agreement(a, b)
    if contained is not None:
        return contained, why

    if a.canonical_magnitude is None or b.canonical_magnitude is None:
        return False, "one or both values could not be normalised"

    x, y = a.canonical_magnitude, b.canonical_magnitude
    if x == y:
        return True, "identical after normalisation"

    if a.kind is ValueKind.RATIO:
        diff = abs(x - y)
        if diff <= RATIO_ABS_TOL:
            return True, f"agree within {diff * 100:.3f} percentage points"
        return False, f"differ by {diff * 100:.2f} percentage points"

    # Compare at the precision the coarser statement actually claimed.
    tol = max(
        _rounding_tolerance(x, a.raw),
        _rounding_tolerance(y, b.raw),
        abs(x) * DEFAULT_REL_TOL if x else Decimal(0),
    )
    diff = abs(x - y)
    if diff <= tol:
        rel = (diff / abs(x) * 100) if x else Decimal(0)
        return True, f"agree within rounding ({rel:.4f}% apart, tolerance {tol})"

    rel = (diff / abs(x) * 100) if x else Decimal(0)
    return False, f"differ by {rel:.2f}%"


def _same_page_same_predicate(a: Claim, b: Claim) -> bool:
    """Two figures for one metric, on one page of one document.

    This is not what a disagreement looks like. A document does not usually
    state the same measure twice, for the same period, with two different
    values, on the same page — but a *two-column statement* does exactly that on
    every single row, because it prints this year beside last year:

        Depreciation and amortisation expense   27   7,215.50   8,311.44
                                                     March 2024  March 2023

    When the column header is not recovered on that page both figures inherit
    the current period, and the pair then differs in value with every scope axis
    identical, which is the definition of a contradiction. Measured on the
    corpus: 16,319 of 17,873 contradictions — 91.3% — had this exact shape.

    Both numbers are real and both are correctly grounded. What is wrong is the
    period on one of them, and that is a column-recovery problem rather than a
    disagreement between sources. Reporting it as a conflict is the same
    mistake as reporting an absent period as one: asserting a finding where
    there is only missing information.

    So these escalate rather than accuse. They stay visible as ambiguous, the
    trace says precisely why, and the honest count of contradictions is what is
    left after they are removed.
    """
    if not (a.evidence and b.evidence):
        return False
    ea, eb = a.evidence[0], b.evidence[0]
    return (
        ea.document_id == eb.document_id
        and ea.page == eb.page
        and a.predicate_id is not None
        and a.predicate_id == b.predicate_id
    )


def _period_established(a: Claim, b: Claim) -> bool:
    """Is there positive evidence that these two claims cover the same period?

    Only the period is required, deliberately. It is the axis that decides
    comparability for almost every financial figure, it is the one most often
    absent, and demanding that every axis be pinned down would suppress genuine
    contradictions in documents that simply never state a segment.
    """
    return a.scope.period.start is not None and b.scope.period.start is not None


def _range_agreement(a: TypedValue, b: TypedValue) -> tuple[bool | None, str]:
    if a.is_range and b.is_range:
        overlap = not (a.range_high < b.range_low or b.range_high < a.range_low)
        return overlap, "ranges overlap" if overlap else "ranges do not overlap"
    if a.is_range and b.canonical_magnitude is not None:
        inside = a.range_low <= b.canonical_magnitude <= a.range_high
        return inside, (
            f"{b.raw} falls inside the stated range {a.raw}"
            if inside
            else f"{b.raw} falls outside the stated range {a.raw}"
        )
    if b.is_range and a.canonical_magnitude is not None:
        inside = b.range_low <= a.canonical_magnitude <= b.range_high
        return inside, (
            f"{a.raw} falls inside the stated range {b.raw}"
            if inside
            else f"{a.raw} falls outside the stated range {b.raw}"
        )
    return None, ""


AXIS_EXPLANATION = {
    "period": "the two statements cover different periods",
    "basis": "one is consolidated and the other is not",
    "segment": "the two statements cover different business segments",
    "geography": "the two statements cover different geographies",
    "accounting": "the two statements are prepared under different accounting standards",
    "modality": "one is a projection or estimate and the other is a reported actual",
}


def compare(a: Claim, b: Claim) -> Verdict:
    """Classify the relationship between two claims.

    Assumes the caller has already established that the two claims are about the
    same subject and predicate — that is the canonicalisation layer's job, and
    doing it here would conflate two very different kinds of judgement.
    """
    trace: list[str] = []

    same_subject = a.subject_id is not None and a.subject_id == b.subject_id
    same_predicate = a.predicate_id is not None and a.predicate_id == b.predicate_id

    if not same_subject:
        return Verdict(
            relation=Relation.UNRELATED,
            confidence=1.0,
            trace=[f"subjects differ: {a.subject_raw!r} vs {b.subject_raw!r}"],
        )
    if not same_predicate:
        return Verdict(
            relation=Relation.UNRELATED,
            confidence=1.0,
            trace=[f"predicates differ: {a.predicate_raw!r} vs {b.predicate_raw!r}"],
        )

    trace.append(f"subject ≡ {a.subject_raw!r}")
    trace.append(f"predicate ≡ {a.predicate_raw!r}")

    diffs = a.scope.differing_axes(b.scope)
    trace.append(
        "scopes are identical on every axis"
        if not diffs
        else f"scope differs on: {', '.join(diffs)}"
    )

    agree, why = values_agree(a.value, b.value)
    trace.append(f"values {'agree' if agree else 'disagree'}: {why}")
    trace.append(f"{a.value.raw!r} → {a.value.canonical_magnitude}")
    trace.append(f"{b.value.raw!r} → {b.value.canonical_magnitude}")

    confidence = min(a.confidence, b.confidence)

    if not diffs:
        if agree:
            return Verdict(
                relation=Relation.CORROBORATION,
                confidence=confidence,
                trace=trace + ["→ corroboration: same scope, same value"],
            )

        # Corroboration and contradiction do not carry the same evidentiary
        # burden, and treating them as though they did is how this system spent
        # an evening manufacturing conflicts.
        #
        # Saying two figures agree is a mild claim. Saying they *contradict* is
        # an accusation placed in front of somebody who will act on it, and it
        # requires positive confirmation that the two statements describe the
        # same thing — not merely the absence of evidence that they do not. When
        # neither claim carries a resolved period, "no axis differs" means only
        # that nothing is known, and asserting a conflict from that is
        # unjustified.
        #
        # Measured on the corpus before this change: 26,933 contradictions, of
        # which 14,003 — 52% — had no resolved period on either side. Those were
        # not findings. They were the absence of information, reported as a
        # finding.
        if not _period_established(a, b):
            return Verdict(
                relation=Relation.AMBIGUOUS,
                confidence=confidence * 0.5,
                trace=trace
                + [
                    "→ ambiguous: the values differ, but neither claim carries a "
                    "resolved period, so there is no positive evidence that the two "
                    "statements cover the same thing. A contradiction needs that "
                    "evidence; its absence is not a finding."
                ],
            )

        if _same_page_same_predicate(a, b):
            return Verdict(
                relation=Relation.AMBIGUOUS,
                confidence=confidence * 0.5,
                trace=trace
                + [
                    "→ ambiguous: both figures are the same measure on the same page "
                    "of the same document. That is the shape of a two-column "
                    "statement printing this year beside last year, not the shape of "
                    "a disagreement — so the likeliest explanation is that one of "
                    "the two inherited the wrong period from an unrecovered column "
                    "header, and a contradiction cannot be asserted over it."
                ],
            )

        return Verdict(
            relation=Relation.CONTRADICTION,
            confidence=confidence,
            trace=trace
            + [
                "→ contradiction: every scope axis was checked and found identical, "
                "so no difference in period, basis, segment, geography, accounting "
                "standard or modality explains the gap"
            ],
        )

    if len(diffs) == 1:
        axis = diffs[0]
        if agree:
            # Agreeing across a scope difference is weaker evidence than agreeing
            # within one — the same number under two different scopes may be a
            # coincidence, so the confidence is discounted rather than asserted.
            return Verdict(
                relation=Relation.CORROBORATION,
                axis=axis,
                confidence=confidence * 0.8,
                trace=trace + [f"→ corroboration across a difference in {axis}"],
            )
        return Verdict(
            relation=Relation.RECONCILED,
            axis=axis,
            confidence=confidence,
            trace=trace
            + [f"→ reconciled: the values differ because {AXIS_EXPLANATION.get(axis, axis)}"],
        )

    return Verdict(
        relation=Relation.AMBIGUOUS,
        confidence=confidence * 0.6,
        trace=trace
        + [
            f"→ ambiguous: {len(diffs)} scope axes differ ({', '.join(diffs)}), so no "
            "single difference explains the gap — escalating for adjudication"
        ],
    )
