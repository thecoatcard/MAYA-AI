// Syntax-checks tracked JSON and YAML configuration, and the YAML front matter
// of tracked agent instruction files (skills, rules, prompts, subagents, workflows, roles).
// YAML needs the pinned "yaml" dev dependency; without it YAML is reported as SKIPPED, not passed.
//
// Usage: node scripts/validation/check-config-syntax.mjs [--root <dir>]
import fs from "node:fs";
import path from "node:path";
import { ROOT, EXIT, readText, isMain, argValue } from "../lib/fsutil.mjs";
import { matchesAny } from "../lib/glob.mjs";
import * as G from "../lib/git.mjs";

const FRONTMATTER_GLOBS = [
  ".claude/**/*.md",
  ".agents/**/*.md",
  ".cursor/**/*.mdc",
  ".github/prompts/*.md",
  ".github/instructions/*.md",
  "governance/workflows/*.md",
  "governance/roles/*.md",
];

async function loadYaml() {
  try {
    const mod = await import("yaml");
    return mod.default ?? mod;
  } catch {
    return null;
  }
}

function frontmatterBlock(text) {
  const t = text.replace(/\r\n/g, "\n");
  if (!t.startsWith("---\n")) return null;
  const end = t.indexOf("\n---", 4);
  return end === -1 ? null : t.slice(4, end);
}

function isRegularFileWithoutSymlinks(root, rel) {
  let current = path.resolve(root);
  try {
    if (fs.lstatSync(current).isSymbolicLink()) return false;
  } catch {
    return false;
  }

  const parts = rel.split("/");
  for (let i = 0; i < parts.length; i += 1) {
    current = path.join(current, parts[i]);
    let stat;
    try {
      stat = fs.lstatSync(current);
    } catch {
      return false;
    }
    if (stat.isSymbolicLink()) return false;
    if (i === parts.length - 1) return stat.isFile();
    if (!stat.isDirectory()) return false;
  }
  return false;
}

export async function checkConfigSyntax(root = ROOT) {
  const YAML = await loadYaml();
  const errors = [];
  let jsonCount = 0;
  let yamlCount = 0;
  let yamlSkipped = 0;

  const yamlProblems = (text, label) => {
    try {
      if (YAML.parseAllDocuments(text).some((doc) => doc.errors.length > 0)) {
        errors.push(`${label}: invalid YAML`);
      }
    } catch {
      errors.push(`${label}: invalid YAML`);
    }
  };

  const tracked = G.git(["ls-files", "--cached", "-z"], {
    cwd: root,
    allowFail: true,
  });
  if (tracked === null) {
    return {
      errors: ["unable to enumerate tracked files"],
      jsonCount,
      yamlCount,
      yamlSkipped,
    };
  }

  for (const file of tracked.split("\0").filter(Boolean)) {
    if (!isRegularFileWithoutSymlinks(root, file)) continue;
    if (file.endsWith(".json")) {
      jsonCount += 1;
      try {
        JSON.parse(readText(root, file));
      } catch {
        errors.push(`${file}: invalid JSON`);
      }
    } else if (/\.ya?ml$/.test(file)) {
      if (!YAML) {
        yamlSkipped += 1;
        continue;
      }
      yamlCount += 1;
      yamlProblems(readText(root, file), file);
    } else if (matchesAny(file, FRONTMATTER_GLOBS)) {
      const block = frontmatterBlock(readText(root, file));
      if (block === null) {
        errors.push(`${file}: missing YAML front matter`);
      } else if (!YAML) {
        yamlSkipped += 1;
      } else {
        yamlCount += 1;
        yamlProblems(block, `${file} (front matter)`);
      }
    }
  }
  return { errors, jsonCount, yamlCount, yamlSkipped };
}

async function main() {
  const root = argValue(process.argv.slice(2), "--root") ?? ROOT;
  const r = await checkConfigSyntax(root);
  if (r.errors.length > 0) {
    console.error(`check-config-syntax: ${r.errors.length} problem(s):`);
    for (const e of r.errors) console.error(`  ${e}`);
    process.exit(EXIT.FAIL);
  }
  if (r.yamlSkipped > 0) {
    console.log(
      `check-config-syntax: ${r.jsonCount} JSON files valid; ${r.yamlSkipped} YAML item(s) NOT checked ` +
        '(the "yaml" package is not installed; run "npm ci").',
    );
    process.exit(EXIT.SKIPPED);
  }
  console.log(
    `check-config-syntax: ${r.jsonCount} JSON and ${r.yamlCount} YAML items valid.`,
  );
  process.exit(EXIT.PASS);
}

if (isMain(import.meta.url)) await main();
