"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { motion } from "framer-motion";
import { EvidencePane } from "@/components/EvidencePane";
import { ScopeChips } from "@/components/ScopeChips";
import { ScopePicker } from "@/components/ScopePicker";
import {
  RELATION_META,
  api,
  fmtInt,
  type DocumentSummary,
  type Edge,
  type Relation,
} from "@/lib/api";

const TABS: { key: Relation; caseNo: string }[] = [
  { key: "corroboration", caseNo: "case 01" },
  { key: "contradiction", caseNo: "case 02" },
  { key: "reconciled", caseNo: "case 03" },
  { key: "ambiguous", caseNo: "escalated" },
];

/**
 * Reconciliation — the screen the assignment is really asking for.
 *
 * Two claims, each with its own page of its own PDF and its own highlighted
 * span, and between them the reasoning that produced the verdict. Crucially the
 * trace is the *machine's* steps, not a paragraph of generated prose: a reader
 * can follow subject ≡, predicate ≡, which scope axes differ, and how far apart
 * the normalised values are. That is what "explained" has to mean in a product
 * somebody stakes their name on.
 */
export default function ReconciliationPage() {
  return (
    <Suspense fallback={null}>
      <Reconciliation />
    </Suspense>
  );
}

function Reconciliation() {
  const params = useSearchParams();
  const [relation, setRelation] = useState<Relation>("reconciled");
  // Which document's comparisons to show. A reader arriving from a finished
  // upload has exactly one question — what does *mine* agree and disagree with
  // — and before this there was no way to ask it: the answer existed, ordered
  // by confidence, somewhere inside a hundred thousand other pairs.
  const [docFilter, setDocFilter] = useState<string>(params.get("document") ?? "");
  const [entityFilter, setEntityFilter] = useState<string>(params.get("subject") ?? "");
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [crossOnly, setCrossOnly] = useState(true);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [i, setI] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.documents().then(setDocs).catch(() => {});
  }, []);

  useEffect(() => {
    // Debounced for the same reason the explorer's search is: the relation and
    // cross-document controls are next to each other and a reader trying two
    // of them fires a request for every intermediate state. The delay also
    // moves setLoading out of the effect body, where it costs a render pass.
    const t = setTimeout(() => {
      setLoading(true);
      api
        .relations({
          relation,
          cross_document: crossOnly ? true : undefined,
          document_id: docFilter || undefined,
          entity_id: entityFilter || undefined,
          limit: 40,
        })
        .then((r) => {
          setEdges(r.items);
          setCounts(r.counts);
          setTotal(r.total);
          setI(0);
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    }, 120);
    return () => clearTimeout(t);
  }, [relation, crossOnly, docFilter, entityFilter]);

  const edge = edges[i];
  const meta = RELATION_META[relation];

  return (
    <div className="mx-auto max-w-[1600px] px-4 py-4">
      {/* ── verdict tabs ─────────────────────────────────────────────────── */}
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        {TABS.map((t) => {
          const m = RELATION_META[t.key];
          const active = relation === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setRelation(t.key)}
              className={`${m.className} rounded px-3 py-1.5 text-left transition-all`}
              style={{
                background: active
                  ? "color-mix(in srgb, var(--v) 14%, transparent)"
                  : "var(--paper)",
                border: `1px solid ${active ? "color-mix(in srgb, var(--v) 45%, transparent)" : "var(--rule)"}`,
              }}
            >
              <div className="note" style={{ fontSize: 9 }}>
                {t.caseNo}
              </div>
              <div
                className="flex items-baseline gap-2 text-[13.5px] font-medium"
                style={{ color: active ? "var(--v)" : "var(--ink-soft)" }}
              >
                {m.label}
                <span className="fig text-[13.5px]" style={{ opacity: 0.7 }}>
                  {fmtInt(counts[t.key])}
                </span>
              </div>
            </button>
          );
        })}

        <span className="ml-auto flex items-center gap-1.5">
          <ScopePicker
            docs={docs}
            entity={entityFilter}
            onEntity={setEntityFilter}
            document={docFilter}
            onDocument={setDocFilter}
          />
        </span>

        <label
          className="flex cursor-pointer items-center gap-1.5 text-[13px]"
          style={{ color: "var(--ink-faint)" }}
        >
          <input
            type="checkbox"
            checked={crossOnly}
            onChange={(e) => setCrossOnly(e.target.checked)}
          />
          across documents only
        </label>
      </div>

      <p className="mb-3 text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
        {meta.blurb}
      </p>

      {loading ? (
        <div className="sheet rounded-md p-16 text-center text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
          loading…
        </div>
      ) : !edge ? (
        <div className="sheet rounded-md p-10 text-center">
          {/*
            An empty result is a finding, and it deserves a reason rather than a
            guess. This used to say "extraction may still be running", which is
            almost never why: the usual reason is that comparison is gated on
            the subject, so a document about a subject nobody else in the corpus
            mentions has nothing to compare against, and never will. Telling a
            reader that their upload is still processing when it finished
            minutes ago sends them to wait for something that is not coming.
          */}
          <p className="m-0 text-[14px]">
            No {meta.label.toLowerCase()} pairs
            {docFilter ? " involving this document" : entityFilter ? " involving this subject" : ""}
            {crossOnly ? ", across documents" : ""}.
          </p>
          <p
            className="mx-auto mt-2 mb-0 max-w-lg text-[13px] leading-relaxed"
            style={{ color: "var(--ink-faint)" }}
          >
            {(docFilter || entityFilter) && crossOnly ? (
              <>
                Two facts are only ever compared when they are about the same
                subject. A document about a subject no other document here
                mentions — a company, a country, an institution — has nothing to
                meet across the corpus. That is the system declining to invent a
                link, not a gap. Untick{" "}
                <em>across documents</em> to see what it says against itself.
              </>
            ) : docFilter || entityFilter ? (
              <>
                Nothing in this document produced a {meta.label.toLowerCase()}{" "}
                pair. Its facts are still in <a href="/explorer" style={{ textDecoration: "underline" }}>Facts</a>.
              </>
            ) : (
              <>
                Nothing in the corpus produced this relation. Where that is the
                honest answer it is left empty rather than filled by lowering
                the bar.
              </>
            )}
          </p>
        </div>
      ) : (
        <motion.div key={edge.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
          {/* ── verdict header ─────────────────────────────────────────── */}
          <div className={`${meta.className} panel mb-3 rounded-md p-3`}>
            <div className="flex flex-wrap items-center gap-3">
              <span className="badge">{meta.label}</span>
              {edge.axis && (
                <span className="text-[13.5px]">
                  <span className="note">because </span>
                  <span style={{ color: "var(--v)" }}>{edge.axis}</span>
                  <span className="note"> differs</span>
                </span>
              )}
              <span className="note">
                decided by {edge.decided_by}
              </span>
              <span className="fig text-[13px]" style={{ color: "var(--ink-faint)" }}>
                confidence {edge.confidence.toFixed(2)}
              </span>
              {edge.cross_document && (
                <span className="note" style={{ color: "var(--focus)" }}>
                  across documents
                </span>
              )}

              {/*
                Says what it is, not just where you are.
                
                This was two unlabelled arrows around "1 / 40", pushed to the
                far edge of a row that already held a verdict, an axis and a
                confidence. A reader looked at this screen and asked why only
                one comparison was being shown out of nine thousand — the
                answer was on screen and unreadable. The count of everything
                available is the part that answers the question, so it is the
                part that is now spelled out.
              */}
              <div className="ml-auto flex items-center gap-2">
                <span className="text-[13px]" style={{ color: "var(--ink-faint)" }}>
                  Example <span className="fig">{i + 1}</span> of{" "}
                  <span className="fig">{fmtInt(Math.min(edges.length, total))}</span>
                  {total > edges.length && (
                    <>
                      {" "}
                      shown · <span className="fig">{fmtInt(total)}</span> in all
                    </>
                  )}
                </span>
                <button
                  onClick={() => setI((n) => Math.max(0, n - 1))}
                  disabled={i === 0}
                  aria-label="Previous example"
                  className="sheet rounded px-2 py-1 text-[13px] disabled:opacity-30"
                >
                  ←
                </button>
                <button
                  onClick={() => setI((n) => Math.min(edges.length - 1, n + 1))}
                  disabled={i >= edges.length - 1}
                  aria-label="Next example"
                  className="sheet rounded px-2 py-1 text-[13px] disabled:opacity-30"
                >
                  →
                </button>
              </div>
            </div>
          </div>

          {/* ── the two claims ─────────────────────────────────────────── */}
          <div className="grid gap-3 lg:grid-cols-2">
            {[edge.a, edge.b].map((c, side) => (
              <div key={c.id} className="space-y-3">
                <div className="sheet rounded-md p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="note mb-1">{c.subject}</div>
                      <h3 className="m-0 truncate text-[14px] font-medium" title={c.predicate}>
                        {c.predicate}
                      </h3>
                    </div>
                    <div className="text-right">
                      <div className="fig text-[20px] font-medium leading-none">
                        {c.value.raw}
                      </div>
                      {c.value.canonical && (
                        <div className="fig mt-1 text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
                          = {Number(c.value.canonical).toExponential(4)}
                        </div>
                      )}
                    </div>
                  </div>
                  <ScopeChips
                    claim={c}
                    className="mt-3"
                    highlight={edge.axis ? [edge.axis] : []}
                  />
                </div>
                <EvidencePane claim={c} height={480} compact />
                <div className="note px-1">
                  side {side === 0 ? "A" : "B"}
                </div>
              </div>
            ))}
          </div>

          {/* ── the reasoning trace ────────────────────────────────────── */}
          <div className="sheet mt-3 rounded-md p-3">
            <p className="note mb-2">how this was decided</p>
            <ol className="m-0 list-none space-y-1 p-0">
              {edge.trace.map((line, n) => {
                const terminal = line.startsWith("→");
                return (
                  <motion.li
                    key={n}
                    initial={{ opacity: 0, x: -4 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: n * 0.04, duration: 0.2 }}
                    className="flex gap-2.5 text-[13px]"
                    style={{ color: terminal ? "var(--ink)" : "var(--ink-soft)" }}
                  >
                    <span
                      className="fig shrink-0"
                      style={{ color: "var(--ink-faint)", minWidth: 16 }}
                    >
                      {terminal ? "" : `${n + 1}.`}
                    </span>
                    <span className={terminal ? "font-medium" : ""}>{line}</span>
                  </motion.li>
                );
              })}
            </ol>
            {edge.explanation && (
              <p
                className="mt-3 border-t pt-3 text-[13.5px] leading-relaxed"
                style={{ borderColor: "var(--rule)", color: "var(--ink-soft)" }}
              >
                <span className="note mr-2">generated summary</span>
                {edge.explanation}
              </p>
            )}
            <p className="mt-3 text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
              Paired because: {edge.reason}. Steps 1–{edge.trace.length} are
              deterministic — no model was asked whether these two facts agree.
            </p>
          </div>
        </motion.div>
      )}
    </div>
  );
}
