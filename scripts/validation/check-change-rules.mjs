// Diff-based documentation, ADR and changelog requirements. Compares the change
// range (base...HEAD) against the rules in governance/governance.config.json.
//
// Usage: node scripts/validation/check-change-rules.mjs --range <base> [--root <dir>]
import { ROOT, EXIT, isMain, argValue } from '../lib/fsutil.mjs';
import { loadConfig } from '../lib/config.mjs';
import { matchesAny } from '../lib/glob.mjs';
import { findWaiver, commitType, cleanMessage } from '../lib/commit.mjs';
import * as G from '../lib/git.mjs';

/**
 * Pure rule evaluation.
 * @param {{ files: {status:string,path:string}[], commits: {message:string}[], cfg: object }} input
 * @returns {{ errors: string[], waived: string[] }}
 */
export function evaluate({ files, commits, cfg }) {
  const rules = cfg.changeRules;
  const errors = [];
  const waived = [];
  const allPaths = files.map((f) => f.path);
  const messages = commits.map((c) => cleanMessage(c.message));
  const types = commits.map((c) => commitType(c.message, cfg)).filter(Boolean);

  // Only commits of non-exempt types trigger documentation/ADR rules.
  const exempt = new Set(rules.exemptCommitTypes ?? []);
  const nonExempt = types.length === 0 || types.some((t) => !exempt.has(t));
  const triggerPaths = nonExempt ? allPaths.filter((p) => !matchesAny(p, rules.ignoreChangedPaths ?? [])) : [];

  const satisfied = (globs, excludes = []) => allPaths.some((p) => matchesAny(p, globs) && !matchesAny(p, excludes));

  for (const rule of rules.docRules ?? []) {
    if (!triggerPaths.some((p) => matchesAny(p, rule.when))) continue;
    if (satisfied(rule.requireOneOf)) continue;
    const reason = findWaiver(messages, rule.trailer);
    if (reason) {
      waived.push(`${rule.id}: ${reason}`);
      continue;
    }
    errors.push(
      `[${rule.id}] ${rule.description}. Update one of: ${rule.requireOneOf.join(', ')} — or add a commit trailer ` +
        `"${rule.trailer}: not-required: <specific reason>".`,
    );
  }

  const adr = rules.adrRule;
  if (adr && types.some((t) => (adr.commitTypes ?? []).includes(t)) && triggerPaths.some((p) => matchesAny(p, adr.when))) {
    if (!satisfied(adr.requireOneOf, adr.excludeFiles ?? [])) {
      const reason = findWaiver(messages, adr.trailer);
      if (reason) waived.push(`${adr.id}: ${reason}`);
      else {
        errors.push(
          `[${adr.id}] ${adr.description}. Add or update an ADR in docs/adr/ — or add "${adr.trailer}: not-required: <specific reason>".`,
        );
      }
    }
  }

  const cl = rules.changelog;
  if (cl && types.some((t) => (cl.requiredForCommitTypes ?? []).includes(t)) && !allPaths.includes(cl.file)) {
    const reason = findWaiver(messages, cl.trailer);
    if (reason) waived.push(`changelog: ${reason}`);
    else {
      errors.push(
        `[changelog] feat/fix/security/perf changes need an entry in ${cl.file} — or add "${cl.trailer}: not-required: <specific reason>".`,
      );
    }
  }
  return { errors, waived };
}

function main() {
  const argv = process.argv.slice(2);
  const root = argValue(argv, '--root') ?? ROOT;
  const base = argValue(argv, '--range');
  if (!base) {
    console.error('usage: check-change-rules.mjs --range <base>');
    process.exit(EXIT.FAIL);
  }
  const cfg = loadConfig(root);
  const result = evaluate({ files: G.rangeFiles(base, root), commits: G.rangeCommits(base, root), cfg });

  for (const w of result.waived) console.log(`waived: ${w}`);
  if (result.errors.length === 0) {
    console.log(`check-change-rules (range ${base}): requirements satisfied.`);
    process.exit(EXIT.PASS);
  }
  console.error(`check-change-rules (range ${base}):`);
  for (const e of result.errors) console.error(`  - ${e}`);
  process.exit(EXIT.FAIL);
}

if (isMain(import.meta.url)) main();
