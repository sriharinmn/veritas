/**
 * Copy the PDF.js worker that react-pdf actually uses into public/.
 *
 * The worker and the API must be the same version of pdf.js or every render
 * fails with "The API version X does not match the Worker version Y", and the
 * evidence pane — the whole point of the app — shows an error instead of a
 * page.
 *
 * That happened here. `public/pdf.worker.min.mjs` had been copied by hand from
 * the top-level pdfjs-dist (6.3.289) while react-pdf resolves its own nested
 * copy (5.4.296), so the two drifted apart silently the moment either was
 * upgraded. Copying by hand is the bug; the version number was only the
 * symptom.
 *
 * So the worker is resolved through react-pdf's own dependency rather than
 * named. Whatever react-pdf imports is what lands in public/, and an upgrade
 * cannot desynchronise them because nobody has to remember anything.
 *
 * Runs on `postinstall` and before `build`, and is safe to run at any time.
 */

import { createRequire } from "node:module";
import { copyFileSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, "..");

// Resolve pdfjs-dist the way react-pdf resolves it: from react-pdf's own
// location, so a nested install wins over a hoisted one.
const requireFromWeb = createRequire(join(web, "package.json"));
const reactPdfEntry = requireFromWeb.resolve("react-pdf");
const requireFromReactPdf = createRequire(reactPdfEntry);

const pdfjsPkgPath = requireFromReactPdf.resolve("pdfjs-dist/package.json");
const pdfjsDir = dirname(pdfjsPkgPath);
const version = JSON.parse(readFileSync(pdfjsPkgPath, "utf8")).version;

const source = join(pdfjsDir, "build", "pdf.worker.min.mjs");
const destDir = join(web, "public");
const dest = join(destDir, "pdf.worker.min.mjs");

mkdirSync(destDir, { recursive: true });
copyFileSync(source, dest);

console.log(`pdf.worker.min.mjs <- pdfjs-dist@${version} (as resolved by react-pdf)`);
