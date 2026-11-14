/**
 * Prints the WCAG contrast of every design-token pair as Markdown tables (both themes).
 *   node scripts/contrast-report.mjs          print the tables
 *   node scripts/contrast-report.mjs --check  exit 1 if any pair fails
 * The same data is enforced in CI by src/app/design-tokens.test.tsx.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { evaluate } from "./design-tokens.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(root, "..", "src", "app", "globals.css"), "utf8");
const rows = evaluate(css);

for (const theme of ["dark", "light"]) {
  console.log(`\n### ${theme === "dark" ? "Dark" : "Light"} theme\n`);
  console.log(
    "| Group | Pair | Foreground | Background | Ratio | Needs | Result |",
  );
  console.log("| :-- | :-- | :-- | :-- | --: | --: | :-- |");
  for (const row of rows.filter((r) => r.theme === theme)) {
    console.log(
      `| ${row.group} | ${row.label} | ${row.fg} | ${row.bg} | ${row.ratio.toFixed(2)}:1 | ${row.min}:1 | ${row.pass ? "Pass" : "FAIL"} |`,
    );
  }
}

const failed = rows.filter((r) => !r.pass);
console.log(`\n${rows.length - failed.length} of ${rows.length} pairs pass.`);
if (process.argv.includes("--check") && failed.length > 0) {
  for (const row of failed) {
    console.error(
      `FAIL ${row.theme}: ${row.label} ${row.ratio.toFixed(2)} < ${row.min}`,
    );
  }
  process.exit(1);
}
