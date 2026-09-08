"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { EvidencePane } from "@/components/EvidencePane";
import { ScopeChips } from "@/components/ScopeChips";
import { ScopePicker } from "@/components/ScopePicker";
import { Source } from "@/components/Source";
import { api, fmtInt, type Claim, type DocumentSummary } from "@/lib/api";

/**
 * The explorer: every grounded claim, and the page it came from.
 *
 * Deliberately a dense table rather than cards. A reviewer is scanning for a
 * figure among hundreds; rows that fit forty to a screen and align their
 * numerals are worth more than anything decorative. The whole interaction is
 * one click — select a row, see the highlighted span.
 */
export default function ExplorerPage() {
  // useSearchParams needs a Suspense boundary to keep the route statically
  // renderable; without one Next refuses to prerender the page at all.
  return (
    <Suspense fallback={null}>
      <Explorer />
    </Suspense>
  );
}

function Explorer() {
  // Arriving from a finished upload, pre-filtered to the document just added.
  // The alternative was telling somebody their document was in here somewhere
  // and leaving them to find it in a filter of six.
  const params = useSearchParams();
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<Claim | null>(null);
  const [docFilter, setDocFilter] = useState<string>(params.get("document") ?? "");
  const [entityFilter, setEntityFilter] = useState<string>(params.get("subject") ?? "");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.documents().then(setDocs).catch(() => {});
  }, []);

  useEffect(() => {
    // Inside the timeout, not before it: the spinner should appear when the
    // request goes out, not while the reader is still typing. Every keystroke
    // used to flash it for 180ms and cancel.
    const t = setTimeout(() => {
      setLoading(true);
      api
        .claims({
          document_id: docFilter || undefined,
          entity_id: entityFilter || undefined,
          q: q || undefined,
          limit: 400,
        })
        .then((r) => {
          setClaims(r.items);
          setTotal(r.total);
          setSelected((prev) =>
            prev && r.items.some((c) => c.id === prev.id) ? prev : r.items[0] ?? null,
          );
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    }, 180);
    return () => clearTimeout(t);
  }, [docFilter, entityFilter, q]);

  const docName = useMemo(
    () => Object.fromEntries(docs.map((d) => [d.id, d.filename])),
    [docs],
  );

  return (
    <div className="mx-auto max-w-[1600px] px-4 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search a metric, a figure or a subject"
          className="sheet w-72 rounded px-2.5 py-1.5 text-[13.5px] outline-none focus:ring-1"
          style={{ color: "var(--ink)" }}
        />
        <ScopePicker
          docs={docs}
          entity={entityFilter}
          onEntity={setEntityFilter}
          document={docFilter}
          onDocument={setDocFilter}
        />
        <span className="fig text-[13px]" style={{ color: "var(--ink-faint)" }}>
          {loading ? "…" : `${fmtInt(total)} claims`}
          {total > claims.length && ` · showing ${claims.length}`}
        </span>
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(420px,44%)]">
        {/* ── the table ───────────────────────────────────────────────────── */}
        <div className="sheet overflow-hidden rounded-md">
          <div className="max-h-[calc(100vh-140px)] overflow-auto">
            <table className="w-full border-collapse text-[13.5px]">
              <thead className="sticky top-0 z-10" style={{ background: "var(--paper)" }}>
                <tr style={{ borderBottom: "1px solid var(--rule)" }}>
                  {["What is measured", "Value", "Period", "Basis", "Read by", "Document", "Pg"].map((h) => (
                    <th
                      key={h}
                      className="note px-2.5 py-2 text-left font-medium"
                      style={{ borderBottom: "1px solid var(--rule)" }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {claims.map((c) => {
                  const active = selected?.id === c.id;
                  return (
                    <tr
                      key={c.id}
                      onClick={() => setSelected(c)}
                      className="cursor-pointer transition-colors"
                      style={{
                        background: active ? "color-mix(in srgb, var(--focus) 12%, transparent)" : undefined,
                        borderLeft: `2px solid ${active ? "var(--focus)" : "transparent"}`,
                      }}
                    >
                      <td className="max-w-[280px] truncate px-2.5 py-1.5" title={c.predicate}>
                        {c.predicate}
                      </td>
                      <td className="fig whitespace-nowrap px-2.5 py-1.5 text-right">
                        {c.value.raw}
                        {c.value.currency && (
                          <span className="ml-1" style={{ color: "var(--ink-faint)" }}>
                            {c.value.currency}
                          </span>
                        )}
                      </td>
                      <td className="fig whitespace-nowrap px-2.5 py-1.5" style={{ color: c.scope.period ? "var(--ink-soft)" : "var(--ink-faint)" }}>
                        {c.scope.period ?? "—"}
                      </td>
                      <td className="px-2.5 py-1.5" style={{ color: "var(--ink-faint)" }}>
                        {c.scope.basis === "unknown" ? "—" : c.scope.basis}
                      </td>
                      <td className="whitespace-nowrap px-2.5 py-1.5">
                        <Source extractor={c.extractor} />
                      </td>
                      <td className="max-w-[130px] truncate px-2.5 py-1.5" style={{ color: "var(--ink-faint)" }}>
                        {(docName[c.document_id ?? ""] ?? "").replace(/^\d+-/, "").slice(0, 18)}
                      </td>
                      <td className="fig px-2.5 py-1.5 text-right" style={{ color: "var(--ink-faint)" }}>
                        {c.page}
                      </td>
                    </tr>
                  );
                })}
                {!loading && claims.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-3 py-10 text-center" style={{ color: "var(--ink-faint)" }}>
                      No facts match that search.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* ── the selected claim ──────────────────────────────────────────── */}
        <div className="space-y-3">
          {selected && (
            <div className="sheet rounded-md p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="note mb-1">{selected.subject}</div>
                  <h2 className="m-0 text-[15px] font-medium leading-tight">
                    {selected.predicate}
                  </h2>
                </div>
                <div className="text-right">
                  <div className="fig text-[19px] font-medium leading-none">
                    {selected.value.raw}
                  </div>
                  <div className="note mt-1">
                    {selected.value.currency ?? selected.value.unit ?? selected.value.kind}
                  </div>
                </div>
              </div>

              <div className="mt-2">
                <Source extractor={selected.extractor} />
              </div>

              <ScopeChips claim={selected} className="mt-3" />

              {selected.value.canonical && (
                <div
                  className="fig mt-3 rounded px-2 py-1.5 text-[13px]"
                  style={{ background: "var(--paper-sunk)", color: "var(--ink-faint)" }}
                >
                  in one form: {selected.value.canonical}
                </div>
              )}
            </div>
          )}

          <EvidencePane claim={selected} height={620} />
        </div>
      </div>
    </div>
  );
}
