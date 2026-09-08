"use client";

/**
 * Where a fact came from.
 *
 * Three tiers read these documents and they are not equally good, so a reader
 * comparing two facts deserves to know which one produced each. Rules alone
 * recover about 23% of what a model finds across the corpus — that is a real
 * difference in what a missing fact means, and hiding it behind a uniform
 * presentation would be the interface making a claim the system cannot support.
 *
 * Deliberately quiet: grey text, small, no colour. Colour on this page means a
 * verdict, and provenance must never compete with one for attention.
 */
export function Source({ extractor, className = "" }: { extractor?: string | null; className?: string }) {
  const { label, title } = describe(extractor);
  return (
    <span className={`source ${className}`} title={title}>
      {label}
    </span>
  );
}

function describe(extractor?: string | null): { label: string; title: string } {
  const raw = (extractor ?? "").toLowerCase();

  if (raw.startsWith("groq")) {
    return {
      label: `Groq · ${modelOf(extractor)}`,
      title: "Read by Groq's hosted model. Fastest and most capable tier, but rationed — roughly ten dense pages a day on the free plan.",
    };
  }
  if (raw.startsWith("ollama") || raw.startsWith("qwen") || raw.includes(":")) {
    return {
      label: `Local · ${modelOf(extractor)}`,
      title: "Read by a model running on this machine through Ollama. No rate limit, no cost, and nothing leaves the host.",
    };
  }
  if (raw.startsWith("deterministic") || raw.startsWith("rules")) {
    return {
      label: "Rules only",
      title: "Read without a model, by rules alone. Recovers about 23% of what the model tier finds, measured across the whole corpus — every fact still grounded, simply fewer of them.",
    };
  }
  return { label: "Source not recorded", title: "This fact carries no provenance." };
}

function modelOf(extractor?: string | null): string {
  const value = extractor ?? "";
  const after = value.includes(":") ? value.slice(value.indexOf(":") + 1) : value;
  // "openai/gpt-oss-120b" reads better as "gpt-oss-120b" at this size.
  return after.split("/").pop() || after;
}
