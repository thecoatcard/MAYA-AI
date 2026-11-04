// Orchestrates the individual checks for each stage. Git hooks and CI both call this,
// so every contributor and agent is held to the same checks.
//
// Usage: node scripts/validation/validate.mjs --stage <pre-commit|fast|full|pre-push|ci|lint|typecheck|security>
//                                            [--base <ref>] [--verbose]
// Result per check: PASS, FAIL, or SKIPPED. SKIPPED means the check did NOT run; it is never a pass.
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { ROOT, EXIT, walk, argValue, isMain } from '../lib/fsutil.mjs';
import * as G from '../lib/git.mjs';

const NODE = process.execPath;
const S = (rel) => path.join(ROOT, 'scripts', ...rel.split('/'));

function buildChecks(base, includeE2e) {
  // The Git-hook end-to-end tests take about a minute (they drive real commits and pushes), so local
  // stages skip them to keep hooks fast. CI runs them, and "npm test" always runs everything.
  const testFiles = walk(ROOT, 'scripts').filter(
    (f) => f.endsWith('.test.mjs') && (includeE2e || !f.endsWith('.e2e.test.mjs')),
  );
  const range = (name, script) => ({ name, needsBase: true, cmd: [NODE, S(script), '--range', base ?? ''] });
  return {
    sensitiveStaged: { name: 'sensitive-files (staged)', cmd: [NODE, S('validation/check-sensitive.mjs'), '--staged'] },
    secretsStaged: { name: 'secrets (staged)', cmd: [NODE, S('validation/check-secrets.mjs'), '--staged'] },
    whitespace: { name: 'whitespace/conflict markers', cmd: ['git', 'diff', '--cached', '--check'] },
    configSyntax: { name: 'config-syntax', strictInCi: true, cmd: [NODE, S('validation/check-config-syntax.mjs')] },
    workflows: { name: 'workflow-security', strictInCi: true, cmd: [NODE, S('validation/check-workflows.mjs')] },
    scriptSyntax: { name: 'script-syntax', cmd: [NODE, S('validation/check-script-syntax.mjs')] },
    agents: { name: 'agent-files', cmd: [NODE, S('governance/sync-agent-files.mjs'), '--check'] },
    docs: { name: 'documentation', cmd: [NODE, S('validation/check-docs.mjs')] },
    secretsAll: { name: 'secrets (repository)', cmd: [NODE, S('validation/check-secrets.mjs'), '--all'] },
    sensitiveTracked: { name: 'sensitive-files (repository)', cmd: [NODE, S('validation/check-sensitive.mjs'), '--tracked'] },
    stackFast: { name: 'stack lint+typecheck', cmd: [NODE, S('validation/run-stack-checks.mjs'), '--kinds', 'lint,typecheck'] },
    stackLint: { name: 'stack lint', cmd: [NODE, S('validation/run-stack-checks.mjs'), '--kinds', 'lint'] },
    stackTypecheck: { name: 'stack typecheck', cmd: [NODE, S('validation/run-stack-checks.mjs'), '--kinds', 'typecheck'] },
    stackSlow: { name: 'stack test+build', cmd: [NODE, S('validation/run-stack-checks.mjs'), '--kinds', 'test,build'] },
    unitTests: {
      name: includeE2e ? 'tests (scripts, incl. hook e2e)' : 'unit tests (scripts)',
      cmd: [NODE, '--test', ...testFiles.map((f) => path.join(ROOT, f))],
    },
    rangeSecrets: range('secrets (changes since base)', 'validation/check-secrets.mjs'),
    rangeSensitive: range('sensitive-files (changes since base)', 'validation/check-sensitive.mjs'),
    rangeCommits: range('commit messages', 'validation/check-commit-msg.mjs'),
    rangeRules: range('docs/ADR/changelog rules', 'validation/check-change-rules.mjs'),
  };
}

