// One-command bootstrap: verifies the toolchain, installs the repository Git hooks,
// installs the pinned dev dependency if needed, and checks the agent files.
//
// Usage: npm run setup            (or: node scripts/governance/setup.mjs [--force])
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, EXIT, exists, isMain } from '../lib/fsutil.mjs';
import * as G from '../lib/git.mjs';

const HOOKS_DIR = 'scripts/hooks';
const MIN_NODE = 22;

function step(msg) {
  console.log(`\n== ${msg}`);
}

function main() {
  const force = process.argv.includes('--force');
  let ok = true;

  step('Toolchain');
  const major = Number(process.versions.node.split('.')[0]);
  if (major < MIN_NODE) {
    console.error(`Node.js ${MIN_NODE}+ is required; found ${process.versions.node}.`);
    process.exit(EXIT.FAIL);
  }
  console.log(`Node.js ${process.versions.node} OK`);

  step('Git repository');
  if (!G.isRepo(ROOT)) {
    console.error('This directory is not a Git repository. Run "git init -b main" first, then re-run setup.');
    process.exit(EXIT.FAIL);
  }
  console.log('Git repository OK');

  step('Git hooks');
  const current = G.git(['config', '--local', '--get', 'core.hooksPath'], { cwd: ROOT, allowFail: true })?.trim();
  if (current && current !== HOOKS_DIR && !force) {
    console.error(`core.hooksPath is already set to "${current}". Re-run with --force to replace it with "${HOOKS_DIR}".`);
    ok = false;
  } else {
    G.git(['config', '--local', 'core.hooksPath', HOOKS_DIR], { cwd: ROOT });
    console.log(`core.hooksPath = ${HOOKS_DIR}`);
    for (const hook of ['pre-commit', 'commit-msg', 'pre-push']) {
      const file = path.join(ROOT, HOOKS_DIR, hook);
      if (!fs.existsSync(file)) {
        console.error(`Missing hook file: ${HOOKS_DIR}/${hook}`);
        ok = false;
        continue;
      }
      if (process.platform !== 'win32') fs.chmodSync(file, 0o755);
    }
    if (process.platform === 'win32') console.log('Windows: Git for Windows runs these hooks with its bundled sh; no chmod needed.');
  }

  step('Dev dependencies');
  if (exists(ROOT, 'package-lock.json') && !exists(ROOT, 'node_modules/yaml')) {
    console.log('Installing pinned dev dependencies with "npm ci" ...');
    const r = spawnSync('npm ci', { cwd: ROOT, shell: true, stdio: 'inherit' });
    if (r.status !== 0) {
      console.error('"npm ci" failed.');
      ok = false;
    }
  } else {
    console.log('Dev dependencies already installed.');
  }

  step('Agent files');
  const r = spawnSync(process.execPath, [path.join(ROOT, 'scripts/governance/sync-agent-files.mjs'), '--check'], { cwd: ROOT, stdio: 'inherit' });
  if (r.status !== 0) {
    console.error('Agent files are out of date; run "npm run agents:sync".');
    ok = false;
  }

  console.log(
    ok
      ? '\nSetup complete. Hooks are active for this clone. Next: npm run validate\n' +
          'Note: hooks are a convenience and can be skipped; CI is the enforcing gate (governance/GIT_WORKFLOW.md).'
      : '\nSetup finished with problems (see above).',
  );
  process.exit(ok ? EXIT.PASS : EXIT.FAIL);
}

if (isMain(import.meta.url)) main();
