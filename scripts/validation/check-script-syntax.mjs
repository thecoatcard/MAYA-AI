// Syntax-checks every .mjs file under scripts/ with `node --check`.
//
// Usage: node scripts/validation/check-script-syntax.mjs [--root <dir>]
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, EXIT, walk, isMain, argValue } from '../lib/fsutil.mjs';

export function checkScriptSyntax(root = ROOT) {
  const files = walk(root, 'scripts').filter((f) => f.endsWith('.mjs'));
  const errors = [];

  // Hooks run from the working tree, so a CRLF shebang breaks them on macOS and Linux.
  for (const f of walk(root, 'scripts/hooks')) {
    if (fs.readFileSync(path.join(root, f), 'utf8').includes('\r')) {
      errors.push(`${f}: has CRLF line endings; shell hooks must use LF (see .gitattributes)`);
    }
  }
  for (const f of files) {
    const r = spawnSync(process.execPath, ['--check', path.join(root, f)], { encoding: 'utf8' });
    if (r.status !== 0) errors.push(`${f}: ${(r.stderr || '').split('\n').find((l) => l.trim()) ?? 'syntax error'}`);
  }
  return { errors, checked: files.length };
}

function main() {
  const root = argValue(process.argv.slice(2), '--root') ?? ROOT;
  const r = checkScriptSyntax(root);
  if (r.errors.length > 0) {
    console.error(`check-script-syntax: ${r.errors.length} problem(s):`);
    for (const e of r.errors) console.error(`  ${e}`);
    process.exit(EXIT.FAIL);
  }
  console.log(`check-script-syntax: ${r.checked} script(s) parse cleanly and hook files use LF.`);
  process.exit(EXIT.PASS);
}

if (isMain(import.meta.url)) main();
