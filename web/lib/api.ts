/**
 * The read API over the knowledge layer.
 *
 * Types mirror api/knowledge.py deliberately rather than being generated: the
 * surface is small, and hand-written types make the shape of a claim legible in
 * the editor to whoever reads this next.
 */

export const API =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8010";

export type Rect = { x0: number; y0: number; x1: number; y1: number };

export type Evidence = {
  document_id: string;
  page: number;
  char_start: number;
  char_end: number;
  quote: string;
  kind: "primary" | "inherited_context";
  rects: Rect[];
};

export type Claim = {
  id: string;
  subject: string;
  predicate: string;
  value: {
    raw: string;
    kind: string;
    canonical: string | null;
    currency: string | null;
    unit: string | null;
    is_range: boolean;
  };
  scope: {
    period: string | null;
    period_start: string | null;
    period_end: string | null;
    convention: string;
    basis: string;
    segment: string | null;
    geography: string | null;
    accounting: string;
    modality: string;
    vintage: string | null;
  };
  confidence: number;
  scale_inferred: boolean;
  extractor: string | null;
  evidence: Evidence[];
  document_id: string | null;
  page: number | null;
};

export type Relation =
  | "corroboration"
  | "contradiction"
  | "reconciled"
  | "ambiguous"
  | "unrelated";

export type Edge = {
  id: string;
  index: number;
  relation: Relation;
  axis: string | null;
  confidence: number;
  decided_by: "deterministic" | "llm";
  trace: string[];
  explanation: string | null;
  reason: string;
  cross_document: boolean;
  a: Claim;
  b: Claim;
};

export type DocumentSummary = {
  id: string;
  filename: string;
  entity: string | null;
  pages_processed: number;
  claims: number;
  quarantined: number;
  sha256: string;
  has_pdf: boolean;
};

export type Stats = {
  documents: number;
  claims: number;
  quarantined: number;
  grounding_pass_rate: number;
  entities: number;
  predicates: number;
  edges: number;
  relations: Record<Relation, number>;
  ingest_in_progress: boolean;
};

async function get<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, { cache: "no-store", ...init });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  stats: () => get<Stats>("/stats"),
  documents: () => get<DocumentSummary[]>("/documents"),
  capabilities: () => get<Capabilities>("/capabilities"),
  claims: (params: Record<string, string | number | undefined> = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== "")
        .map(([k, v]) => [k, String(v)]),
    );
    return get<{ total: number; items: Claim[] }>(`/claims?${qs}`);
  },
  claim: (id: string) =>
    get<{ claim: Claim; related: Edge[] }>(`/claims/${id}`),
  relations: (params: Record<string, string | number | boolean | undefined> = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== "")
        .map(([k, v]) => [k, String(v)]),
    );
    return get<{ total: number; counts: Record<Relation, number>; items: Edge[] }>(
      `/relations?${qs}`,
    );
  },
  ontology: (kind: "predicate" | "entity" = "predicate") =>
    get<Ontology>(`/ontology?kind=${kind}`),
  quarantine: () =>
    get<{ total: number; by_reason: Record<string, number>; items: unknown[] }>(
      "/quarantine",
    ),
  pdfUrl: (documentId: string) => `${API}/documents/${documentId}/file`,
};

export type Capabilities = {
  best_tier: string;
  degraded: boolean;
  tiers: { tier: string; available: boolean; detail: string; models: string[] }[];
};

export type Ontology = {
  stats: Record<string, number>;
  nodes: { id: string; label: string; aliases: string[]; alias_count: number }[];
  decisions: {
    query: string;
    action: string;
    similarity: number;
    nearest: string | null;
    describe: string;
    needs_review: boolean;
  }[];
};

/** Relation display metadata, defined once so every surface agrees. */
export const RELATION_META: Record<
  Relation,
  { label: string; className: string; blurb: string }
> = {
  corroboration: {
    label: "Corroborates",
    className: "v-corroboration",
    blurb: "Same scope, same value after normalisation.",
  },
  contradiction: {
    label: "Contradicts",
    className: "v-contradiction",
    blurb:
      "Same scope on every axis, different values — nothing explains the gap.",
  },
  reconciled: {
    label: "Reconciled",
    className: "v-reconciled",
    blurb: "Values differ, and exactly one scope axis explains why.",
  },
  ambiguous: {
    label: "Ambiguous",
    className: "v-ambiguous",
    blurb: "Two or more scope axes differ — escalated for adjudication.",
  },
  unrelated: {
    label: "Unrelated",
    className: "v-unrelated",
    blurb: "Different subject or predicate.",
  },
};

export function fmtInt(n: number | undefined): string {
  return n === undefined ? "—" : n.toLocaleString("en-IN");
}
