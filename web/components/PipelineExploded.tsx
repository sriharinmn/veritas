"use client";

import {
  motion,
  useMotionValueEvent,
  useScroll,
  useTransform,
  type MotionValue,
} from "framer-motion";
import { useRef, useState } from "react";

/**
 * The pipeline, exploded on scroll.
 *
 * Seven plates stacked flat, which fan apart along an isometric axis as the
 * reader scrolls, each one lifting to show what that stage actually does. The
 * technique is borrowed from product teardowns; the justification for using it
 * here is that the thing being taken apart is real. A reviewer who scrolls this
 * once knows the architecture, which is the only job the first twenty seconds
 * of the demo has.
 *
 * Everything below the fold reverts to the terminal register. Motion is for
 * explaining a system; it has no business anywhere near a table of figures a
 * banker is checking.
 */

type Stage = {
  key: string;
  n: string;
  title: string;
  blurb: string;
  detail: string;
  accent: string;
  art: (active: boolean) => React.ReactNode;
};

const C = {
  ink: "#e8e6e1",
  dim: "#a8a49c",
  faint: "#6b675f",
  line: "#33373d",
  accent: "#5b9dd9",
  green: "#4ba07a",
  red: "#d0524a",
  amber: "#c08a3e",
};

/* ── per-stage artwork ───────────────────────────────────────────────────────
   Deliberately schematic. Each drawing shows the one idea that makes the stage
   worth having, not a faithful screenshot. */

const page = (boxes: boolean) => (
  <g>
    <rect x={18} y={12} width={92} height={116} rx={2} fill="#0f1113" stroke={C.line} />
    {[24, 34, 44, 62, 72, 82, 100, 110].map((y, i) => (
      <rect
        key={y}
        x={26}
        y={y}
        width={i % 3 === 0 ? 60 : 76}
        height={3}
        rx={1.5}
        fill={C.faint}
        opacity={0.5}
      />
    ))}
    {boxes && (
      <>
        <rect x={24} y={59} width={80} height={10} rx={1.5} fill="none" stroke={C.accent} strokeWidth={1} opacity={0.9} />
        <rect x={24} y={97} width={80} height={10} rx={1.5} fill="none" stroke={C.accent} strokeWidth={1} opacity={0.6} />
      </>
    )}
  </g>
);

