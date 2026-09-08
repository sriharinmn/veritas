import type { Claim } from "@/lib/api";

/**
 * The seven scope axes, rendered as chips.
 *
 * These are the whole argument of the system made visible: a reader looking at
 * two claims should be able to see at a glance which axes match and which do
 * not, because that is exactly what decides whether a difference in value is a
 * contradiction or an explanation. Unset axes are shown greyed rather than
 * hidden — "we do not know the basis" is information, and hiding it would let a
 * reader assume a match that was never established.
 */
export function ScopeChips({
  claim,
  className = "",
  highlight = [],
}: {
  claim: Claim;
  className?: string;
  highlight?: string[];
}) {
  const s = claim.scope;
  const axes: [string, string | null, string?][] = [
    ["period", s.period, s.period_start ? `${s.period_start} → ${s.period_end}` : undefined],
    ["basis", s.basis === "unknown" ? null : s.basis],
    ["segment", s.segment],
    ["geography", s.geography],
    ["accounting", s.accounting === "unknown" ? null : s.accounting],
    ["modality", s.modality === "unknown" ? null : s.modality],
    ["vintage", s.vintage],
  ];

  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      {axes.map(([name, value, title]) => {
        const differs = highlight.includes(name);
        return (
          <span
            key={name}
            title={title ?? `${name}: ${value ?? "not stated"}`}
            className="inline-flex items-baseline gap-1.5 rounded px-1.5 py-0.5 text-[13.5px]"
            style={{
              // The differing axis is the reason for the verdict, so it is the
              // one thing on the card that gets colour.
              background: differs
                ? "color-mix(in srgb, var(--reconcile) 18%, transparent)"
                : "var(--paper-sunk)",
              border: `1px solid ${differs ? "color-mix(in srgb, var(--reconcile) 45%, transparent)" : "var(--rule)"}`,
              color: value ? "var(--ink-soft)" : "var(--ink-faint)",
            }}
          >
            <span className="note" style={{ fontSize: 9 }}>
              {name}
            </span>
            <span className={value ? "num" : ""} style={{ opacity: value ? 1 : 0.5 }}>
              {value ?? "—"}
            </span>
          </span>
        );
      })}
    </div>
  );
}
