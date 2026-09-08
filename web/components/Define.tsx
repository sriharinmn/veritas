"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const WIDTH = 288; // w-72
const EDGE = 12; // keep this clear of the viewport edge
const GAP = 8; // between the marker and the panel

/**
 * A footnote marker that explains a term.
 *
 * This product has a vocabulary a reader does not arrive with — scope axes,
 * grounding, quarantine, the difference between reconciled and ambiguous — and
 * an interface that uses those words without explaining them is asking people
 * to guess. A financial document annotates with markers rather than by
 * interrupting the sentence, so this does too.
 *
 * Only genuinely unfamiliar terms get one. A marker on every noun is the same
 * as no markers at all.
 *
 * It opens on hover *and* on click, and closes on Escape or an outside click,
 * because hover alone is unreachable by keyboard and unusable on a touchscreen.
 *
 * **The panel is a portal, not a child.** As an absolutely-positioned child it
 * was clipped by the first ancestor with `overflow`, and the scope table on a
 * case page is exactly that — `overflow-x-auto`, because a wide comparison has
 * to scroll. The definition of "period" appeared with its first words sliced
 * off, which is a poor advertisement for a tooltip. Portalled to the body and
 * positioned against the viewport, it cannot be clipped by anything, and it is
 * clamped so a marker near an edge does not push it off-screen either.
 */
export function Define({ term, children }: { term: keyof typeof GLOSSARY | string; children?: string }) {
  const [open, setOpen] = useState(false);
  const [box, setBox] = useState<{ top: number; left: number; above: boolean } | null>(null);
  const wrap = useRef<HTMLSpanElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const closing = useRef<number | undefined>(undefined);
  const id = useId();
  const text = children ?? GLOSSARY[term as keyof typeof GLOSSARY] ?? "";

  // Hovering from the marker to the panel crosses a gap that is no longer
  // inside the marker's own element, so a bare mouseleave would close it the
  // moment you tried to read it. A short grace period makes the two behave as
  // one target.
  const show = useCallback(() => {
    window.clearTimeout(closing.current);
    setOpen(true);
  }, []);
  const hide = useCallback(() => {
    closing.current = window.setTimeout(() => setOpen(false), 120);
  }, []);

  useLayoutEffect(() => {
    if (!open || !wrap.current) return;
    const place = () => {
      const marker = wrap.current?.getBoundingClientRect();
      if (!marker) return;
      const height = panel.current?.offsetHeight ?? 0;
      const below = marker.bottom + GAP;
      const above = height > 0 && below + height > window.innerHeight - EDGE;
      setBox({
        top: above ? marker.top - GAP - height : below,
        left: Math.max(
          EDGE,
          Math.min(
            marker.left + marker.width / 2 - WIDTH / 2,
            window.innerWidth - WIDTH - EDGE,
          ),
        ),
        above,
      });
    };
    place();
    // Measured twice: the first pass has no panel to measure, so it cannot know
    // whether the definition fits below the marker.
    const frame = requestAnimationFrame(place);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (!wrap.current?.contains(target) && !panel.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  useEffect(() => () => window.clearTimeout(closing.current), []);

  if (!text) return null;

  return (
    <span ref={wrap} className="inline-block" onMouseEnter={show} onMouseLeave={hide}>
      <button
        type="button"
        className="marker"
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        aria-label={`What ${term} means`}
        onClick={(e) => {
          e.stopPropagation();
          window.clearTimeout(closing.current);
          setOpen((v) => !v);
        }}
      >
        i
      </button>

      {open &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={panel}
            role="tooltip"
            id={id}
            onMouseEnter={show}
            onMouseLeave={hide}
            className="sheet p-3 text-[13px] leading-relaxed"
            style={{
              position: "fixed",
              top: box?.top ?? -9999,
              left: box?.left ?? -9999,
              width: WIDTH,
              zIndex: 60,
              color: "var(--ink-soft)",
              boxShadow: "0 6px 20px rgba(27,35,48,0.14)",
              // Hidden until it has been placed, so it never flashes in the
              // corner on the way to where it belongs.
              visibility: box ? "visible" : "hidden",
            }}
          >
            <span className="mb-1 block font-medium" style={{ color: "var(--ink)" }}>
              {term}
            </span>
            {text}
          </div>,
          document.body,
        )}
    </span>
  );
}

/**
 * The vocabulary, defined once.
 *
 * Written for someone who has never read this repository: what the thing is and
 * why it matters, not how it is implemented.
 */
export const GLOSSARY = {
  grounded:
    "The value appears literally in the span of the page the fact points at. A fact that fails this check is quarantined and never enters the graph — there is no partial credit.",
  scope:
    "The six things that decide whether two figures are comparable at all: period, basis, segment, geography, accounting standard, and whether it is reported or projected.",
  basis:
    "Consolidated figures include subsidiaries; standalone figures are the parent company alone. The same metric differs legitimately between the two.",
  period:
    "The stretch of time a figure covers. FY24 and 'year ended March 31, 2024' are the same twelve months; a nine-month stub ending in December is not.",
  corroboration:
    "Two documents state the same thing and agree, once units and scales are normalised — 1,266 million and 127 crore are one figure written two ways.",
  contradiction:
    "Two documents state the same thing and disagree, with every scope axis checked and found identical, so nothing about what is being measured explains the gap.",
  reconciled:
    "The values differ, and exactly one scope axis differs too. That axis is the explanation — not a guess about it.",
  ambiguous:
    "Two or more scope axes differ, or neither figure carries a resolved period. The system declines to decide rather than assert something it cannot support.",
  quarantine:
    "Facts the grounding check refused. They are counted and kept visible rather than deleted, because an error rate you cannot see is one you cannot fix.",
  ontology:
    "The list of metric names the system has learned from the documents themselves. There is no fixed list of metrics anywhere in the code.",
  predicate: "What is being measured — 'revenue from operations', 'EBITDA', 'closing cash balance'.",
  subject:
    "Who or what a fact is about — a company, a country, an institution. Two facts are only ever compared when they share one, which is why documents about the same subject are the documents that can meet at all.",
  extractor:
    "Which tier read this fact: Groq's hosted model, a local model through Ollama, or rules alone with no model at all.",
  candidate:
    "Every numeral, date, percentage and amount a regex sweep found on the page. The model only labels these; it never writes a number itself.",
  "spot conversion":
    "How many of the numbers found on the page became usable facts. Because the sweep misses nothing, this is a recall figure that needs no hand-labelled answers.",
  "deterministic mode":
    "Extraction by rules alone, when no model is reachable. Measured across the whole corpus it recovers about 23% of the facts the model tier finds on the same pages, and every one is still grounded.",
} as const;