const FAST = ['configSyntax', 'workflows', 'scriptSyntax', 'agents', 'docs', 'secretsAll', 'sensitiveTracked', 'stackFast'];
const FULL = [...FAST, 'unitTests', 'stackSlow', 'rangeSecrets', 'rangeSensitive', 'rangeCommits', 'rangeRules'];

const STAGES = {
  'pre-commit': ['sensitiveStaged', 'secretsStaged', 'whitespace', 'configSyntax', 'workflows', 'scriptSyntax', 'agents', 'docs', 'stackFast'],
  fast: FAST,
  full: FULL,
  'pre-push': FULL,
  ci: FULL,
  lint: ['scriptSyntax', 'configSyntax', 'workflows', 'stackLint'],
  typecheck: ['stackTypecheck'],
  security: ['secretsAll', 'sensitiveTracked', 'workflows'],
};

function lastLine(text) {
  const lines = (text ?? '').split('\n').map((l) => l.trim()).filter(Boolean);
  return lines[lines.length - 1] ?? '';
}

function run(check, base, inCi) {
  if (check.needsBase && !base) {
    return inCi
      ? { status: 'FAIL', detail: 'no comparison base ref; pass --base <ref> (CI must fetch full history)' }
      : { status: 'SKIPPED', detail: 'no base branch to compare against (no upstream, origin/main or main)' };
  }
  const r = spawnSync(check.cmd[0], check.cmd.slice(1), { cwd: ROOT, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  const out = `${r.stdout ?? ''}${r.stderr ?? ''}`;
  if (r.error) return { status: 'FAIL', detail: `could not start: ${r.error.message}`, out };
  if (r.status === EXIT.PASS) {
    // node --test prints a TAP-style summary; report the pass count instead of its last line (a duration).
    const tests = /ℹ pass (\d+)/.exec(r.stdout ?? '');
    return { status: 'PASS', detail: tests ? `${tests[1]} tests passed` : lastLine(r.stdout), out };
  }
  if (r.status === EXIT.SKIPPED) {
    return inCi && check.strictInCi
      ? { status: 'FAIL', detail: `skipped in CI, which is not allowed: ${lastLine(r.stdout)}`, out }
      : { status: 'SKIPPED', detail: lastLine(r.stdout), out };
  }
  return { status: 'FAIL', detail: lastLine(r.stderr) || lastLine(r.stdout), out };
}

function main() {
  const argv = process.argv.slice(2);
  const stage = argValue(argv, '--stage', 'fast');
  const verbose = argv.includes('--verbose');
  const names = STAGES[stage];
  if (!names) {
    console.error(`Unknown stage "${stage}". Choose one of: ${Object.keys(STAGES).join(', ')}`);
    process.exit(EXIT.FAIL);
  }
  const inCi = stage === 'ci';
  const needsRange = names.some((n) => n.startsWith('range'));
  const base = needsRange ? G.resolveBase(argValue(argv, '--base'), ROOT) : null;

  console.log(`Validation stage: ${stage}${needsRange ? ` (base: ${base ?? 'none'})` : ''}`);
  const checks = buildChecks(base, inCi);
  const tally = { PASS: 0, FAIL: 0, SKIPPED: 0 };

  for (const key of names) {
    const check = checks[key];
    const res = run(check, base, inCi);
    tally[res.status] += 1;
    console.log(`${res.status.padEnd(8)} ${check.name}${res.detail ? `  - ${res.detail}` : ''}`);
    if ((res.status === 'FAIL' || verbose) && res.out) {
      console.log(res.out.trimEnd().split('\n').map((l) => `           ${l}`).join('\n'));
    }
  }

  console.log(`\n${tally.PASS} passed, ${tally.FAIL} failed, ${tally.SKIPPED} skipped (skipped checks did NOT run).`);
  process.exit(tally.FAIL === 0 ? EXIT.PASS : EXIT.FAIL);
}

if (isMain(import.meta.url)) main();
