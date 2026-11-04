// Syntax-checks JSON and YAML configuration, and the YAML front matter of agent
// instruction files (skills, rules, prompts, subagents, workflows, roles).
// YAML needs the pinned "yaml" dev dependency; without it YAML is reported as SKIPPED, not passed.
//
// Usage: node scripts/validation/check-config-syntax.mjs [--root <dir>]
import { ROOT, EXIT, walk, readText, isMain, argValue } from '../lib/fsutil.mjs';
import { matchesAny } from '../lib/glob.mjs';

const FRONTMATTER_GLOBS = [
  '.claude/**/*.md',
  '.agents/**/*.md',
  '.cursor/**/*.mdc',
  '.github/prompts/*.md',
  '.github/instructions/*.md',
  'governance/workflows/*.md',
  'governance/roles/*.md',
];

async function loadYaml() {
  try {
    const mod = await import('yaml');
    return mod.default ?? mod;
  } catch {
    return null;
  }
}

function frontmatterBlock(text) {
  const t = text.replace(/\r\n/g, '\n');
  if (!t.startsWith('---\n')) return null;
  const end = t.indexOf('\n---', 4);
  return end === -1 ? null : t.slice(4, end);
}

export async function checkConfigSyntax(root = ROOT) {
  const YAML = await loadYaml();
  const errors = [];
  let jsonCount = 0;
  let yamlCount = 0;
  let yamlSkipped = 0;

  const yamlProblems = (text, label) => {
    const docs = YAML.parseAllDocuments(text);
    for (const d of docs) for (const e of d.errors) errors.push(`${label}: ${e.message.split('\n')[0]}`);
  };

  for (const file of walk(root)) {
    if (file.endsWith('.json')) {
      jsonCount += 1;
      try {
        JSON.parse(readText(root, file));
      } catch (e) {
        errors.push(`${file}: invalid JSON (${e.message})`);
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
  const root = argValue(process.argv.slice(2), '--root') ?? ROOT;
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
  console.log(`check-config-syntax: ${r.jsonCount} JSON and ${r.yamlCount} YAML items valid.`);
  process.exit(EXIT.PASS);
}

if (isMain(import.meta.url)) await main();
