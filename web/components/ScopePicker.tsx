"use client";

import { Define } from "@/components/Define";
import type { DocumentSummary } from "@/lib/api";

/**
 * Choose a subject, then a document about it.
 *
 * The nearest thing this system has to a project, and it is *derived* rather
 * than declared: two facts are only ever compared when they are about the same
 * subject, so the documents that can meaningfully meet are exactly the ones the
 * ontology has already put under one entity node. Asking somebody to file their
 * uploads into folders would be asking them to restate, by hand, a grouping the
 * cover pages already settled.
 *
 * **"Subject", not "company".** This shipped labelled "company" and the corpus
 * answered back: the Economic Survey and the IMF Article IV are about *India*,
 * and the RBI's annual report is about a central bank. A subject is whatever a
 * document is about — a company, a country, an institution — and calling it a
 * company was both wrong on screen and wrong about what the comparator does.
 *
 * There is a consequence worth being honest about here: uploading a filing
 * about a subject nothing else mentions gives you a subject of one, and it will
 * have no cross-document comparisons — not because the upload failed, but
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
  const subjects = new Map<string, { name: string; count: number }>();
  for (const d of docs) {
    if (!d.entity_id) continue;
    const seen = subjects.get(d.entity_id);
    if (seen) seen.count += 1;
    else subjects.set(d.entity_id, { name: d.entity ?? "—", count: 1 });
  }

  const within = entity ? docs.filter((d) => d.entity_id === entity) : docs;

  return (
    <>
      {subjects.size > 1 && (
        <span className="inline-flex items-center gap-1">
          <select
            value={entity}
            onChange={(e) => {
              onEntity(e.target.value);
              onDocument(""); // a document from the old subject would filter to nothing
            }}
            aria-label="Show one subject"
            className="sheet rounded px-2 py-1.5 text-[13.5px] outline-none"
            style={{ color: "var(--ink-soft)" }}
          >
            <option value="">All subjects</option>
            {[...subjects.entries()]
              .sort((a, b) => b[1].count - a[1].count)
              .map(([id, s]) => (
                <option key={id} value={id}>
                  {s.name} ({s.count})
                </option>
              ))}
          </select>
          <Define term="subject" />
        </span>
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