const STAGES: Stage[] = [
  {
    key: "parse",
    n: "01",
    title: "Parse",
    blurb: "Text, tables and geometry — with an offset invariant.",
    detail:
      "page.text[start:end] === block.text, asserted on every block. Page text is built from the blocks, so a citation can never drift off the words it points at.",
    accent: C.accent,
    art: (a) => (
      <g>
        {page(a)}
        {a && (
          <text x={118} y={68} fontSize={7} fill={C.accent} fontFamily="monospace">
            bbox 0..1
          </text>
        )}
      </g>
    ),
  },
  {
    key: "spot",
    n: "02",
    title: "Spot",
    blurb: "Every numeral found by regex. Free, and exhaustive.",
    detail:
      "Because the sweep misses nothing, it yields a denominator: spotted → claimed / rejected / quarantined / silently dropped. That last bucket is measurable recall loss with no labels.",
    accent: C.accent,
    art: (a) => (
      <g>
        {page(false)}
        {[
          [30, 60],
          [66, 60],
          [30, 98],
          [70, 98],
          [48, 79],
        ].map(([x, y], i) => (
          <motion.rect
            key={i}
            x={x}
            y={y}
            width={22}
            height={8}
            rx={1.5}
            fill={C.accent}
            initial={false}
            animate={{ opacity: a ? 0.85 : 0.18 }}
            transition={{ delay: a ? i * 0.05 : 0, duration: 0.35 }}
          />
        ))}
      </g>
    ),
  },
  {
    key: "extract",
    n: "03",
    title: "Extract",
    blurb: "The model labels candidates. It never writes a number.",
    detail:
      "Values and citations are taken from the document mechanically. A model that only labels what was already located cannot hallucinate a figure — the class of failure is removed, not filtered.",
    accent: C.green,
    art: (a) => (
      <g>
        <rect x={16} y={26} width={96} height={88} rx={3} fill="#0f1113" stroke={C.line} />
        {[
          ["subject", "Delhivery Ltd"],
          ["predicate", "revenue"],
          ["value", "74,540.82"],
          ["period", "FY24"],
          ["basis", "standalone"],
        ].map(([k, v], i) => (
          <motion.g
            key={k}
            initial={false}
            animate={{ opacity: a ? 1 : 0.25, x: a ? 0 : -4 }}
            transition={{ delay: a ? 0.06 * i : 0, duration: 0.3 }}
          >
            <text x={24} y={44 + i * 15} fontSize={6.5} fill={C.faint} fontFamily="monospace">
              {k}
            </text>
            <text
              x={104}
              y={44 + i * 15}
              fontSize={6.5}
              fill={k === "value" ? C.ink : C.dim}
              fontFamily="monospace"
              textAnchor="end"
            >
              {v}
            </text>
          </motion.g>
        ))}
      </g>
    ),
  },
  {
    key: "ground",
    n: "04",
    title: "Ground",
    blurb: "A hard gate. Not a confidence score.",
    detail:
      "If the value is not literally inside the span it cites, the claim is quarantined and never enters the graph. There is no low-confidence escape hatch, because a score is something a reader talks themselves past.",
    accent: C.green,
    art: (a) => (
      <g>
        <path d="M30 30 L98 30 L86 74 L42 74 Z" fill="#0f1113" stroke={C.line} />
        <motion.g initial={false} animate={{ opacity: a ? 1 : 0.3 }}>
          <path d="M52 88 L64 100 L84 76" stroke={C.green} strokeWidth={3} fill="none" strokeLinecap="round" />
          <text x={22} y={118} fontSize={6.5} fill={C.green} fontFamily="monospace">
            grounded
          </text>
          <path d="M100 84 L114 98 M114 84 L100 98" stroke={C.red} strokeWidth={2.5} strokeLinecap="round" />
          <text x={92} y={118} fontSize={6.5} fill={C.red} fontFamily="monospace">
            quarantine
          </text>
        </motion.g>
      </g>
    ),
  },
  {
    key: "normalise",
    n: "05",
    title: "Normalise",
    blurb: "₹7,225 crore and Rs. 72,251 mn are one number.",
    detail:
      "Units, scales, currencies and fiscal periods resolve to canonical form. FY24 and 'year ended March 31, 2024' are the same twelve months; the IMF's CY2024 is not.",
    accent: C.amber,
    art: (a) => (
      <g>
        <text x={16} y={44} fontSize={7} fill={C.dim} fontFamily="monospace">
          ₹7,225 crore
        </text>
        <text x={16} y={60} fontSize={7} fill={C.dim} fontFamily="monospace">
          Rs. 72,251 mn
        </text>
        <motion.path
          d="M78 40 C 96 40, 96 58, 110 58"
          stroke={C.amber}
          strokeWidth={1.2}
          fill="none"
          initial={false}
          animate={{ pathLength: a ? 1 : 0.15, opacity: a ? 1 : 0.3 }}
          transition={{ duration: 0.5 }}
        />
        <motion.path
          d="M84 56 C 98 56, 98 58, 110 58"
          stroke={C.amber}
          strokeWidth={1.2}
          fill="none"
          initial={false}
          animate={{ pathLength: a ? 1 : 0.15, opacity: a ? 1 : 0.3 }}
          transition={{ duration: 0.5, delay: 0.08 }}
        />
        <motion.text
          x={16}
          y={92}
          fontSize={7.5}
          fill={C.ink}
          fontFamily="monospace"
          initial={false}
          animate={{ opacity: a ? 1 : 0.2 }}
        >
          7.22510e10 INR
        </motion.text>
        <text x={16} y={110} fontSize={6} fill={C.faint} fontFamily="monospace">
          compared at the coarser precision
        </text>
      </g>
    ),
  },
  {
    key: "canon",
    n: "06",
    title: "Canonicalise",
    blurb: "An ontology grown at runtime, never enumerated.",
    detail:
      "New labels merge, create, or go to a model to adjudicate. When unsure and alone it keeps them separate — a wrong merge invents relationships; a duplicate only misses them.",
    accent: C.accent,
    art: (a) => (
      <g>
        {[
          [34, 40, "Delhivery Ltd"],
          [34, 96, "Delhivery Limited"],
        ].map(([x, y, t], i) => (
          <g key={i}>
            <circle cx={x as number} cy={y as number} r={7} fill="#0f1113" stroke={C.line} strokeWidth={1.5} />
            <text x={(x as number) + 13} y={(y as number) + 3} fontSize={6} fill={C.faint} fontFamily="monospace">
              {t}
            </text>
          </g>
        ))}
        <motion.circle
          cx={96}
          cy={68}
          r={10}
          fill="#0f1113"
          stroke={C.accent}
          strokeWidth={2}
          initial={false}
          animate={{ opacity: a ? 1 : 0.25, scale: a ? 1 : 0.8 }}
        />
        <motion.g initial={false} animate={{ opacity: a ? 0.9 : 0.15 }}>
          <path d="M41 42 L88 64" stroke={C.accent} strokeWidth={1} />
          <path d="M41 94 L88 74" stroke={C.accent} strokeWidth={1} />
        </motion.g>
        <text x={80} y={96} fontSize={6} fill={C.dim} fontFamily="monospace">
          one node
        </text>
      </g>
    ),
  },
  {
    key: "compare",
    n: "07",
    title: "Compare",
    blurb: "The verdict is derived, not prompted.",
    detail:
      "Scopes identical and values agree → corroboration. Identical and disagree → contradiction. Differ on exactly one axis → reconciled, and that axis is the explanation.",
    accent: C.amber,
    art: (a) => (
      <g>
        {[
          ["corroborates", C.green, 38],
          ["contradicts", C.red, 66],
          ["reconciled · period", C.amber, 94],
        ].map(([t, col, y], i) => (
          <motion.g
            key={i}
            initial={false}
            animate={{ opacity: a ? 1 : 0.22, x: a ? 0 : -6 }}
            transition={{ delay: a ? i * 0.08 : 0, duration: 0.3 }}
          >
            <rect x={16} y={(y as number) - 10} width={100} height={18} rx={3} fill="#0f1113" stroke={col as string} strokeOpacity={0.45} />
            <circle cx={26} cy={y as number} r={3} fill={col as string} />
            <text x={34} y={(y as number) + 3} fontSize={6.5} fill={col as string} fontFamily="monospace">
              {t}
            </text>
          </motion.g>
        ))}
      </g>
    ),
  },
];

