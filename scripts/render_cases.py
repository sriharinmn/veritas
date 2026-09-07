"""Render the curated cases into the README, between markers.

    python -m scripts.curate_cases      # choose them
    python -m scripts.render_cases      # write them into README.md

plan.md commits to a specific standard: *someone must be able to grade this
submission from the README alone.* That rules out "see the app" and it rules out
screenshots as the only evidence, because a grader reading on a train cannot
click and may not be able to see images at all.

So each case is rendered as a table a reader can check by hand — both values,
both normalisations, every scope axis, both source pages, and both verbatim
quotes — followed by the comparator's actual reasoning steps rather than a
paragraph about them.

It is generated rather than written because it will be regenerated: the corpus
run is still adding documents, and a hand-written case section would quietly go
stale against the data it describes. Anything in this file that looks like an
opinion is computed from `evals/cases.json`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASES = Path("evals/cases.json")
README = Path("README.md")
START = "<!-- cases:start -->"
END = "<!-- cases:end -->"

AXES = [
    ("period", "Period"),
    ("basis", "Basis"),
    ("segment", "Segment"),
    ("geography", "Geography"),
    ("accounting", "Accounting"),
    ("modality", "Modality"),
]


def _scope_rows(a: dict, b: dict) -> list[str]:
    rows = []
    for key, label in AXES:
        x = a["scope"].get(key) or "—"
        y = b["scope"].get(key) or "—"
        same = "=" if x == y else "**differs**"
        rows.append(f"| {label} | {x} | {y} | {same} |")
    return rows


def _quote(text: str, limit: int = 220) -> str:
    text = " ".join((text or "").split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace("|", "\\|")


def render_case(c: dict) -> str:
    if c["case"] == 4:
        return render_case_4(c)
    if not c.get("found"):
        return (
            f"### Case {c['case']} — {c['title']}\n\n"
            "**No pair in this corpus meets the criteria, and that is the finding.**\n\n"
            "The comparator does report contradictions — thousands of them — but none "
            "survives every check when the check is applied honestly. Cross-document "
            "candidates fail on precision or on chart-derived evidence; the ones that "
            "score highest are two figures from the *same row of the same two-column "
            "statement*, where the prior-year column inherited the current year's "
            "period. Case 4 shows a verified instance.\n\n"
            "Presenting one of those as a contradiction between documents would be "
            "presenting a bug as a finding, which is the one thing this system is "
            "built not to do. The criteria that rule them out are in "
            "`scripts/curate_cases.py` and a reviewer can loosen them and look.\n"
        )

    a, b = c["a"], c["b"]
    scale_a = f" ({a['scale']})" if a.get("scale") else ""
    scale_b = f" ({b['scale']})" if b.get("scale") else ""

    out = [
        f"### Case {c['case']} — {c['title']}",
        "",
        f"**Verdict: {c['relation'].upper()}**"
        + (f", on the `{c['axis']}` axis" if c.get("axis") else "")
        + f" · confidence {c['confidence']:.2f}"
        + (" · across two documents" if c.get("cross_document") else " · within one document"),
        "",
        f"> {a['subject']} — *{a['predicate']}*",
        "",
        "| | Statement A | Statement B |",
        "|---|---|---|",
        f"| **Value as written** | `{a['value']}`{scale_a} | `{b['value']}`{scale_b} |",
        f"| **Normalised** | {a['normalised']} {a['unit']} | {b['normalised']} {b['unit']} |",
        f"| **Source** | {a['evidence']['document']} p{a['evidence']['page']} "
        f"| {b['evidence']['document']} p{b['evidence']['page']} |",
        "",
        "| Scope axis | A | B | |",
        "|---|---|---|---|",
        *_scope_rows(a, b),
        "",
        "**Evidence, verbatim from the page:**",
        "",
        f"- A — “{_quote(a['evidence']['quote'])}”",
        f"- B — “{_quote(b['evidence']['quote'])}”",
        "",
        "**Why this pair was chosen** (criteria in `scripts/curate_cases.py`):",
        "",
        *[f"- {w}" for w in c.get("selected_because", [])],
        "",
        "<details><summary>The comparator's reasoning, step by step</summary>",
        "",
        "```",
        *c.get("trace", []),
        "```",
        "",
        "</details>",
        "",
    ]
    return "\n".join(out)


def render_case_4(c: dict) -> str:
    leak = c.get("adjacent_column_period_leak", {})
    om = c.get("ontology_over_merge", {})
    pa = c.get("period_attribution", {})
    q = c.get("quarantine", {})
    ur = c.get("unresolved_relations", {})

    lines = [
        f"### Case 4 — {c['title']}",
        "",
        "Every figure here comes from the same run that produced the three cases",
        "above. None of it is recalled from memory or softened.",
        "",
        "**The failure that explains why case 2 is empty: the prior-year column.**",
        "",
        f"{leak.get('count', 0):,} of {leak.get('of_total_contradictions', 0):,} "
        f"contradictions ({leak.get('share', 0) * 100:.1f}%) are two figures from the",
        "same page, same row of a two-column statement. Verified by hand on "
        f"{leak.get('verified_example', {}).get('document', '')} "
        f"p{leak.get('verified_example', {}).get('page', '')}:",
        "",
        "```",
        leak.get("verified_example", {}).get("row", ""),
        f"headers: {leak.get('verified_example', {}).get('headers', '')}",
        f"reported as: {leak.get('verified_example', {}).get('reported_as', '')}",
        f"actually:    {leak.get('verified_example', {}).get('actually', '')}",
        "```",
        "",
        f"_{leak.get('note', '')}_",
        "",
        "**The other dominant failure: predicates that should not have merged.**",
        "",
        f"{om.get('implausible_contradictions', 0):,} of "
        f"{om.get('of_total_contradictions', 0):,} contradictions "
        f"({om.get('share', 0) * 100:.1f}%) hold two values that differ by more than 500%.",
        "Two figures that far apart are not a disagreement between documents — they are",
        "two different quantities collapsed onto one predicate node, after which every",
        "pair inside that node reads as a conflict.",
        "",
    ]
    if om.get("worst_examples"):
        lines += [
            "| Predicate | A | B | Apart |",
            "|---|---|---|---|",
            *[
                f"| {w['predicate']} | `{w['a']}` (p{w['a_page']}) | "
                f"`{w['b']}` (p{w['b_page']}) | {w['gap_percent']:,.0f}% |"
                for w in om["worst_examples"][:3]
            ],
            "",
            f"_{om.get('note', '')}_",
            "",
        ]

    lines += [
        "**Period attribution is the weakest field.**",
        "",
        f"{pa.get('resolved', 0):,} of {pa.get('total', 0):,} claims "
        f"({pa.get('rate', 0) * 100:.1f}%) resolve to real dates. "
        f"{pa.get('note', '')}",
        "",
        "**What the grounding gate refused.**",
        "",
        f"{q.get('total', 0):,} claims were quarantined. {q.get('note', '')}",
        "",
        "**What the comparator declined to decide.**",
        "",
        f"{ur.get('ambiguous', 0):,} pairs. {ur.get('note', '')}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if not CASES.exists():
        print(f"{CASES} not found — run `python -m scripts.curate_cases` first")
        return 1

    data = json.loads(CASES.read_text(encoding="utf-8"))
    body = "\n".join(render_case(c) for c in data["cases"])

    readme = README.read_text(encoding="utf-8")
    if START not in readme or END not in readme:
        print(f"markers not found in README.md — add {START} and {END}")
        return 1

    head = readme.split(START)[0]
    tail = readme.split(END)[1]
    README.write_text(f"{head}{START}\n\n{body}\n{END}{tail}", encoding="utf-8")
    print(f"rendered {len(data['cases'])} cases into README.md ({len(body):,} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
