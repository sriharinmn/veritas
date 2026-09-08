"use client";

import type { DocumentSummary } from "@/lib/api";

/**
 * Choose a company, then a document within it.
 *
 * The nearest thing this system has to a project, and it is *derived* rather
 * than declared: two facts are only ever compared when they are about the same
 * subject, so the documents that can meaningfully meet are exactly the ones the
 * ontology put under one entity node. Asking somebody to file their uploads
 * into folders would be asking them to restate, by hand, a grouping the system
 * has already worked out from the cover pages.
 *
 * That has a consequence worth being honest about on screen: uploading a filing
 * for a company nothing else here mentions gives you a company of one, and it
 * will have no cross-document comparisons — not because the upload failed, but
 * because there is nothing for it to meet.
 */
export function ScopePicker({
  docs,
  entity,
  onEntity,
  document,
  onDocument,
}: {
  docs: DocumentSummary[];
  entity: string;
  onEntity: (id: string) => void;
  document: string;
  onDocument: (id: string) => void;
}) {
  const companies = new Map<string, { name: string; count: number }>();
  for (const d of docs) {
    if (!d.entity_id) continue;
    const seen = companies.get(d.entity_id);
    if (seen) seen.count += 1;
    else companies.set(d.entity_id, { name: d.entity ?? "—", count: 1 });
  }

  const within = entity ? docs.filter((d) => d.entity_id === entity) : docs;

  return (
    <>
      {companies.size > 1 && (
        <select
          value={entity}
          onChange={(e) => {
            onEntity(e.target.value);
            onDocument(""); // a document from the old company would filter to nothing
          }}
          aria-label="Show one company"
          className="sheet rounded px-2 py-1.5 text-[13.5px] outline-none"
          style={{ color: "var(--ink-soft)" }}
        >
          <option value="">All companies</option>
          {[...companies.entries()]
            .sort((a, b) => b[1].count - a[1].count)
            .map(([id, c]) => (
              <option key={id} value={id}>
                {c.name} ({c.count})
              </option>
            ))}
        </select>
      )}

      <select
        value={document}
        onChange={(e) => onDocument(e.target.value)}
        aria-label="Show one document"
        className="sheet rounded px-2 py-1.5 text-[13.5px] outline-none"
        style={{ color: "var(--ink-soft)" }}
      >
        <option value="">{entity ? "All of its documents" : "All documents"}</option>
        {within.map((d) => (
          <option key={d.id} value={d.id}>
            {d.filename.replace(/^\d+-/, "").replace(/\.pdf$/, "")}
          </option>
        ))}
      </select>
    </>
  );
}
