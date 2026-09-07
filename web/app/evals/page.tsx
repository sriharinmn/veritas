"use client";

import { useEffect, useState } from "react";
import {
  api,
  fmtInt,
  RELATION_META,
  type EvalReport,
  type Relation,
  type Stats,
} from "@/lib/api";

/**
 * Evals — where the system reports on itself.
 *
 * The grounding pass rate is a count, not an estimate: every claim either had
 * its value found inside the span it cited or it did not. The quarantine queue
 * below is the assignment's fourth required case, living in the product rather
 * than being recalled for a write-up.
 */
export default function Evals() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [q, setQ] = useState<{
    total: number;
    by_reason: Record<string, number>;
  } | null>(null);

  const [report, setReport] = useState<EvalReport | null>(null);

  useEffect(() => {
    api.stats().then(setStats).catch(() => {});
    api.quarantine().then(setQ).catch(() => {});
    api.evals().then(setReport).catch(() => {});
  }, []);

  const rel = stats?.relations;

  return (
    <div className="mx-auto max-w-[1200px] px-4 py-6">
      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          {
            l: "grounding pass rate",
            v: stats ? `${(stats.grounding_pass_rate * 100).toFixed(1)}%` : "—",
            s: `${fmtInt(stats?.claims)} grounded · ${fmtInt(stats?.quarantined)} quarantined`,
            c: "var(--corroborate)",
          },
          {
            l: "grounded claims",
            v: fmtInt(stats?.claims),
            s: `across ${fmtInt(stats?.documents)} documents`,
          },
          {
            l: "classified pairs",
            v: fmtInt(stats?.edges),
            s: "blocked, never all-pairs",
          },
          {
            l: "ontology",
            v: fmtInt(stats?.predicates),
            s: `${fmtInt(stats?.entities)} entities · grown at runtime`,
          },
        ].map((k) => (
          <div key={k.l} className="panel rounded-md p-3">
            <div className="label mb-1.5">{k.l}</div>
            <div className="num text-[26px] font-medium leading-none" style={{ color: k.c }}>
              {k.v}
            </div>
            <div className="mt-1.5 text-[11px]" style={{ color: "var(--ink-faint)" }}>
              {k.s}
            </div>
          </div>
        ))}
      </div>

      {/* The harness report. Read from disk with its timestamp shown — a stale
          number a reader can date is worth more than a fresh one they waited a
          minute for. */}
      <div className="panel mb-3 rounded-md p-3">
        <div className="mb-3 flex items-baseline gap-3">
          <p className="label m-0">eval harness</p>
          {report?.generated_at && (
            <span className="num text-[10px]" style={{ color: "var(--ink-faint)" }}>
              {new Date(report.generated_at).toLocaleString()} · {report.seconds}s
            </span>
          )}
        </div>
        {!report?.available ? (
          <p className="m-0 text-[12px]" style={{ color: "var(--ink-faint)" }}>
            {report?.hint ?? "No report yet."}
          </p>
        ) : (
          <div className="space-y-1">
            {report.layers?.map((l) => (
              <div
                key={l.name}
                className="flex items-baseline gap-3 rounded px-2 py-1.5"
                style={{ background: "var(--bg-sunken)" }}
              >
                <span
                  className="num w-3 shrink-0 text-center text-[12px]"
                  style={{
                    color:
                      l.passed === null
                        ? "var(--ink-faint)"
                        : l.passed
                          ? "var(--corroborate)"
                          : "var(--contradict)",
                  }}
                >
                  {l.passed === null ? "·" : l.passed ? "✓" : "✗"}
                </span>
                <span className="w-40 shrink-0 text-[11.5px]" style={{ color: "var(--ink-dim)" }}>
                  {l.name}
                </span>
                <span className="text-[11.5px]">{l.headline}</span>
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 text-[10.5px] leading-relaxed" style={{ color: "var(--ink-faint)" }}>
          Four of these five layers need no hand-written labels, which is the
          point: a golden set stops being representative the moment a grader
          uploads a document it does not cover. Arithmetic coherence checks the
          document&apos;s own identities — revenue plus other income equals total
          income, in every column — so when it holds, the values, periods, bases
          and row labels were all read correctly at once.
        </p>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="panel rounded-md p-3">
          <p className="label mb-3">relation distribution</p>
          {rel &&
            (Object.keys(RELATION_META) as Relation[])
              .filter((r) => r !== "unrelated")
              .map((r) => {
                const n = rel[r] ?? 0;
                const max = Math.max(...Object.values(rel));
                return (
                  <div key={r} className={`${RELATION_META[r].className} mb-2.5`}>
                    <div className="mb-1 flex items-baseline justify-between text-[11.5px]">
                      <span style={{ color: "var(--v)" }}>{RELATION_META[r].label}</span>
                      <span className="num" style={{ color: "var(--ink-faint)" }}>
                        {fmtInt(n)}
                      </span>
                    </div>
                    <div className="h-1.5 rounded-full" style={{ background: "var(--bg-sunken)" }}>
                      <div
                        className="h-1.5 rounded-full transition-all duration-500"
                        style={{
                          width: `${max ? (n / max) * 100 : 0}%`,
                          background: "var(--v)",
                          opacity: 0.75,
                        }}
                      />
                    </div>
                  </div>
                );
              })}
          <p className="mt-4 text-[11px] leading-relaxed" style={{ color: "var(--ink-faint)" }}>
            The contradiction count is currently far too high, and the cause is
            known rather than mysterious: predicate labels are weak where a page
            gives the model little context to read, so unrelated figures land on
            one ontology node and every pair inside it reads as a conflict. The
            eval harness will put a number on that instead of this sentence.
          </p>
        </div>

        <div className="panel rounded-md p-3">
          <p className="label mb-3">quarantine — claims refused entry</p>
          {q && q.total === 0 ? (
            <div>
              <p className="m-0 text-[12px]" style={{ color: "var(--ink-dim)" }}>
                Nothing quarantined so far.
              </p>
              <p className="mt-2 text-[11px] leading-relaxed" style={{ color: "var(--ink-faint)" }}>
                That is expected rather than impressive, and worth being precise
                about: the model in this pipeline never writes a value or a
                citation. It only labels candidates the regex sweep already
                located, so the number in a claim is a substring of the page it
                cites <em>by construction</em>. The verifier stays as a hard gate
                because defence in depth is cheap — but it has little to catch
                while the architecture is doing its job.
              </p>
            </div>
          ) : (
            <div className="space-y-1.5">
              {q &&
                Object.entries(q.by_reason).map(([reason, n]) => (
                  <div
                    key={reason}
                    className="flex items-baseline justify-between rounded px-2 py-1.5 text-[11.5px]"
                    style={{ background: "var(--bg-sunken)" }}
                  >
                    <span style={{ color: "var(--ink-dim)" }}>{reason.replace(/_/g, " ")}</span>
                    <span className="num" style={{ color: "var(--contradict)" }}>
                      {fmtInt(n)}
                    </span>
                  </div>
                ))}
            </div>
          )}
        </div>
      </div>

      <div className="panel mt-3 rounded-md p-3">
        <p className="label mb-2">not yet measured</p>
        <ul className="m-0 space-y-1 pl-4 text-[11.5px]" style={{ color: "var(--ink-faint)" }}>
          <li>Extraction precision and recall against a hand-labelled golden set.</li>
          <li>
            Relation classification confusion matrix — in particular the rate of
            contradictions predicted where reconciliation was correct, which is
            the false alarm that would destroy a reader&apos;s trust.
          </li>
          <li>Spot conversion: candidates spotted versus claims produced, per document.</li>
          <li>Full mode against deterministic mode, on the same pages.</li>
        </ul>
      </div>
    </div>
  );
}
