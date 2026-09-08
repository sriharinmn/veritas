"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type Capabilities, type Stats } from "@/lib/api";

/**
 * Named for what a reader wants, not for what the code is called.
 *
 * "Explorer", "Reconciliation" and "Ontology" describe the implementation.
 * Someone opening this for the first time is looking for the facts, the
 * comparisons between them, the vocabulary the system learned, and proof that
 * any of it is right — so those are the words.
 */
const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/case/1", label: "The four cases" },
  { href: "/explorer", label: "Facts" },
  { href: "/reconciliation", label: "Comparisons" },
  { href: "/ontology", label: "Vocabulary" },
  { href: "/evals", label: "Checks" },
  { href: "/upload", label: "Add a document" },
];

/**
 * The tier the system is running on is stated permanently, in the chrome.
 *
 * A reader must never have to guess whether they are looking at full extraction
 * or a degraded fallback — the difference is roughly half the facts on a page,
 * and a knowledge layer that hides which mode produced its output is asking to
 * be trusted on exactly the question it refuses to answer.
 */
export function Nav() {
  const pathname = usePathname();
  const [stats, setStats] = useState<Stats | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);

  useEffect(() => {
    const load = () => {
      api.stats().then(setStats).catch(() => {});
      api.capabilities().then(setCaps).catch(() => {});
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  const degraded = caps?.degraded ?? false;
  const tier = degraded ? "Rules only" : tierName(caps?.best_tier);

  return (
    <header
      className="sticky top-0 z-50 border-b"
      style={{ borderColor: "var(--rule)", background: "var(--paper)" }}
    >
      <div className="mx-auto flex h-14 max-w-[1180px] items-center gap-6 px-6">
        <Link href="/" className="flex items-baseline gap-2 no-underline">
          <span className="text-[17px] font-semibold tracking-tight">Veritas</span>
          <span className="hidden text-[13px] sm:inline" style={{ color: "var(--ink-faint)" }}>
            facts from filings
          </span>
        </Link>

        <nav className="flex flex-1 items-center gap-1 overflow-x-auto">
          {LINKS.map((l) => {
            const active =
              l.href === "/" ? pathname === "/" : pathname.startsWith(l.href.split("/1")[0]);
            return (
              <Link
                key={l.href}
                href={l.href}
                aria-current={active ? "page" : undefined}
                className="whitespace-nowrap rounded px-2.5 py-1.5 text-[13.5px] no-underline transition-colors"
                style={{
                  color: active ? "var(--ink)" : "var(--ink-soft)",
                  background: active ? "var(--paper-sunk)" : "transparent",
                  fontWeight: active ? 550 : 400,
                }}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-4">
          {stats && (
            <span className="fig hidden text-[13.5px] lg:inline" style={{ color: "var(--ink-faint)" }}>
              {stats.claims.toLocaleString("en-IN")} facts
            </span>
          )}
          <span
            className="badge"
            style={{
              // Amber, not red: running on rules is a working state a reader
              // must notice, not an error they cannot proceed past.
              ["--v" as string]: degraded ? "var(--reconcile)" : "var(--corroborate)",
            }}
            title={caps?.tiers.find((t) => t.available)?.detail ?? "Checking which tier is reachable…"}
          >
            {tier}
          </span>
        </div>
      </div>
    </header>
  );
}

function tierName(tier?: string): string {
  if (tier === "groq") return "Groq";
  if (tier === "ollama") return "Local model";
  if (tier === "deterministic") return "Rules only";
  return "Checking…";
}
