"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, fmtInt, type Stats } from "@/lib/api";

/**
 * The overview.
 *
 * There used to be a scroll-driven exploded diagram here. It was cut: it did
 * not expand cleanly at every viewport, and a diagram that misbehaves while
 * explaining a system undermines the system. What replaced it is the pipeline
 * as a static, readable row — the same information, legible at a glance, and
 * correct at every width.
 */
export default function Home() {
  const [stats, setStats] = useState<Stats | null>(null);
  useEffect(() => {
    api.stats().then(setStats).catch(() => {});
  }, []);

  return (
    <div>
      {/* ── hero ─────────────────────────────────────────────────────────── */}
      <section className="border-b" style={{ borderColor: "var(--line)" }}>
        <div className="mx-auto max-w-5xl px-6 pb-16 pt-20">
          <p className="label mb-5">Superjoin · engineering intern assignment</p>

          <h1 className="m-0 max-w-3xl text-[38px] font-semibold leading-[1.1] sm:text-[48px]">
            A fact is not a sentence.
            <br />
            <span style={{ color: "var(--ink-faint)" }}>
              It is a typed tuple with a scope.
            </span>
          </h1>

          <p className="mt-6 max-w-2xl text-[15px] leading-relaxed" style={{ color: "var(--ink-dim)" }}>
            Once a claim carries its own period, basis, segment, geography,
            accounting standard and modality, the three relationships this
            assignment asks for stop being an opinion and start being derivable.
            Two figures that disagree are a{" "}
            <em style={{ color: "var(--contradict)", fontStyle: "normal" }}>contradiction</em>{" "}
            only when every scope axis matches. If exactly one differs, that axis{" "}
            <em style={{ color: "var(--reconcile)", fontStyle: "normal" }}>is</em> the
            explanation.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-2.5">
            <Link href="/upload" className="btn btn-primary">
              Upload a PDF
            </Link>
            <Link href="/case/1" className="btn btn-quiet">
              See the four cases
            </Link>
            <Link href="/explorer" className="btn btn-quiet">
              Browse the evidence
            </Link>
          </div>

          {/* Live counters, not marketing numbers. */}
          <div className="mt-12 flex flex-wrap gap-x-10 gap-y-5 border-t pt-6" style={{ borderColor: "var(--line)" }}>
            {[
              ["grounded claims", fmtInt(stats?.claims)],
              ["classified pairs", fmtInt(stats?.edges)],
              [
                "grounding pass rate",
                stats ? `${(stats.grounding_pass_rate * 100).toFixed(1)}%` : "—",
              ],
              ["predicates grown", fmtInt(stats?.predicates)],
              ["cost to build", "₹0"],
            ].map(([label, value]) => (
              <div key={label}>
                <div className="num text-[22px] font-medium">{value}</div>
                <div className="label mt-0.5">{label}</div>
              </div>
            ))}
          </div>

          {stats?.ingest_in_progress && (
            <p className="mt-4 text-[12px]" style={{ color: "var(--ink-faint)" }}>
              Extraction is still running — these figures are rising as pages land.
            </p>
          )}
        </div>
      </section>

      {/* ── the pipeline, stated plainly ──────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-6 py-16">
        <p className="label mb-6">how a page becomes a comparable fact</p>
        <div className="grid gap-px overflow-hidden rounded-lg border md:grid-cols-4" style={{ borderColor: "var(--line)", background: "var(--line)" }}>
          {[
            ["Parse", "PyMuPDF, with one invariant asserted on every block: the page text at a claim's offsets reproduces its quote exactly. That is what makes a highlight land on the right words."],
            ["Spot", "A regex sweep finds every numeral, date, percentage and amount. Exhaustive by construction, which turns it into a recall denominator that needs no labels."],
            ["Extract", "The model is handed a candidate already located, with the occurrence marked in place, and asked only what it means. It never writes a number, a quote, a page or an offset."],
            ["Ground", "A hard gate. If the value is not literally inside the span it cites, the claim is quarantined and never enters the graph. No low-confidence escape hatch."],
            ["Normalise", "Units, scales, currencies and fiscal periods to canonical form. ₹7,225 crore and 72,251 million are one number written two ways."],
            ["Canonicalise", "An ontology grown at runtime. There is no metric enum and no company list anywhere in the codebase."],
            ["Pair", "Blocked candidate generation on subject, predicate and value — never all-pairs, which would be quadratic and unauditable."],
            ["Compare", "A pure function over two claims. Same scope and same value corroborates; one differing axis reconciles, and that axis is the reason."],
          ].map(([title, body], i) => (
            <div key={title} className="p-5" style={{ background: "var(--bg-raised)" }}>
              <div className="num mb-2 text-[11px]" style={{ color: "var(--accent)" }}>
                {String(i + 1).padStart(2, "0")}
              </div>
              <h3 className="m-0 text-[14px] font-semibold">{title}</h3>
              <p className="mt-2 mb-0 text-[12.5px] leading-relaxed" style={{ color: "var(--ink-faint)" }}>
                {body}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* ── what it is asked to demonstrate ───────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-6 pb-24">
        <p className="label mb-6">the four required cases</p>
        <div className="grid gap-px overflow-hidden rounded-lg border sm:grid-cols-2" style={{ borderColor: "var(--line)", background: "var(--line)" }}>
          {[
            {
              n: 1,
              t: "Corroborated across documents",
              d: "FY24 EBITDA stated as 1,266 in millions in the annual report and 127 in crore on an earnings tile. One value, two dialects, and the normaliser is what sees it.",
              c: "var(--corroborate)",
            },
            {
              n: 2,
              t: "A genuine contradiction",
              d: "Empty, and that is the result. No cross-document contradiction in this corpus survives verification — the strongest candidates all trace to a period or row label on the wrong figure.",
              c: "var(--contradict)",
            },
            {
              n: 3,
              t: "Explained by context",
              d: "Cross-border revenue at 10.70% consolidated in the prospectus against 1.87% standalone in the annual report. Same period, differing basis, and the axis is the whole explanation.",
              c: "var(--reconcile)",
            },
            {
              n: 4,
              t: "A failure, measured",
              d: "91.3% of contradictions were a prior-year column inheriting the current period. Verified by hand, counted, and fixed — reported with the same machinery as the successes.",
              c: "var(--ambiguous)",
            },
          ].map((c) => (
            <Link
              key={c.n}
              href={`/case/${c.n}`}
              className="block p-5 no-underline transition-colors hover:brightness-125"
              style={{ background: "var(--bg-raised)" }}
            >
              <div className="num mb-2 text-[11px]" style={{ color: c.c }}>
                {String(c.n).padStart(2, "0")}
              </div>
              <h3 className="m-0 text-[15px] font-semibold">{c.t}</h3>
              <p className="mt-2 mb-0 text-[12.5px] leading-relaxed" style={{ color: "var(--ink-faint)" }}>
                {c.d}
              </p>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
