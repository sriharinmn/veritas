"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Define } from "@/components/Define";
import { api, fmtInt, type Stats } from "@/lib/api";

/**
 * The overview.
 *
 * The hero is not a slogan over a gradient — it is the thing this product
 * actually does, shown at full size: one figure written two ways in two
 * different filings, and the machine's finding that they are the same fact.
 * A reader understands the whole system from that one row, and everything
 * below it is elaboration.
 */
export default function Home() {
  const [stats, setStats] = useState<Stats | null>(null);
  useEffect(() => {
    api.stats().then(setStats).catch(() => {});
  }, []);

  return (
    <div>
      <section className="mx-auto max-w-[1180px] px-6 pb-14 pt-16">
        <h1 className="max-w-[19ch] text-[2.6rem] leading-[1.12] sm:text-[3.1rem]">
          Two filings. One figure. Written two different ways.
        </h1>

        <p className="mt-5 text-[1.05rem]" style={{ color: "var(--ink-soft)" }}>
          Veritas reads financial documents, pins every fact to the exact words
          on the page it came from, and works out whether facts from different
          documents agree, disagree, or only look like they disagree.
        </p>

        {/* The worked example, at full size. This is the hero. */}
        <figure className="sheet mt-10 overflow-hidden">
          <div className="grid md:grid-cols-2">
            <div className="p-6 md:border-r" style={{ borderColor: "var(--rule)" }}>
              <p className="m-0 text-[13px]" style={{ color: "var(--ink-faint)" }}>
                Annual report FY24, page 6
              </p>
              <p className="fig m-0 mt-2 text-[2.4rem] leading-none">1,266</p>
              <p className="m-0 mt-2 text-[13.5px]" style={{ color: "var(--ink-soft)" }}>
                EBITDA, in millions
              </p>
            </div>
            <div className="border-t p-6 md:border-t-0" style={{ borderColor: "var(--rule)" }}>
              <p className="m-0 text-[13px]" style={{ color: "var(--ink-faint)" }}>
                Earnings deck, page 23
              </p>
              <p className="fig m-0 mt-2 text-[2.4rem] leading-none">127</p>
              <p className="m-0 mt-2 text-[13.5px]" style={{ color: "var(--ink-soft)" }}>
                EBITDA, in crore
              </p>
            </div>
          </div>
          <figcaption
            className="flex flex-wrap items-center gap-3 border-t px-6 py-4 text-[13.5px]"
            style={{ borderColor: "var(--rule)", background: "var(--paper-sunk)" }}
          >
            <span className="badge v-corroboration">Same fact</span>
            <span style={{ color: "var(--ink-soft)" }}>
              Both are ₹1.27 billion for the year ended 31 March 2024. Same period,
              same basis, same segment — so the figures corroborate rather than
              conflict.
            </span>
          </figcaption>
        </figure>

        <div className="mt-8 flex flex-wrap gap-2.5">
          <Link href="/case/1" className="btn btn-primary">
            See the four cases
          </Link>
          <Link href="/explorer" className="btn btn-quiet">
            Browse the facts
          </Link>
          <Link href="/upload" className="btn btn-quiet">
            Add your own document
          </Link>
        </div>
      </section>

      {/* Live figures, stated plainly. */}
      <section className="border-y" style={{ borderColor: "var(--rule)", background: "var(--paper-sunk)" }}>
        <dl className="mx-auto grid max-w-[1180px] grid-cols-2 gap-x-8 gap-y-6 px-6 py-8 sm:grid-cols-4">
          <Figure label="Facts extracted" value={fmtInt(stats?.claims)} />
          <Figure
            label="Grounded in the page"
            value={stats ? `${(stats.grounding_pass_rate * 100).toFixed(1)}%` : "—"}
            define="grounded"
          />
          <Figure label="Comparisons drawn" value={fmtInt(stats?.edges)} />
          <Figure label="Metric names learned" value={fmtInt(stats?.predicates)} define="ontology" />
        </dl>
      </section>

      {/* How it works, as a sequence — which this genuinely is. */}
      <section className="mx-auto max-w-[1180px] px-6 py-14">
        <h2 className="mb-1">How a page becomes a comparable fact</h2>
        <p className="mb-8 text-[14.5px]" style={{ color: "var(--ink-soft)" }}>
          Each step narrows what the next one is allowed to do. That is what
          makes the output checkable rather than merely plausible.
        </p>

        <ol className="m-0 list-none space-y-0 p-0">
          {STEPS.map((s, i) => (
            <li
              key={s.title}
              className="grid gap-x-6 gap-y-1 py-5 sm:grid-cols-[3rem_14rem_1fr]"
              style={{ borderTop: i ? "1px solid var(--rule)" : "none" }}
            >
              <span className="fig text-[14px]" style={{ color: "var(--ink-faint)" }}>
                {String(i + 1).padStart(2, "0")}
              </span>
              <h3 className="m-0 text-[15px]">{s.title}</h3>
              <p className="m-0 text-[14px]" style={{ color: "var(--ink-soft)" }}>
                {s.body}
              </p>
            </li>
          ))}
        </ol>
      </section>

      {/* The four cases. */}
      <section className="border-t" style={{ borderColor: "var(--rule)" }}>
        <div className="mx-auto max-w-[1180px] px-6 py-14">
          <h2 className="mb-1">The four cases</h2>
          <p className="mb-8 text-[14.5px]" style={{ color: "var(--ink-soft)" }}>
            Chosen by a scoring function over every pair the system produced, not
            picked by hand. The criteria are in the repository, so you can
            disagree with a criterion rather than with an example.
          </p>

          <div className="grid gap-5 sm:grid-cols-2">
            {CASES.map((c) => (
              <Link
                key={c.n}
                href={`/case/${c.n}`}
                className="sheet block p-5 no-underline transition-colors hover:bg-[var(--paper-sunk)]"
              >
                <span className={`badge ${c.klass}`}>{c.verdict}</span>
                <h3 className="mb-1.5 mt-3">{c.title}</h3>
                <p className="m-0 text-[14px]" style={{ color: "var(--ink-soft)" }}>
                  {c.body}
                </p>
              </Link>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

function Figure({
  label,
  value,
  define,
}: {
  label: string;
  value: string;
  define?: string;
}) {
  return (
    <div>
      <dd className="fig m-0 text-[1.75rem] leading-none">{value}</dd>
      <dt className="mt-1.5 text-[13.5px]" style={{ color: "var(--ink-soft)" }}>
        {label}
        {define && <Define term={define} />}
      </dt>
    </div>
  );
}

const STEPS = [
  {
    title: "Read the page",
    body: "Every fact keeps the exact character range it came from, so a highlight later lands on the right words rather than near them.",
  },
  {
    title: "Find every number",
    body: "A plain text search finds each numeral, date, percentage and amount. It misses nothing, which is what makes the next step measurable.",
  },
  {
    title: "Ask what it means",
    body: "The model is shown a number already located on the page and asked only what it means. It never writes a figure, so it cannot invent one.",
  },
  {
    title: "Check it is really there",
    body: "If the value is not literally inside the span it points at, the fact is set aside and never enters the graph. There is no partial credit.",
  },
  {
    title: "Put it in comparable terms",
    body: "Units, scales, currencies and financial years are converted to one form. ₹7,225 crore and 72,251 million become the same number.",
  },
  {
    title: "Compare",
    body: "Two facts with the same subject and metric are checked axis by axis. Same scope and same value agree; one differing axis explains the gap.",
  },
];

const CASES = [
  {
    n: 1,
    verdict: "Corroborates",
    klass: "v-corroboration",
    title: "The same fact, written differently",
    body: "FY24 EBITDA as 1,266 in millions in the annual report and 127 in crore on an earnings slide. One figure, two dialects.",
  },
  {
    n: 2,
    verdict: "None found",
    klass: "v-ambiguous",
    title: "A genuine contradiction",
    body: "No conflict between two documents survives checking. Four candidates were traced to the source page and all four were mistakes of ours, not of the filings.",
  },
  {
    n: 3,
    verdict: "Explained",
    klass: "v-reconciled",
    title: "A disagreement that context explains",
    body: "Cross-border revenue at 10.70% in one document and 1.87% in another. One counts subsidiaries and the other does not — that is the whole story.",
  },
  {
    n: 4,
    verdict: "Measured",
    klass: "v-contradiction",
    title: "Where it goes wrong",
    body: "91.3% of reported contradictions were one row of a two-column statement compared against itself. Found by hand, counted, fixed.",
  },
];
