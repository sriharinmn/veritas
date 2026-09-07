"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import { api, type Claim } from "@/lib/api";

// ssr:false is load-bearing. react-pdf touches DOMMatrix at module scope, and a
// "use client" boundary stops a component rendering on the server but not the
// module evaluating there — which is how this cost a 500 before it cost a
// thought.
const PdfCanvas = dynamic(() => import("@/components/PdfCanvas"), {
  ssr: false,
  loading: () => (
    <div className="p-8 text-[11px]" style={{ color: "var(--ink-faint)" }}>
      preparing viewer…
    </div>
  ),
});

/**
 * The evidence pane.
 *
 * A claim is only worth anything if a reader can see where it came from, so
 * this renders the actual page of the actual PDF and draws the claim's bounding
 * box over it. The rectangles are stored normalised to 0..1 at parse time,
 * which is why they land correctly at any zoom without the frontend knowing
 * anything about page dimensions.
 *
 * This is the screen the assignment's "link every fact to evidence in its
 * source document" requirement actually cashes out in.
 */
export function EvidencePane({
  claim,
  height = 640,
  compact = false,
}: {
  claim: Claim | null;
  height?: number;
  compact?: boolean;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  // react-pdf reaches for DOM APIs that do not exist during server rendering,
  // and a client component is still rendered on the server in the App Router.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const [width, setWidth] = useState(560);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver(([e]) =>
      setWidth(Math.max(240, e.contentRect.width - 2)),
    );
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    setError(null);
  }, [claim?.id]);

  const ev = claim?.evidence?.[0];

  if (!claim || !ev || !claim.document_id) {
    return (
      <div
        ref={wrapRef}
        className="panel flex items-center justify-center rounded-md"
        style={{ height }}
      >
        <p className="text-[12px]" style={{ color: "var(--ink-faint)" }}>
          Select a claim to see the page it came from.
        </p>
      </div>
    );
  }

  return (
    <div ref={wrapRef} className="panel flex flex-col overflow-hidden rounded-md">
      <div
        className="flex items-center gap-3 border-b px-3 py-2"
        style={{ borderColor: "var(--line)" }}
      >
        <span className="label">evidence</span>
        <span className="num text-[11px]" style={{ color: "var(--ink-dim)" }}>
          page {ev.page}
        </span>
        <span className="num text-[11px]" style={{ color: "var(--ink-faint)" }}>
          chars {ev.char_start}–{ev.char_end}
        </span>
        {claim.scale_inferred && (
          <span className="badge v-reconciled" title="Scale inherited from document context rather than stated locally">
            scale inferred
          </span>
        )}
        <a
          href={api.pdfUrl(claim.document_id)}
          target="_blank"
          rel="noreferrer"
          className="ml-auto text-[11px] no-underline"
          style={{ color: "var(--ink-faint)" }}
        >
          open pdf ↗
        </a>
      </div>

      {/* The verbatim quote, above the render. A reader checking a number should
          be able to read the sentence without hunting for it in the image. */}
      <div
        className="border-b px-3 py-2 text-[11px] leading-relaxed"
        style={{ borderColor: "var(--line)", color: "var(--ink-dim)" }}
      >
        <span
          className="num"
          style={{
            background: "color-mix(in srgb, var(--accent) 22%, transparent)",
            padding: "1px 3px",
            borderRadius: 2,
          }}
        >
          {claim.value.raw}
        </span>{" "}
        <span style={{ color: "var(--ink-faint)" }}>in</span>{" "}
        {ev.quote.length > 260 ? ev.quote.slice(0, 260) + "…" : ev.quote}
      </div>

      <div
        className="sunken relative flex-1 overflow-auto"
        style={{ height: height - 78 }}
      >
        {!mounted ? (
          <div className="p-8 text-[11px]" style={{ color: "var(--ink-faint)" }}>
            preparing viewer…
          </div>
        ) : error ? (
          <div className="flex h-full items-center justify-center px-6 text-center">
            <p className="text-[12px]" style={{ color: "var(--contradict)" }}>
              {error}
            </p>
          </div>
        ) : (
          <PdfCanvas
            file={api.pdfUrl(claim.document_id)}
            evidence={ev}
            width={compact ? Math.min(width - 24, 420) : width - 24}
            onError={setError}
          />
        )}
      </div>
    </div>
  );
}
