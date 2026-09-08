"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { API, fmtInt } from "@/lib/api";

/**
 * Upload a PDF and watch it become facts.
 *
 * The screen is built around one idea: extraction takes minutes, so the reader
 * must be able to see it working rather than watch a spinner and wonder. Pages
 * are processed densest-first and announced as they land, so the financial
 * statements arrive in the first few seconds and the claim count climbs while
 * the sparse pages are still being worked through.
 *
 * The routing decision is shown before any page is processed, because which
 * tier is doing the work — and why — is the most interesting thing about the
 * system and the thing a reviewer would otherwise have to take on trust.
 */

type Event = {
  type: string;
  [k: string]: unknown;
};

type PageRow = { page: number; grounded: number; quarantined: number };

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [job, setJob] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("idle");
  const [error, setError] = useState<string | null>(null);

  const [routed, setRouted] = useState<Event | null>(null);
  const [parsed, setParsed] = useState<Event | null>(null);
  const [spotted, setSpotted] = useState<Event | null>(null);
  const [context, setContext] = useState<Event | null>(null);
  const [pages, setPages] = useState<PageRow[]>([]);
  const [progress, setProgress] = useState({ done: 0, total: 0, claims: 0 });
  const [summary, setSummary] = useState<Event | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const streamRef = useRef<EventSource | null>(null);

  useEffect(() => () => streamRef.current?.close(), []);

  const reset = () => {
    streamRef.current?.close();
    setJob(null);
    setStatus("idle");
    setError(null);
    setRouted(null);
    setParsed(null);
    setSpotted(null);
    setContext(null);
    setPages([]);
    setProgress({ done: 0, total: 0, claims: 0 });
    setSummary(null);
  };

  const start = useCallback(async () => {
    if (!file) return;
    reset();
    setStatus("uploading");

    const body = new FormData();
    body.append("file", file);

    let accepted: { job: string };
    try {
      const res = await fetch(`${API}/documents`, { method: "POST", body });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(detail.detail ?? `Upload failed (${res.status})`);
      }
      accepted = await res.json();
    } catch (e) {
      setStatus("failed");
      setError(e instanceof Error ? e.message : String(e));
      return;
    }

    setJob(accepted.job);
    setStatus("running");

    const stream = new EventSource(`${API}/jobs/${accepted.job}/events`);
    streamRef.current = stream;

    stream.onmessage = (message) => {
      const e: Event = JSON.parse(message.data);
      switch (e.type) {
        case "parsed":
          setParsed(e);
          break;
        case "spotted":
          setSpotted(e);
          break;
        case "routed":
          setRouted(e);
          break;
        case "context":
          setContext(e);
          break;
        case "page":
          setPages((prev) => [
            { page: e.page as number, grounded: e.grounded as number, quarantined: e.quarantined as number },
            ...prev,
          ]);
          setProgress({
            done: e.pages_done as number,
            total: e.pages_total as number,
            claims: e.claims_total as number,
          });
          break;
        case "page_failed":
          setPages((prev) => [
            { page: e.page as number, grounded: -1, quarantined: 0 },
            ...prev,
          ]);
          break;
        case "done":
          setSummary(e);
          setStatus("done");
          stream.close();
          break;
        case "error":
          setError(String(e.message));
          setStatus("failed");
          stream.close();
          break;
      }
    };

    // A stream that dies mid-extraction must say so rather than looking stalled.
    stream.onerror = () => {
      if (status !== "done") {
        setStatus((s) => (s === "running" ? "disconnected" : s));
      }
      stream.close();
    };
  }, [file, status]);

  const busy = status === "uploading" || status === "running";
  const pct = progress.total ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <main className="mx-auto max-w-5xl px-6 py-14">
      <header className="mb-10">
        <p className="note mb-2">ingest</p>
        <h1 className="m-0 text-[30px] font-semibold">Upload a document</h1>
        <p className="mt-3 mb-0 max-w-2xl" style={{ color: "var(--ink-soft)" }}>
          A PDF this system has never seen becomes grounded facts, linked to the
          exact page they came from and compared against everything already known.
          Pages are processed densest-first, so the financial statements land in
          the first few seconds.
        </p>
      </header>

      {/* ── the drop target ────────────────────────────────────────────────── */}
      <section
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const dropped = e.dataTransfer.files?.[0];
          if (dropped) setFile(dropped);
        }}
        onClick={() => !busy && inputRef.current?.click()}
        className="card flex cursor-pointer flex-col items-center justify-center px-6 py-12 text-center transition-colors"
        style={{
          borderStyle: "dashed",
          borderColor: dragging ? "var(--focus)" : "var(--rule)",
          background: dragging ? "color-mix(in srgb, var(--focus) 7%, var(--paper))" : undefined,
          opacity: busy ? 0.6 : 1,
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <p className="m-0 text-[15px] font-medium">
          {file ? file.name : "Drop a PDF here, or click to choose one"}
        </p>
        <p className="mt-1.5 mb-0 text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
          {file
            ? `${(file.size / 1e6).toFixed(1)} MB — ready`
            : "Annual reports, prospectuses, statistical releases. Up to 80 MB."}
        </p>
      </section>

      <div className="mt-5 flex flex-wrap items-center gap-2.5">
        <button className="btn btn-primary" disabled={!file || busy} onClick={start}>
          {busy ? "Extracting…" : "Extract facts"}
        </button>
        {file && !busy && (
          <button className="btn btn-quiet" onClick={() => { setFile(null); reset(); }}>
            Clear
          </button>
        )}
        {status === "done" && (
          <Link href="/explorer" className="btn btn-quiet">
            Browse the facts
          </Link>
        )}
      </div>

      {error && (
        <p
          className="mt-5 rounded-md border px-4 py-3 text-[13px]"
          style={{
            borderColor: "color-mix(in srgb, var(--contradict) 40%, transparent)",
            background: "color-mix(in srgb, var(--contradict) 10%, transparent)",
            color: "var(--contradict)",
          }}
        >
          {error}
        </p>
      )}

      {status === "disconnected" && !error && (
        <p className="mt-5 text-[13px]" style={{ color: "var(--reconcile)" }}>
          The progress stream dropped. Extraction is still running on the server —
          the facts will appear in the explorer when it finishes.
        </p>
      )}

      {/* ── the routing decision, before any work is done ──────────────────── */}
      {routed && (
        <section className="card mt-8 p-5">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <span className="note">tier selected</span>
            <span className={`badge ${routed.degraded ? "v-reconciled" : "v-corroboration"}`}>
              {String(routed.tier)}
            </span>
            {typeof routed.eta_seconds === "number" && (
              <span className="text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
                estimated {Math.max(1, Math.round((routed.eta_seconds as number) / 60))} min
              </span>
            )}
          </div>

          {routed.banner ? (
            <p
              className="mb-3 rounded-md border px-4 py-3 text-[13px]"
              style={{
                borderColor: "color-mix(in srgb, var(--reconcile) 45%, transparent)",
                background: "color-mix(in srgb, var(--reconcile) 10%, transparent)",
                color: "var(--reconcile)",
              }}
            >
              {String(routed.banner)}
            </p>
          ) : null}

          <p className="fig m-0 text-[13.5px]" style={{ color: "var(--ink-soft)" }}>
            {String(routed.estimate)}
          </p>

          <details className="mt-3">
            <summary className="cursor-pointer text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
              Why this tier
            </summary>
            <ol className="mt-3 mb-0 space-y-1.5 pl-0 text-[13.5px]" style={{ color: "var(--ink-soft)", listStyle: "none" }}>
              {(routed.trace as string[]).map((line, i) => (
                <li key={i} className="fig leading-relaxed">{line}</li>
              ))}
            </ol>
          </details>
        </section>
      )}

      {/* ── live progress ──────────────────────────────────────────────────── */}
      {(parsed || busy || summary) && (
        <section className="mt-6">
          <div className="mb-3 flex flex-wrap items-baseline gap-x-8 gap-y-2">
            <Metric label="pages" value={parsed ? String(parsed.pages) : "—"} />
            <Metric label="candidates spotted" value={spotted ? fmtInt(spotted.candidates as number) : "—"} />
            <Metric
              label="pages extracted"
              value={progress.total ? `${progress.done} / ${progress.total}` : "—"}
            />
            <Metric label="grounded claims" value={fmtInt(progress.claims)} />
            {context?.entity ? <Metric label="entity read" value={String(context.entity)} /> : null}
          </div>

          <div
            className={`relative h-1.5 w-full overflow-hidden rounded-full ${
              busy && !progress.total ? "bar-indeterminate" : ""
            }`}
            style={{ background: "var(--paper)" }}
          >
            {progress.total > 0 && (
              <div
                className="h-full rounded-full transition-[width] duration-500"
                style={{ width: `${pct}%`, background: "var(--focus)" }}
              />
            )}
          </div>
        </section>
      )}

      {summary && (
        <section className="card mt-6 p-5">
          <p className="m-0 text-[15px] font-medium">
            {fmtInt(summary.claims as number)} grounded claims from {String(summary.pages)} pages
            in {String(summary.seconds)}s
          </p>
          <p className="mt-1.5 mb-0 text-[13px]" style={{ color: "var(--ink-faint)" }}>
            {summary.quarantined as number} quarantined
            {(summary.pages_skipped as number) > 0 &&
              ` · ${summary.pages_skipped} sparser pages beyond the budget were not processed`}
            . The facts are now in the explorer and compared against every document
            already loaded.
          </p>
        </section>
      )}

      {pages.length > 0 && (
        <section className="mt-6">
          <p className="note mb-2">pages as they land — densest first</p>
          <div className="card max-h-80 overflow-y-auto">
            <table className="w-full text-[13.5px]">
              <tbody>
                {pages.map((p, i) => (
                  <tr key={`${p.page}-${i}`} style={{ borderTop: i ? "1px solid var(--rule)" : undefined }}>
                    <td className="fig px-4 py-2" style={{ color: "var(--ink-faint)" }}>
                      page {p.page}
                    </td>
                    <td className="fig px-4 py-2 text-right">
                      {p.grounded < 0 ? (
                        <span style={{ color: "var(--contradict)" }}>failed</span>
                      ) : (
                        <>
                          {p.grounded} claims
                          {p.quarantined > 0 && (
                            <span style={{ color: "var(--ink-faint)" }}> · {p.quarantined} quarantined</span>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {job && (
        <p className="mt-6 text-[13px]" style={{ color: "var(--ink-faint)" }}>
          job {job} · progress streams over server-sent events; closing this tab does
          not stop the extraction.
        </p>
      )}
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="fig text-[19px] font-medium">{value}</div>
      <div className="note mt-0.5">{label}</div>
    </div>
  );
}
