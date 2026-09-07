"use client";

import { useEffect, useState } from "react";
import { api, fmtInt, type Ontology } from "@/lib/api";

/**
 * The ontology, and the decisions that grew it.
 *
 * There is no metric enum and no company list anywhere in this codebase, which
 * is easy to claim and hard to believe. This screen is the evidence: every node
 * here was created by a document, and every merge decision is shown with the
 * similarity that produced it and the reasoning behind it.
 *
 * The decisions marked "kept separate" are the interesting ones. When the
 * ontology is unsure and has no model to ask, it declines to merge — a wrong
 * merge invents relationships between metrics that were never comparable, and
 * a duplicate merely misses some. The costs are not symmetric.
 */
export default function OntologyPage() {
  const [kind, setKind] = useState<"predicate" | "entity">("predicate");
  const [data, setData] = useState<Ontology | null>(null);

  useEffect(() => {
    api.ontology(kind).then(setData).catch(() => {});
  }, [kind]);

  const review = data?.decisions.filter((d) => d.needs_review) ?? [];
  const merges = data?.decisions.filter((d) => d.action.includes("merge")) ?? [];

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-5">
      <div className="mb-4 flex items-center gap-2">
        {(["predicate", "entity"] as const).map((k) => (
          <button
            key={k}
            onClick={() => setKind(k)}
            className="rounded px-3 py-1.5 text-[12px] capitalize"
            style={{
              background: kind === k ? "var(--bg-raised)" : "transparent",
              border: `1px solid ${kind === k ? "var(--line-bright)" : "transparent"}`,
              color: kind === k ? "var(--ink)" : "var(--ink-faint)",
            }}
          >
            {k}s
          </button>
        ))}
        {data && (
          <div className="ml-4 flex gap-5">
            {[
              ["nodes", data.stats.nodes],
              ["labels seen", data.stats.labels_seen],
              ["aliases", data.stats.aliases],
              ["awaiting adjudication", review.length],
            ].map(([l, v]) => (
              <div key={l as string}>
                <span className="num text-[15px]">{fmtInt(v as number)}</span>{" "}
                <span className="label">{l as string}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="panel overflow-hidden rounded-md">
          <div className="border-b px-3 py-2" style={{ borderColor: "var(--line)" }}>
            <span className="label">nodes, by alias count</span>
          </div>
          <div className="max-h-[calc(100vh-220px)] overflow-auto">
            {data?.nodes.map((n) => (
              <div
                key={n.id}
                className="border-b px-3 py-2"
                style={{ borderColor: "var(--line)" }}
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-[12px]">{n.label}</span>
                  <span className="num text-[11px]" style={{ color: "var(--ink-faint)" }}>
                    {n.alias_count} alias{n.alias_count === 1 ? "" : "es"}
                  </span>
                </div>
                {n.alias_count > 1 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {n.aliases.slice(0, 6).map((a) => (
                      <span
                        key={a}
                        className="rounded px-1.5 py-0.5 text-[10px]"
                        style={{ background: "var(--bg-sunken)", color: "var(--ink-faint)" }}
                      >
                        {a}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-3">
          <div className="panel overflow-hidden rounded-md">
            <div className="border-b px-3 py-2" style={{ borderColor: "var(--line)" }}>
              <span className="label">
                kept separate — unsure, and no adjudicator available
              </span>
            </div>
            <div className="max-h-[46vh] overflow-auto">
              {review.length === 0 && (
                <p className="px-3 py-6 text-center text-[11px]" style={{ color: "var(--ink-faint)" }}>
                  Nothing pending.
                </p>
              )}
              {review.slice(0, 60).map((d, i) => (
                <div key={i} className="border-b px-3 py-2" style={{ borderColor: "var(--line)" }}>
                  <div className="flex items-baseline gap-2 text-[11.5px]">
                    <span>{d.query}</span>
                    <span className="num" style={{ color: "var(--reconcile)" }}>
                      {d.similarity.toFixed(2)}
                    </span>
                    <span style={{ color: "var(--ink-faint)" }}>vs</span>
                    <span style={{ color: "var(--ink-dim)" }}>{d.nearest}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="panel overflow-hidden rounded-md">
            <div className="border-b px-3 py-2" style={{ borderColor: "var(--line)" }}>
              <span className="label">merges</span>
            </div>
            <div className="max-h-[34vh] overflow-auto">
              {merges.length === 0 && (
                <p className="px-3 py-6 text-center text-[11px]" style={{ color: "var(--ink-faint)" }}>
                  No merges yet.
                </p>
              )}
              {merges.slice(0, 60).map((d, i) => (
                <div
                  key={i}
                  className="border-b px-3 py-2 text-[11.5px]"
                  style={{ borderColor: "var(--line)", color: "var(--ink-dim)" }}
                >
                  {d.describe}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
