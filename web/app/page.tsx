"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { PipelineExploded } from "@/components/PipelineExploded";
import { api, fmtInt, type Stats } from "@/lib/api";

/**
 * The overview. The one screen allowed to be cinematic, because its job is to
 * explain a system rather than to display a number — and because it is the
 * opening twenty seconds of the demo.
 */
export default function Home() {
  const [stats, setStats] = useState<Stats | null>(null);
  useEffect(() => {
    api.stats().then(setStats).catch(() => {});
  }, []);

  return (
    <div>
      {/* ── hero ─────────────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden">
        <div className="grid-bg pointer-events-none absolute inset-0 opacity-[0.35]" />
        <div
          className="pointer-events-none absolute inset-x-0 top-0 h-full"
          style={{
            background:
              "radial-gradient(60% 50% at 50% 0%, color-mix(in srgb, #5b9dd9 9%, transparent), transparent 70%)",
          }}
        />

        <div className="relative mx-auto max-w-5xl px-6 pb-24 pt-28">
          <motion.p
            className="label mb-5"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
          >
            Superjoin · engineering intern assignment
          </motion.p>

          <motion.h1
            className="m-0 max-w-3xl text-[40px] font-semibold leading-[1.08] tracking-[-0.025em] sm:text-[52px]"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.05 }}
          >
            A fact is not a sentence.
            <br />
            <span style={{ color: "var(--ink-faint)" }}>
              It is a typed tuple with a scope.
            </span>
          </motion.h1>

          <motion.p
            className="mt-6 max-w-2xl text-[14px] leading-relaxed"
            style={{ color: "var(--ink-dim)" }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.6, delay: 0.15 }}
          >
            Once a claim carries its own period, basis, segment, geography,
            accounting standard and modality, the three relationships this
            assignment asks for stop being an opinion and start being
            derivable. Two figures that disagree are a{" "}
            <em style={{ color: "var(--contradict)", fontStyle: "normal" }}>
              contradiction
            </em>{" "}
            only when every scope axis matches. If exactly one differs, that
            axis <em style={{ color: "var(--reconcile)", fontStyle: "normal" }}>is</em>{" "}
            the explanation.
          </motion.p>

          <motion.div
            className="mt-9 flex flex-wrap items-center gap-2"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.5, delay: 0.25 }}
          >
            <Link
              href="/reconciliation"
              className="rounded px-3.5 py-2 text-[12px] font-medium no-underline transition-opacity hover:opacity-85"
              style={{ background: "var(--ink)", color: "var(--bg)" }}
            >
              See the four cases →
            </Link>
            <Link
              href="/explorer"
              className="panel rounded px-3.5 py-2 text-[12px] no-underline transition-colors hover:brightness-125"
              style={{ color: "var(--ink-dim)" }}
            >
              Browse the evidence
            </Link>
          </motion.div>

          {/* Live counters, not marketing numbers — they move while the corpus
              run is still writing, and the strip says so. */}
          <motion.div
            className="mt-14 flex flex-wrap gap-x-10 gap-y-4 border-t pt-6"
            style={{ borderColor: "var(--line)" }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.5, delay: 0.35 }}
          >
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
                <div className="num text-[20px] font-medium tracking-tight">
                  {value}
                </div>
                <div className="label mt-0.5">{label}</div>
              </div>
            ))}
          </motion.div>

          {stats?.ingest_in_progress && (
            <p className="mt-4 text-[11px]" style={{ color: "var(--ink-faint)" }}>
              Extraction is still running — these figures are rising as pages land.
            </p>
          )}
        </div>
      </section>

      {/* ── the exploded pipeline ─────────────────────────────────────────── */}
      <PipelineExploded />

      {/* ── what it is asked to demonstrate ───────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-6 py-24">
        <p className="label mb-8">the four required cases</p>
        <div className="grid gap-px overflow-hidden rounded-md border sm:grid-cols-2" style={{ borderColor: "var(--line)", background: "var(--line)" }}>
          {[
            {
              n: "01",
              t: "Corroborated across documents",
              d: "The same fact stated in millions in an audited statement and in crore on a KPI tile, rounded differently. One value, two dialects.",
              c: "var(--corroborate)",
            },
            {
              n: "02",
              t: "A genuine contradiction",
              d: "Same entity, metric, period and basis — different numbers. The trace shows every axis that was checked and found identical, because ruling out reconciliation is the argument.",
              c: "var(--contradict)",
            },
            {
              n: "03",
              t: "Explained by context",
              d: "The IMF reports India on calendar years; the Economic Survey does not. Two institutions print different numbers for what looks like the same year and both are right.",
              c: "var(--reconcile)",
            },
            {
              n: "04",
              t: "A failure, measured",
              d: "Sourced from the quarantine queue and the eval confusion matrix rather than from memory. Real rates, per failure mode.",
              c: "var(--ambiguous)",
            },
          ].map((c) => (
            <div key={c.n} className="p-5" style={{ background: "var(--bg-raised)" }}>
              <div className="num mb-2 text-[10px]" style={{ color: c.c }}>
                {c.n}
              </div>
              <h3 className="m-0 text-[14px] font-medium">{c.t}</h3>
              <p className="mt-2 mb-0 text-[12px] leading-relaxed" style={{ color: "var(--ink-faint)" }}>
                {c.d}
              </p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
