"use client";

import { useEffect, useId, useRef, useState } from "react";

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
 */
export function Define({ term, children }: { term: keyof typeof GLOSSARY | string; children?: string }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const id = useId();
  const text = children ?? GLOSSARY[term as keyof typeof GLOSSARY] ?? "";

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onClick = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  if (!text) return null;

  return (
    <span
      ref={wrap}
      className="relative inline-block"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="marker"
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        aria-label={`What ${term} means`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        i
      </button>

      {open && (
        <span
          role="tooltip"
          id={id}
          className="sheet absolute left-1/2 z-50 mt-2 block w-72 -translate-x-1/2 p-3 text-[13px] leading-relaxed"
          style={{ top: "100%", color: "var(--ink-soft)", boxShadow: "0 6px 20px rgba(27,35,48,0.1)" }}
        >
          <span className="mb-1 block font-medium" style={{ color: "var(--ink)" }}>
            {term}
          </span>
          {text}
        </span>
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
  extractor:
    "Which tier read this fact: Groq's hosted model, a local model through Ollama, or rules alone with no model at all.",
  candidate:
    "Every numeral, date, percentage and amount a regex sweep found on the page. The model only labels these; it never writes a number itself.",
  "spot conversion":
    "How many of the numbers found on the page became usable facts. Because the sweep misses nothing, this is a recall figure that needs no hand-labelled answers.",
  "deterministic mode":
    "Extraction by rules alone, when no model is reachable. It recovers about 47% of the facts the model tier finds on the same pages, and every one is still grounded.",
} as const;
