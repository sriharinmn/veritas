import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Vendored, minified, and not ours: public/pdf.worker.min.mjs is copied
    // out of react-pdf's own pdfjs-dist on install. Linting it drowned four
    // real errors in our code under 1,471 warnings about someone else's
    // minifier output.
    "public/**",
  ]),
]);

export default eslintConfig;
