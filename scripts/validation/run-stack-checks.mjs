// Runs lint / typecheck / test / build commands for each application project
// that exists (see "stacks" in governance/governance.config.json). When no project
// exists the result is SKIPPED, which is deliberately not a pass.
//
// Usage: node scripts/validation/run-stack-checks.mjs --kinds lint,typecheck [--list] [--root <dir>]
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { ROOT, EXIT, exists, isMain, argValue } from '../lib/fsutil.mjs';
import { loadConfig } from '../lib/config.mjs';

/** Decide which commands would run. Pure, so it can be unit-tested. */
export function planChecks(root, cfg, kinds) {
  const detected = [];
  const plan = [];
  for (const stack of cfg.stacks ?? []) {
    if (!exists(root, stack.detect)) continue;
    detected.push(stack.name);
    for (const kind of kinds) {
      for (const cmd of stack[kind] ?? []) plan.push({ stack: stack.name, kind, cwd: stack.cwd ?? '.', cmd });
    }
  }
  return { detected, plan };
}

const shellQuote = (s) => (/^[\w./:=@%+-]+$/.test(s) ? s : JSON.stringify(s));

function runOne(root, item) {
  const cwd = path.join(root, item.cwd);
  // Commands come from the committed config file, never from user input. A shell is needed on
  // Windows to resolve npm.cmd and similar launchers.
  const line = item.cmd.map(shellQuote).join(' ');
  const r = spawnSync(line, { cwd, shell: true, stdio: 'inherit' });
  return r.status === 0;
}

function main() {
  const argv = process.argv.slice(2);
  const root = argValue(argv, '--root') ?? ROOT;
  const kinds = (argValue(argv, '--kinds', 'lint,typecheck') ?? '').split(',').filter(Boolean);
  const cfg = loadConfig(root);
  const { detected, plan } = planChecks(root, cfg, kinds);

  if (detected.length === 0) {
    console.log(`run-stack-checks (${kinds.join(',')}): SKIPPED. No application project exists yet (looked for: ${(cfg.stacks ?? []).map((s) => s.detect).join(', ')}).`);
    process.exit(EXIT.SKIPPED);
  }
  if (argv.includes('--list')) {
    for (const p of plan) console.log(`${p.stack} [${p.kind}] (${p.cwd}): ${p.cmd.join(' ')}`);
    process.exit(EXIT.PASS);
  }

  let failed = 0;
  for (const item of plan) {
    console.log(`> ${item.stack} [${item.kind}] ${item.cmd.join(' ')}`);
    if (!runOne(root, item)) {
      failed += 1;
      console.error(`FAILED: ${item.stack} [${item.kind}] ${item.cmd.join(' ')} (is the toolchain installed?)`);
    }
  }
  process.exit(failed === 0 ? EXIT.PASS : EXIT.FAIL);
}

if (isMain(import.meta.url)) main();
