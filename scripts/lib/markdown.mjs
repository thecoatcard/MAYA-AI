// Markdown scanning helpers: separates prose from code so validators do not
// flag text that is only being quoted, and extracts links, inline code and headings.

const FENCE = /^\s{0,3}(`{3,}|~{3,})/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const INLINE_CODE = /(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g;
const LINK = /(!?)\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;

/** GitHub-style heading slug. */
export function slugify(text) {
  return text
    .toLowerCase()
    .replace(/`/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[^\p{L}\p{N}\s_-]/gu, '')
    .trim()
    .replace(/\s/g, '-');
}

/**
 * Analyse markdown text.
 * Returns:
 *  - lines:       prose lines with fenced code, inline code and HTML comments blanked out (line numbers preserved)
 *  - inlineCodes: [{ text, line }]
 *  - links:       [{ text, href, line, image }]
 *  - headings:    [{ text, level, slug, line }]  (slugs de-duplicated like GitHub: x, x-1, x-2)
 */
export function analyze(markdown) {
  const raw = markdown.replace(/\r\n/g, '\n').split('\n');
  const lines = [];
  const inlineCodes = [];
  const links = [];
  const headings = [];
  const slugCounts = new Map();

  let fence = null; // { char, len }
  let inComment = false;

  raw.forEach((original, idx) => {
    const lineNo = idx + 1;
    let line = original;

    if (fence) {
      const m = FENCE.exec(line);
      if (m && m[1][0] === fence.char && m[1].length >= fence.len) fence = null;
      lines.push('');
      return;
    }
    const open = FENCE.exec(line);
    if (open) {
      fence = { char: open[1][0], len: open[1].length };
      lines.push('');
      return;
    }

    if (inComment) {
      const end = line.indexOf('-->');
      if (end === -1) {
        lines.push('');
        return;
      }
      line = line.slice(end + 3);
      inComment = false;
    }
    line = line.replace(/<!--.*?-->/g, '');
    const start = line.indexOf('<!--');
    if (start !== -1) {
      line = line.slice(0, start);
      inComment = true;
    }

    const h = HEADING.exec(line);
    if (h) {
      const base = slugify(h[2]);
      const n = slugCounts.get(base) ?? 0;
      slugCounts.set(base, n + 1);
      headings.push({ text: h[2], level: h[1].length, slug: n === 0 ? base : `${base}-${n}`, line: lineNo });
    }

    line = line.replace(INLINE_CODE, (_all, _ticks, content) => {
      inlineCodes.push({ text: content.trim(), line: lineNo });
      return ' '.repeat(_all.length);
    });

    for (const m of line.matchAll(LINK)) {
      links.push({ image: m[1] === '!', text: m[2], href: m[3], line: lineNo });
    }
    lines.push(line);
  });

  return { lines, inlineCodes, links, headings };
}
