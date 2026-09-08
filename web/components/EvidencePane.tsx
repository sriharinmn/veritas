"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Source } from "@/components/Source";
import { api, type Claim } from "@/lib/api";

// ssr:false is load-bearing. react-pdf touches DOMMatrix at module scope, and a
// "use client" boundary stops a component rendering on the server but not the
// module evaluating there — which is how this cost a 500 before it cost a
// thought.
const PdfCanvas = dynamic(() => import("@/components/PdfCanvas"), {
  ssr: false,
  loading: () => <Waiting />,
});

// useSyncExternalStore's server snapshot is what runs during SSR and the
// client snapshot on hydration, so this is `true` only in a browser. Module
// scope, not inline, so the identities are stable across renders.
const _subscribeNever = () => () => {};
const _onClient = () => true;
const _onServer = () => false;

/**
 * The evidence pane — the screen the whole product exists for.
 *
 * A fact is worth nothing if a reader cannot see where it came from, so this
 * renders the real page of the real PDF and marks the claim's span on it. The
 * rectangles are stored normalised to 0..1 at parse time, which is why they
 * land correctly at any zoom without the frontend knowing anything about page
 * dimensions.
 *
 * The quote is shown above the render as well as marked on it. Someone checking
 * a figure should be able to read the sentence immediately rather than hunt for
 * it in an image — and if the PDF itself will not load, the quote alone still
 * answers "where did this come from".
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
  const [width, setWidth] = useState(560);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  // "Are we on the client yet", asked the way React means it to be asked. The
  // effect-plus-setState version of this renders the pane twice on every mount,
  // and the second render is what starts the PDF measuring itself -- which is
  // the loop the ResizeObserver below already has to defend against. One
  // render is both correct and one fewer thing feeding that loop.
  const mounted = useSyncExternalStore(_subscribeNever, _onClient, _onServer);

  useEffect(() => {
    if (!wrapRef.current) return;

    // Measuring this element decides the PDF's render width, and rendering the
    // PDF changes this element's size — a loop that made the pane shudder for
    // several seconds on load before settling.
    //
    // Two things break it. A change smaller than the threshold is ignored, so
    // sub-pixel and scrollbar-width jitter cannot feed itself; and the update
    // is deferred to the next frame, so a measurement taken during layout does
    // not synchronously trigger the next one. Ten pixels is well below what a
    // reader would notice and well above the noise.
    const THRESHOLD = 10;
    let frame = 0;

    const ro = new ResizeObserver(([entry]) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const next = Math.max(240, Math.round(entry.contentRect.width) - 2);
        setWidth((current) => (Math.abs(current - next) < THRESHOLD ? current : next));
      });
    });

    ro.observe(wrapRef.current);
    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
    };
  }, []);

  // Reset the load state when the pane is pointed at a different fact.
  //
  // Adjusting state during render rather than in an effect, which is what
  // react.dev recommends for exactly this shape: an effect would commit the
  // previous claim's error message for one frame and then render again to
  // clear it, so a reader clicking away from a failed PDF saw the old failure
  // flash on the new one.
  const [shownClaimId, setShownClaimId] = useState(claim?.id);
  if (claim?.id !== shownClaimId) {
    setShownClaimId(claim?.id);
    setError(null);
    setAttempt(0);
  }

  const retry = useCallback(() => {
    setError(null);
    setAttempt((a) => a + 1);
  }, []);

  const ev = claim?.evidence?.[0];

  if (!claim || !ev || !claim.document_id) {
    return (
      <div ref={wrapRef} className="sheet flex items-center justify-center" style={{ height }}>
        <p className="m-0 text-[13.5px]" style={{ color: "var(--ink-faint)" }}>
          Choose a fact to see the page it came from.
        </p>
      </div>
    );
  }

  return (
    <div ref={wrapRef} className="sheet flex flex-col overflow-hidden">
      <div
        className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-4 py-2.5"
        style={{ borderColor: "var(--rule)" }}
      >
        <span className="text-[13.5px] font-medium">Page {ev.page}</span>
        <Source extractor={claim.extractor} />
        {claim.scale_inferred && (
          <span
            className="badge v-reconciled"
            title="The scale for this figure was taken from a table header or a document-level statement rather than stated next to the number itself."
          >
            Scale inherited
          </span>
        )}
        <a
          href={api.pdfUrl(claim.document_id)}
          target="_blank"
          rel="noreferrer"
          className="ml-auto text-[13px]"
          style={{ color: "var(--ink-soft)" }}
        >
          Open the PDF
        </a>
      </div>

      {/* The verbatim quote. This is the claim's proof, and it works even when
          the page image does not. */}
      <blockquote
        className="m-0 border-b px-4 py-3 text-[13.5px] leading-relaxed"
        style={{ borderColor: "var(--rule)", color: "var(--ink-soft)" }}
      >
        <mark
          className="fig"
          style={{
            background: "var(--mark)",
            color: "var(--ink)",
            padding: "1px 4px",
            borderRadius: 2,
          }}
        >
          {claim.value.raw}
        </mark>{" "}
        {ev.quote.length > 260 ? `${ev.quote.slice(0, 260)}…` : ev.quote}
      </blockquote>

      <div className="sunk relative flex-1 overflow-auto" style={{ height: height - 96 }}>
        {!mounted ? (
          <Waiting />
        ) : error ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
            <p className="m-0 text-[13.5px]" style={{ color: "var(--ink-soft)" }}>
              {error}
            </p>
            <p className="m-0 text-[13px]" style={{ color: "var(--ink-faint)" }}>
              The quote above is the evidence; only the page image is missing.
            </p>
            <button className="btn btn-quiet" onClick={retry}>
              Try again
            </button>
          </div>
        ) : (
          <PdfCanvas
            key={`${claim.id}-${attempt}`}
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

function Waiting() {
  return (
    <div className="p-8 text-[13px]" style={{ color: "var(--ink-faint)" }}>
      Loading the page…
    </div>
  );
}
