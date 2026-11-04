// Tiny YAML-front-matter reader for the governance workflow and role files.
// Supports only flat "key: value" pairs, which is all those files use.

export function parseFrontmatter(text) {
  const t = text.replace(/\r\n/g, '\n');
  if (!t.startsWith('---\n')) return { data: {}, body: t };
  const end = t.indexOf('\n---', 4);
  if (end === -1) return { data: {}, body: t };

  const block = t.slice(4, end);
  const body = t.slice(end + 4).replace(/^\n/, '');
  const data = {};
  for (const line of block.split('\n')) {
    const m = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
    if (!m) continue;
    let value = m[2].trim();
    if (value.startsWith('"') && value.endsWith('"') && value.length >= 2) {
      try {
        value = JSON.parse(value);
      } catch {
        value = value.slice(1, -1);
      }
    } else if (value.startsWith("'") && value.endsWith("'") && value.length >= 2) {
      value = value.slice(1, -1);
    }
    data[m[1]] = value;
  }
  return { data, body };
}
