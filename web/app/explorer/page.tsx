"use client";

import { useEffect, useMemo, useState } from "react";
import { EvidencePane } from "@/components/EvidencePane";
import { ScopeChips } from "@/components/ScopeChips";
import { api, fmtInt, type Claim, type DocumentSummary } from "@/lib/api";

/**
 * The explorer: every grounded claim, and the page it came from.
 *
 * Deliberately a dense table rather than cards. A reviewer is scanning for a
 * figure among hundreds; rows that fit forty to a screen and align their
 * numerals are worth more than anything decorative. The whole interaction is
 * one click — select a row, see the highlighted span.
 */
export default function Explorer() {
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<Claim | null>(null);
  const [docFilter, setDocFilter] = useState<string>("");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.documents().then(setDocs).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    const t = setTimeout(() => {
      api
        .claims({ document_id: docFilter || undefined, q: q || undefined, limit: 400 })
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
  }, [docFilter, q]);

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
          placeholder="Filter by predicate, value or entity…"
          className="panel w-72 rounded px-2.5 py-1.5 text-[12px] outline-none focus:ring-1"
          style={{ color: "var(--ink)" }}
        />
        <select
          value={docFilter}
          onChange={(e) => setDocFilter(e.target.value)}
          className="panel rounded px-2 py-1.5 text-[12px] outline-none"
          style={{ color: "var(--ink-dim)" }}
        >
          <option value="">All documents</option>
          {docs.map((d) => (
            <option key={d.id} value={d.id}>
              {d.filename.replace(/^\d+-/, "").replace(/\.pdf$/, "")} ({d.claims})
            </option>
          ))}
        </select>
        <span className="num text-[11px]" style={{ color: "var(--ink-faint)" }}>
          {loading ? "…" : `${fmtInt(total)} claims`}
          {total > claims.length && ` · showing ${claims.length}`}
        </span>
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(420px,44%)]">
        {/* ── the table ───────────────────────────────────────────────────── */}
        <div className="panel overflow-hidden rounded-md">
          <div className="max-h-[calc(100vh-140px)] overflow-auto">
            <table className="w-full border-collapse text-[12px]">
              <thead className="sticky top-0 z-10" style={{ background: "var(--bg-raised)" }}>
                <tr style={{ borderBottom: "1px solid var(--line)" }}>
                  {["Predicate", "Value", "Period", "Basis", "Doc", "Pg"].map((h) => (
                    <th
                      key={h}
                      className="label px-2.5 py-2 text-left font-medium"
                      style={{ borderBottom: "1px solid var(--line)" }}
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
                        background: active ? "color-mix(in srgb, var(--accent) 12%, transparent)" : undefined,
                        borderLeft: `2px solid ${active ? "var(--accent)" : "transparent"}`,
                      }}
                    >
                      <td className="max-w-[280px] truncate px-2.5 py-1.5" title={c.predicate}>
                        {c.predicate}
                      </td>
                      <td className="num whitespace-nowrap px-2.5 py-1.5 text-right">
                        {c.value.raw}
                        {c.value.currency && (
                          <span className="ml-1" style={{ color: "var(--ink-faint)" }}>
                            {c.value.currency}
                          </span>
                        )}
                      </td>
                      <td className="num whitespace-nowrap px-2.5 py-1.5" style={{ color: c.scope.period ? "var(--ink-dim)" : "var(--ink-faint)" }}>
                        {c.scope.period ?? "—"}
                      </td>
                      <td className="px-2.5 py-1.5" style={{ color: "var(--ink-faint)" }}>
                        {c.scope.basis === "unknown" ? "—" : c.scope.basis}
                      </td>
                      <td className="max-w-[130px] truncate px-2.5 py-1.5" style={{ color: "var(--ink-faint)" }}>
                        {(docName[c.document_id ?? ""] ?? "").replace(/^\d+-/, "").slice(0, 18)}
                      </td>
                      <td className="num px-2.5 py-1.5 text-right" style={{ color: "var(--ink-faint)" }}>
                        {c.page}
                      </td>
                    </tr>
                  );
                })}
                {!loading && claims.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-3 py-10 text-center" style={{ color: "var(--ink-faint)" }}>
                      No claims match that filter.
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
            <div className="panel rounded-md p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="label mb-1">{selected.subject}</div>
                  <h2 className="m-0 text-[15px] font-medium leading-tight">
                    {selected.predicate}
                  </h2>
                </div>
                <div className="text-right">
                  <div className="num text-[19px] font-medium leading-none">
                    {selected.value.raw}
                  </div>
                  <div className="label mt-1">
                    {selected.value.currency ?? selected.value.unit ?? selected.value.kind}
                  </div>
                </div>
              </div>

              <ScopeChips claim={selected} className="mt-3" />

              {selected.value.canonical && (
                <div
                  className="num mt-3 rounded px-2 py-1.5 text-[11px]"
                  style={{ background: "var(--bg-sunken)", color: "var(--ink-faint)" }}
                >
                  canonical {selected.value.canonical}
                  {selected.extractor && (
                    <span className="ml-2">· {selected.extractor}</span>
                  )}
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