function Plate({
  stage,
  index,
  progress,
  count,
}: {
  stage: Stage;
  index: number;
  progress: MotionValue<number>;
  count: number;
}) {
  // Plates stay stacked through the first fifth of the scroll, then fan apart
  // along an isometric axis. The reader sees a solid object become a system.
  const spread = useTransform(progress, [0.04, 0.42], [0, 1], { clamp: true });
  const y = useTransform(spread, (s) => s * index * 118 - index * 5);
  const x = useTransform(spread, (s) => s * index * 34);
  const rot = useTransform(spread, [0, 1], [0, -1.2]);

  // Which plate is "open" — scaled up, full opacity, detail revealed.
  const focus = useTransform(progress, (p) => {
    const t = (p - 0.34) / 0.6;
    return Math.max(0, 1 - Math.abs(t * (count - 1) - index) * 0.85);
  });
  const opacity = useTransform(focus, (f) => 0.3 + f * 0.7);
  const scale = useTransform(focus, (f) => 0.96 + f * 0.06);

  // A MotionValue read during render never re-renders, so the artwork needs a
  // real subscription. Only the boolean crosses into React state — the
  // continuous values stay on the compositor where they belong.
  const [activeNow, setActiveNow] = useState(false);
  useMotionValueEvent(focus, "change", (f) => {
    const next = f > 0.55;
    setActiveNow((prev) => (prev === next ? prev : next));
  });

  return (
    <motion.div
      className="absolute left-1/2 top-0"
      style={{ x, y, rotate: rot, opacity, scale, zIndex: count - index }}
    >
      <div className="-translate-x-1/2">
        <div
          className="flex items-start gap-5 rounded-md border p-4 backdrop-blur-sm"
          style={{
            borderColor: activeNow ? stage.accent + "66" : "var(--line)",
            background: "color-mix(in srgb, #131517 92%, transparent)",
            width: 520,
            boxShadow: activeNow
              ? `0 18px 50px -20px ${stage.accent}55`
              : "0 10px 30px -22px #000",
          }}
        >
          <svg width={132} height={140} viewBox="0 0 132 140" className="shrink-0">
            {stage.art(activeNow)}
          </svg>

          <div className="min-w-0 pt-1">
            <div className="flex items-baseline gap-2">
              <span className="num text-[10px]" style={{ color: stage.accent }}>
                {stage.n}
              </span>
              <h3 className="m-0 text-[15px] font-semibold tracking-tight">
                {stage.title}
              </h3>
            </div>
            <p className="mt-1 mb-2 text-[12px]" style={{ color: "var(--ink-dim)" }}>
              {stage.blurb}
            </p>
            <motion.p
              className="m-0 text-[11px] leading-relaxed"
              style={{ color: "var(--ink-faint)" }}
              animate={{ opacity: activeNow ? 1 : 0, height: activeNow ? "auto" : 0 }}
              transition={{ duration: 0.28 }}
            >
              {stage.detail}
            </motion.p>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

export function PipelineExploded() {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ["start start", "end end"],
  });

  const headingOpacity = useTransform(scrollYProgress, [0, 0.08], [1, 0]);
  const railHeight = useTransform(scrollYProgress, [0.04, 0.95], ["0%", "100%"]);

  return (
    <div ref={ref} style={{ height: "520vh" }} className="relative">
      <div className="sticky top-[41px] h-[calc(100vh-41px)] overflow-hidden">
        {/* A hairline rail that fills as the reader descends — the only progress
            indicator, and it doubles as the spine the plates hang from. */}
        <div
          className="absolute left-8 top-16 bottom-16 w-px"
          style={{ background: "var(--line)" }}
        >
          <motion.div
            className="w-px"
            style={{ height: railHeight, background: "var(--accent)" }}
          />
        </div>

        <motion.div
          className="pointer-events-none absolute inset-x-0 top-24 text-center"
          style={{ opacity: headingOpacity }}
        >
          <p className="label mb-2">the pipeline, taken apart</p>
          <p className="mx-auto max-w-md text-[12px]" style={{ color: "var(--ink-faint)" }}>
            Scroll to separate the layers.
          </p>
        </motion.div>

        <div className="relative mx-auto h-full" style={{ paddingTop: "14vh" }}>
          {STAGES.map((s, i) => (
            <Plate
              key={s.key}
              stage={s}
              index={i}
              progress={scrollYProgress}
              count={STAGES.length}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
