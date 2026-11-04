// File-system helpers shared by the governance scripts. Zero dependencies.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** Repository root, derived from this file's location (scripts/lib -> repo root). */
export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

const SKIP_DIRS = new Set(['.git', 'node_modules']);

export const toPosix = (p) => p.split(path.sep).join('/');
export const normalizeEol = (s) => s.replace(/\r\n/g, '\n');

/** Recursively list files under `startRel` as sorted POSIX-style relative paths. Symlinks are skipped. */
export function walk(root, startRel = '', skip = SKIP_DIRS) {
  const out = [];
  const stack = [startRel];
  while (stack.length > 0) {
    const rel = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(path.join(root, rel), { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (entry.isSymbolicLink()) continue;
      const childRel = rel ? `${rel}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        if (!skip.has(entry.name)) stack.push(childRel);
      } else if (entry.isFile()) {
        out.push(childRel);
      }
    }
  }
  return out.sort();
}

export const exists = (root, rel) => fs.existsSync(path.join(root, rel));

export function isDirectory(root, rel) {
  try {
    return fs.statSync(path.join(root, rel)).isDirectory();
  } catch {
    return false;
  }
}

export const readText = (root, rel) => fs.readFileSync(path.join(root, rel), 'utf8');
export const readJson = (root, rel) => JSON.parse(readText(root, rel));

export function writeText(root, rel, content) {
  const abs = path.join(root, rel);
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  fs.writeFileSync(abs, content, 'utf8');
}

/** Heuristic binary check: a NUL byte in the first 8000 bytes. */
export const isBinary = (buf) => buf.subarray(0, 8000).includes(0);

/** Immediate subdirectory names of `rel` (empty list if it does not exist). */
export function listDirs(root, rel) {
  try {
    return fs
      .readdirSync(path.join(root, rel), { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
      .sort();
  } catch {
    return [];
  }
}

/** Process exit codes shared by the check scripts. */
export const EXIT = { PASS: 0, FAIL: 1, SKIPPED: 3 };

/** True when the module at `metaUrl` is the script node was started with (robust to Windows path casing). */
export function isMain(metaUrl) {
  if (!process.argv[1]) return false;
  try {
    return fs.realpathSync.native(path.resolve(process.argv[1])) === fs.realpathSync.native(fileURLToPath(metaUrl));
  } catch {
    return false;
  }
}

/** Value following `flag` in argv, or `fallback`. */
export function argValue(argv, flag, fallback = null) {
  const i = argv.indexOf(flag);
  return i !== -1 && i + 1 < argv.length ? argv[i + 1] : fallback;
}
