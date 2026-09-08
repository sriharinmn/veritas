"use client";

import { useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { Evidence } from "@/lib/api";

// Served from public/ rather than a CDN so the app works with no network at
// all, which is the premise of the offline snapshot.
pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

/**
 * The PDF surface and the bounding boxes drawn over it.
 *
 * Kept in its own module and loaded with `ssr: false`, because importing
 * react-pdf reaches for DOMMatrix at module scope — a "use client" boundary
 * stops the component *rendering* on the server but not the module *evaluating*
 * there, which is a distinction worth remembering.
 */
export default function PdfCanvas({
  file,
  evidence,
  width,
  onError,
}: {
  file: string;
  evidence: Evidence;
  width: number;
  onError: (message: string) => void;
}) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);

  return (
    <div className="relative mx-auto w-fit py-3">
      <Document
        file={file}
        // "Failed to fetch" is what the browser says when the API is not
        // answering, and it tells a reader nothing they can act on. Naming the
        // likely cause is the difference between a dead end and a next step.
        onLoadError={(e) =>
          onError(
            /failed to fetch/i.test(e.message)
              ? "Could not reach the server for this page."
              : `Could not read this PDF — ${e.message}`,
          )
        }
        loading={
          <div className="p-8 text-[13px]" style={{ color: "var(--ink-faint)" }}>
            Loading page {evidence.page}…
          </div>
        }
      >
        <Page
          pageNumber={evidence.page}
          width={width}
          renderTextLayer={false}
          renderAnnotationLayer={false}
          onRenderSuccess={(p) => setSize({ w: p.width, h: p.height })}
        />
      </Document>

      {/* Positioned from rects normalised to 0..1 at parse time, which is why
          they land correctly at any width without the frontend knowing the
          page dimensions. Drawn only after the page renders, so a box can never
          appear over blank space. */}
      {size &&
        evidence.rects.map((r, i) => (
          <div
            key={i}
            className="evidence-rect"
            style={{
              left: r.x0 * size.w,
              top: r.y0 * size.h + 12,
              width: Math.max(3, (r.x1 - r.x0) * size.w),
              height: Math.max(3, (r.y1 - r.y0) * size.h),
              animationDelay: `${i * 60}ms`,
            }}
          />
        ))}
    </div>
  );
}
