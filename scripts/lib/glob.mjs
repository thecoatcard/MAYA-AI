// Minimal glob matcher for POSIX-style repository paths.
// Supports: ** (any depth), * (within a segment), ? (one char), {a,b}.
// Like .gitignore, a pattern with no "/" matches the basename at any depth.

const cache = new Map();
const escapeRe = (s) => s.replace(/[.+^$()|[\]\\]/g, '\\$&');

export function globToRegExp(glob) {
  let re = '';
  let i = 0;
  while (i < glob.length) {
    const c = glob[i];
    if (c === '*') {
      if (glob[i + 1] === '*') {
        if (glob[i + 2] === '/') {
          re += '(?:.*/)?';
          i += 3;
        } else {
          re += '.*';
          i += 2;
        }
      } else {
        re += '[^/]*';
        i += 1;
      }
    } else if (c === '?') {
      re += '[^/]';
      i += 1;
    } else if (c === '{') {
      const end = glob.indexOf('}', i);
      if (end === -1) {
        re += '\\{';
        i += 1;
      } else {
        const alts = glob
          .slice(i + 1, end)
          .split(',')
          .map(escapeRe);
        re += `(?:${alts.join('|')})`;
        i = end + 1;
      }
    } else {
      re += escapeRe(c);
      i += 1;
    }
  }
  return new RegExp(`^${re}$`);
}

/** True if `filePath` matches `glob`. */
export function matchGlob(filePath, glob) {
  let re = cache.get(glob);
  if (!re) {
    re = globToRegExp(glob);
    cache.set(glob, re);
  }
  const target = glob.includes('/') ? filePath : filePath.slice(filePath.lastIndexOf('/') + 1);
  return re.test(target);
}

export const matchesAny = (filePath, globs = []) => globs.some((g) => matchGlob(filePath, g));
