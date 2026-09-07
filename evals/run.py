"""The eval harness — where the system reports on itself.

    python -m evals.run              # print the table, write a report
    python -m evals.run --quiet      # report only

Five layers, cheapest first. Four of them need no hand-written labels at all,
which matters more than it sounds: a golden set of eighty items is expensive to
build and stops being representative the moment a grader uploads a document it
does not cover. These four keep working on documents nobody has ever labelled.

  1 normalisation      pure functions, hundreds of cases, no LLM
  2 grounding          every claim's value found inside the span it cites
  3 spot conversion    the recall denominator: what the sweep found vs. what
                       survived, with the silently-dropped bucket named
  4 arithmetic         totals that must equal their parts — a precision signal
                       taken from the document's own internal consistency
  5 determinism        the same inputs must produce the same output

Layer 4 is the interesting one. A financial statement carries its own answer
key: revenue plus other income equals total income, and it does so in every
column. If the extracted claims reproduce that identity, the values, the
periods, the bases and the row labels were all read correctly — verified
against the document's arithmetic rather than against an opinion. It is not a
substitute for labelled precision, but it is the only precision signal here
that costs nothing and generalises to documents nobody has seen.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from itertools import combinations
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.extract.spot import spot_document
from core.models import Claim, Relation
from core.parse.pdf import parse_pdf
from core.store.checkpoints import CORPUS_DIR, build, load_claims

REPORTS = Path("evals/reports")

# Two figures agree arithmetically if they are within a tenth of a percent —
# these documents are stated to two decimals and rounding is not disagreement.
ARITH_TOL = Decimal("0.001")

TOTAL_WORDS = ("total", "aggregate", "sum of")


@dataclass
class Layer:
    name: str
    headline: str
    detail: dict = field(default_factory=dict)
    passed: bool | None = None


# ── 1 · normalisation ────────────────────────────────────────────────────────


def normalisation() -> Layer:
    """The pure-function suite. Fast enough to run every time."""
    t = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
    )
    blob = proc.stdout + "\n" + proc.stderr
    passed = proc.returncode == 0

    # Count the progress characters rather than parsing the summary line. Under
    # pytest 9 that final "N passed" line does not reach captured stdout, and
    # depending on its wording made this layer silently report "0 unit tests
    # green" — a metric quietly reading zero is worse than one that errors,
    # because it looks like a fact.
    outcomes = {".": 0, "F": 0, "E": 0, "s": 0, "x": 0}
    for line in blob.splitlines():
        if not re.search(r"\[\s*\d+%\]\s*$", line):
            continue
        for ch in line.split("[")[0].strip():
            if ch in outcomes:
                outcomes[ch] += 1

    count = sum(outcomes.values())
    failed = outcomes["F"] + outcomes["E"]
    tail = [l for l in blob.splitlines() if "passed" in l or "failed" in l]
    summary = tail[-1].strip() if tail else f"{count} collected, {failed} failing"
    verdict = "green" if passed else f"{failed} FAILING"
    return Layer(
        "normalisation",
        f"{count} unit tests {verdict}",
        {
            "tests": count,
            "failed": failed,
            "summary": summary,
            "seconds": round(time.perf_counter() - t, 1),
        },
        passed,
    )


# ── 2 · grounding ────────────────────────────────────────────────────────────


def grounding(claims: list[Claim], quarantined: list[dict]) -> Layer:
    """A count, not an estimate: the value was in the cited span or it was not."""
    total = len(claims) + len(quarantined)
    rate = len(claims) / total if total else 0.0
    by_reason: dict[str, int] = defaultdict(int)
    for q in quarantined:
        by_reason[str(q.get("reason", "unknown"))] += 1

    inherited = sum(1 for c in claims if c.scale_inferred)
    return Layer(
        "grounding",
        f"{rate:.1%} pass rate ({len(claims):,} grounded, {len(quarantined):,} quarantined)",
        {
            "grounded": len(claims),
            "quarantined": len(quarantined),
            "pass_rate": round(rate, 4),
            "by_reason": dict(by_reason),
            "scale_inferred": inherited,
            "scale_inferred_pct": round(inherited / len(claims), 4) if claims else 0.0,
        },
        rate >= 0.95,
    )


# ── 3 · spot conversion ──────────────────────────────────────────────────────


def spot_conversion(claims: list[Claim]) -> Layer:
    """The denominator no label can give you.

    The regex sweep is exhaustive over numerals by construction, so every number
    in a document is accounted for exactly once. Only pages the run actually
    processed are counted — measuring conversion against pages nobody extracted
    would report the page budget as a recall failure.
    """
    claimed_pages: dict[str, set[int]] = defaultdict(set)
    claims_per_doc: dict[str, int] = defaultdict(int)
    for c in claims:
        if not c.evidence:
            continue
        doc = _document_of(c)
        claimed_pages[doc].add(c.evidence[0].page)
        claims_per_doc[doc] += 1

    per_doc = {}
    spotted_total = 0
    claimed_total = 0
    for path in sorted(Path("seed").glob("*/*.pdf")):
        name = path.name
        pages = claimed_pages.get(name)
        if not pages:
            continue
        doc = parse_pdf(path, detect_tables=False)
        spots = spot_document(doc)
        on_processed = sum(
            p.density for p in spots.pages if p.page in pages
        )
        got = claims_per_doc[name]
        spotted_total += on_processed
        claimed_total += got
        per_doc[name] = {
            "pages_processed": len(pages),
            "candidates_on_those_pages": on_processed,
            "claims": got,
            "conversion": round(got / on_processed, 4) if on_processed else 0.0,
        }

    conv = claimed_total / spotted_total if spotted_total else 0.0
    return Layer(
        "spot conversion",
        f"{conv:.1%} of signal candidates became claims "
        f"({claimed_total:,} of {spotted_total:,})",
        {
            "candidates": spotted_total,
            "claims": claimed_total,
            "conversion": round(conv, 4),
            "not_converted": spotted_total - claimed_total,
            "per_document": per_doc,
            "note": (
                "Not-converted covers both correct rejections (identifiers, "
                "regulation numbers, dates) and silent drops. Separating the two "
                "needs the model to report its rejections, which is the next "
                "thing worth building here."
            ),
        },
        None,
    )


DOC_BY_ID: dict[str, str] = {}


def _document_of(c: Claim) -> str:
    if not c.evidence:
        return ""
    return DOC_BY_ID.get(str(c.evidence[0].document_id), "")


# ── 4 · arithmetic coherence ─────────────────────────────────────────────────


def arithmetic(claims: list[Claim]) -> Layer:
    """Check the document's own identities against what we extracted.

    Within one subject, period and basis, a claim whose predicate names a total
    should equal the sum of other claims in that same scope. When it does, the
    values, the period attribution, the basis attribution and the row labels
    were all read correctly at once — a precision signal with no labelling, and
    one that keeps working on a document nobody has seen.
    """
    groups: dict[tuple, list[Claim]] = defaultdict(list)
    for c in claims:
        if c.value.canonical_magnitude is None:
            continue
        key = (
            c.subject_id or c.subject_raw,
            c.scope.period.start or c.scope.period.label,
            c.scope.period.end,
            c.scope.basis.value,
            _document_of(c),
            c.evidence[0].page if c.evidence else None,
        )
        groups[key].append(c)

    checked = 0
    satisfied = 0
    examples: list[dict] = []

    for key, members in groups.items():
        if not (2 < len(members) <= 60):
            continue
        totals = [m for m in members if any(w in m.predicate_raw.lower() for w in TOTAL_WORDS)]
        parts = [m for m in members if m not in totals]
        if not totals or len(parts) < 2:
            continue

        for t in totals:
            target = t.value.canonical_magnitude
            if target is None or target == 0:
                continue
            checked += 1
            hit = None
            for a, b in combinations(parts, 2):
                if a.value.canonical_magnitude is None or b.value.canonical_magnitude is None:
                    continue
                s = a.value.canonical_magnitude + b.value.canonical_magnitude
                if abs(s - target) / abs(target) <= ARITH_TOL:
                    hit = (a, b)
                    break
            if hit:
                satisfied += 1
                if len(examples) < 8:
                    examples.append(
                        {
                            "period": t.scope.period.label,
                            "basis": t.scope.basis.value,
                            "total": f"{t.predicate_raw} = {t.value.raw}",
                            "parts": [
                                f"{hit[0].predicate_raw} = {hit[0].value.raw}",
                                f"{hit[1].predicate_raw} = {hit[1].value.raw}",
                            ],
                        }
                    )

    rate = satisfied / checked if checked else 0.0
    return Layer(
        "arithmetic coherence",
        f"{satisfied}/{checked} total-lines reproduce their parts ({rate:.1%})"
        if checked
        else "no total-lines found to check",
        {
            "checked": checked,
            "satisfied": satisfied,
            "rate": round(rate, 4),
            "examples": examples,
            "note": (
                "Only pairwise sums are attempted, so a total made of three or "
                "more parts is counted as unsatisfied. The figure is therefore a "
                "floor, not an accuracy score."
            ),
        },
        None,
    )


# ── 5 · determinism ──────────────────────────────────────────────────────────


def _checkpoint_signature() -> tuple:
    return tuple(
        sorted((p.name, p.stat().st_size) for p in CORPUS_DIR.glob("*.jsonl"))
    ) if CORPUS_DIR.exists() else ()


def determinism() -> Layer:
    """The same checkpoints must produce the same knowledge layer, twice.

    The inputs are checked either side of the comparison, because the corpus run
    appends while this runs. The first version of this reported DIVERGED simply
    because 101 more claims had landed between the two builds — a moving input,
    not non-determinism, and calling it a failure would have sent somebody
    hunting a bug that was not there. An eval that cries wolf is worse than no
    eval, because it teaches the reader to ignore a red mark.
    """
    before = _checkpoint_signature()
    a = build()
    b = build()
    after = _checkpoint_signature()

    if before != after:
        return Layer(
            "determinism",
            "inconclusive — checkpoints changed mid-check (ingest is running)",
            {"claims": [len(a.claims), len(b.claims)], "inputs_stable": False},
            None,
        )

    ok = (
        len(a.claims) == len(b.claims)
        and len(a.edges) == len(b.edges)
        and a.counts() == b.counts()
    )
    return Layer(
        "determinism",
        "identical across two builds" if ok else "DIVERGED between builds",
        {
            "claims": [len(a.claims), len(b.claims)],
            "edges": [len(a.edges), len(b.edges)],
            "inputs_stable": True,
        },
        ok,
    )


# ── report ───────────────────────────────────────────────────────────────────


def main(quiet: bool = False) -> int:
    started = time.perf_counter()
    claims, quarantined, docs = load_claims(CORPUS_DIR)

    DOC_BY_ID.clear()
    DOC_BY_ID.update({d.id: d.filename for d in docs})

    layer_obj = build()
    layers = [
        normalisation(),
        grounding(layer_obj.claims, quarantined),
        spot_conversion(layer_obj.claims),
        arithmetic(layer_obj.claims),
        determinism(),
    ]

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "documents": [
            {"filename": d.filename, "pages_processed": d.pages_processed, "claims": d.claims}
            for d in docs
        ],
        "totals": layer_obj.stats(),
        "layers": [asdict(x) for x in layers],
        "seconds": round(time.perf_counter() - started, 1),
    }

    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = REPORTS / f"{stamp}.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (REPORTS / "latest.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )

    if not quiet:
        print()
        print(f"  eval report · {len(docs)} documents · {len(claims):,} claims")
        print("  " + "─" * 70)
        for x in layers:
            mark = "·" if x.passed is None else ("✓" if x.passed else "✗")
            print(f"  {mark} {x.name:22} {x.headline}")
        print("  " + "─" * 70)
        rel = layer_obj.counts()
        print(
            "    relations   "
            + "  ".join(
                f"{r.value}={rel.get(r.value, 0):,}" for r in Relation if r is not Relation.UNRELATED
            )
        )
        print(f"\n  written to {out}\n")

    failed = [x for x in layers if x.passed is False]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(quiet="--quiet" in sys.argv))
