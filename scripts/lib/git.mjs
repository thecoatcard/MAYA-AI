// Thin, defensive wrappers around the git CLI. Arguments are always passed as an
// array to execFileSync (never through a shell), so values cannot inject commands.
import { execFileSync } from 'node:child_process';
import { ROOT } from './fsutil.mjs';

const BIG = 256 * 1024 * 1024;

export function git(args, { cwd = ROOT, input, allowFail = false } = {}) {
  try {
    return execFileSync('git', ['-c', 'core.quotepath=false', ...args], {
      cwd,
      input,
      encoding: 'utf8',
      maxBuffer: BIG,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
  } catch (err) {
    if (allowFail) return null;
    const detail = (err.stderr ? err.stderr.toString() : err.message).trim();
    throw new Error(`git ${args.join(' ')} failed: ${detail}`);
  }
}

const splitNul = (s) => (s ?? '').split('\0').filter(Boolean);

export const isRepo = (cwd = ROOT) => git(['rev-parse', '--is-inside-work-tree'], { cwd, allowFail: true })?.trim() === 'true';
export const hasCommits = (cwd = ROOT) => git(['rev-parse', '--verify', 'HEAD'], { cwd, allowFail: true }) !== null;
export const refExists = (ref, cwd = ROOT) => git(['rev-parse', '--verify', '--quiet', `${ref}^{commit}`], { cwd, allowFail: true }) !== null;

/** Staged (added/copied/modified/renamed) file paths. */
export const stagedFiles = (cwd = ROOT) => splitNul(git(['diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'], { cwd }));

/** Staged file content as a Buffer, or null. */
export function stagedBlob(filePath, cwd = ROOT) {
  try {
    return execFileSync('git', ['show', `:${filePath}`], { cwd, maxBuffer: BIG, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch {
    return null;
  }
}

/** Tracked plus untracked-but-not-ignored files. */
export const workingFiles = (cwd = ROOT) => splitNul(git(['ls-files', '-co', '--exclude-standard', '-z'], { cwd }));

/** Pick the comparison base: explicit ref, else upstream, else origin/main|master, main|master (unless identical to HEAD). */
export function resolveBase(explicit, cwd = ROOT) {
  if (!hasCommits(cwd)) return null;
  if (explicit) return refExists(explicit, cwd) ? explicit : null;
  const upstream = git(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'], { cwd, allowFail: true })?.trim();
  if (upstream) return upstream;
  const head = git(['rev-parse', 'HEAD'], { cwd }).trim();
  for (const ref of ['origin/main', 'origin/master', 'main', 'master']) {
    if (!refExists(ref, cwd)) continue;
    if (git(['rev-parse', ref], { cwd }).trim() === head) continue;
    return ref;
  }
  return null;
}

/** Files changed between base and HEAD: [{ status, path }]. Renames report the new path. */
export function rangeFiles(base, cwd = ROOT) {
  const parts = splitNul(git(['diff', '--name-status', '-z', `${base}...HEAD`], { cwd }));
  const out = [];
  for (let i = 0; i < parts.length; ) {
    const status = parts[i];
    if (status[0] === 'R' || status[0] === 'C') {
      out.push({ status: status[0], path: parts[i + 2] });
      i += 3;
    } else {
      out.push({ status: status[0], path: parts[i + 1] });
      i += 2;
    }
  }
  return out;
}

/** Commits in base..HEAD (oldest first, merges excluded) with their messages and touched files. */
export function rangeCommits(base, cwd = ROOT) {
  const shas = git(['rev-list', '--reverse', '--no-merges', `${base}..HEAD`], { cwd })
    .split('\n')
    .filter(Boolean);
  return shas.map((sha) => ({
    sha,
    author: git(['log', '-1', '--format=%an', sha], { cwd }).trim(),
    message: git(['log', '-1', '--format=%B', sha], { cwd }),
    files: splitNul(git(['diff-tree', '--no-commit-id', '--name-only', '-r', '--root', '-z', sha], { cwd })),
  }));
}

/** Lines added between base and HEAD: [{ path, line, text }]. */
export function rangeAddedLines(base, cwd = ROOT) {
  const diff = git(['diff', '-U0', '--no-color', `${base}...HEAD`], { cwd });
  const out = [];
  let current = null;
  let lineNo = 0;
  for (const line of diff.split('\n')) {
    if (line.startsWith('+++ ')) {
      current = line.startsWith('+++ b/') ? line.slice(6) : null;
    } else if (line.startsWith('@@')) {
      const m = /\+(\d+)/.exec(line);
      lineNo = m ? Number(m[1]) : 0;
    } else if (line.startsWith('+') && current) {
      out.push({ path: current, line: lineNo, text: line.slice(1) });
      lineNo += 1;
    }
  }
  return out;
}
