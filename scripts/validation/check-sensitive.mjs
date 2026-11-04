// Blocks files that must never be committed (.env files, private keys, credential files).
//
// Usage: node scripts/validation/check-sensitive.mjs [--tracked | --staged | --range <base>] [--root <dir>]
import { ROOT, EXIT, isMain, argValue } from '../lib/fsutil.mjs';
import { loadConfig } from '../lib/config.mjs';
import { matchesAny } from '../lib/glob.mjs';
import * as G from '../lib/git.mjs';

/** True if the path is a file that must not be committed. */
export function isBlocked(file, cfg) {
  const s = cfg.sensitive;
  if (matchesAny(file, s.blockedExceptions ?? [])) return false;
  return matchesAny(file, s.blockedFiles ?? []);
}

/** Paths among `files` that sit under approval-required (guardrail) locations. */
export const approvalPathsTouched = (files, cfg) => files.filter((f) => matchesAny(f, cfg.sensitive.approvalRequiredPaths ?? []));

function main() {
  const argv = process.argv.slice(2);
  const root = argValue(argv, '--root') ?? ROOT;
  const cfg = loadConfig(root);

  let mode = 'tracked';
  let files;
  if (argv.includes('--staged')) {
    mode = 'staged';
    files = G.stagedFiles(root);
  } else if (argv.includes('--range')) {
    mode = 'range';
    files = G.rangeFiles(argValue(argv, '--range'), root)
      .filter((f) => f.status !== 'D')
      .map((f) => f.path);
  } else {
    files = G.isRepo(root) ? G.workingFiles(root) : [];
  }

  const bad = files.filter((f) => isBlocked(f, cfg));
  if (bad.length === 0) {
    console.log(`check-sensitive (${mode}): no blocked files.`);
    process.exit(EXIT.PASS);
  }
  console.error(`check-sensitive (${mode}): these files must not be committed:`);
  for (const f of bad) console.error(`  ${f}`);
  console.error('Remove them from the commit (git rm --cached <file>) and keep real secrets out of the repository.');
  process.exit(EXIT.FAIL);
}

if (isMain(import.meta.url)) main();
