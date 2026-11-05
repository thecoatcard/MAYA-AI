/**
 * A small, dependency-free, safe Markdown subset for assistant replies.
 *
 * Supported: paragraphs, headings (# to ######), bold, italic, inline code,
 * fenced code blocks, ordered and unordered lists (nested), blockquotes, links
 * and horizontal rules. Everything else (tables, raw HTML, setext headings,
 * reference links, footnotes) is shown as plain text.
 *
 * Safety rules, all enforced here and covered by tests:
 *  - The parser returns a plain data tree. It never builds HTML strings, and the
 *    React renderer never uses dangerouslySetInnerHTML or innerHTML.
 *  - Raw HTML is just text, so `<script>` and `onerror=` stay inert text.
 *  - Links are only produced for http, https and mailto URLs without embedded
 *    credentials (see `sanitizeUrl`). Any other destination, including
 *    javascript:, data: and vbscript: and relative URLs, leaves the source text
 *    as plain text.
 *  - Images are never loaded: `![alt](url)` becomes the alt text.
 *  - Only the basic entities (&amp; &lt; &gt; &quot; &#39;) are decoded inside a
 *    link URL, so an entity can never spell out a scheme.
 *  - Parsing is bounded: no regular expression runs over unbounded input,
 *    nesting is capped, every scan has a size limit, a failure memo or a work
 *    budget (link and image attempts that fail), parentheses in bare URLs are
 *    counted once, and oversized input skips formatting entirely.
 *  - The renderer shows the real destination host next to a link (see
 *    `describeLinkTarget` and `linkLabelShowsTarget`) so the link text cannot
 *    pose as another site.
 */

export type Inline =
  | { type: "text"; value: string }
  | { type: "strong"; children: Inline[] }
  | { type: "em"; children: Inline[] }
  | { type: "code"; value: string }
  | { type: "link"; href: string; children: Inline[] }
  | { type: "br" };

export type Block =
  | { type: "paragraph"; children: Inline[] }
  | { type: "heading"; level: 1 | 2 | 3 | 4 | 5 | 6; children: Inline[] }
  | { type: "code"; language: string | null; value: string }
  | { type: "list"; ordered: boolean; start: number; items: Block[][] }
  | { type: "blockquote"; children: Block[] }
  | { type: "hr" };

/** Longer input is shown as plain paragraphs without any formatting. */
export const MAX_MARKDOWN_CHARS = 200_000;

const MAX_BLOCK_DEPTH = 6;
const MAX_INLINE_DEPTH = 6;
const MAX_URL_CHARS = 2048;
const MAX_TITLE_CHARS = 400;
/**
 * Characters scanned by link and image attempts that produced no link, per
 * inline parse. A hostile reply such as "[a](" repeated would otherwise rescan
 * up to 2048 characters from every opener. Once the budget is spent the rest of
 * that text is shown without link or image syntax, which is always safe.
 */
const MAX_FAILED_LINK_WORK = 40_000;
const ASCII_PUNCTUATION = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~";

/** Bidirectional formatting controls (embeddings, overrides, isolates). */
const BIDI_CONTROL = /[\u202a-\u202e\u2066-\u2069]/g;

/**
 * Show bidirectional control characters as visible `<U+202E>` markers. Used for
 * code, where an invisible override can make the code read differently from what
 * it does ("Trojan Source"). The marked text is also what the Copy button copies.
 */
export function markBidiControls(text: string): string {
  return text.replace(
    BIDI_CONTROL,
    (char) => `<U+${char.charCodeAt(0).toString(16).toUpperCase()}>`,
  );
}

/** Remove bidirectional control characters, for text that is shown inside a link. */
export function stripBidiControls(text: string): string {
  return text.replace(BIDI_CONTROL, "");
}

const BASIC_ENTITIES: Readonly<Record<string, string>> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
};

/**
 * Decode only the five basic entities, in one pass. Numeric and named entities
 * such as `&#106;` or `&colon;` are deliberately left alone, so an entity can
 * never be used to spell a scheme.
 */
export function decodeBasicEntities(value: string): string {
  if (!value.includes("&")) {
    return value;
  }
  return value.replace(
    /&(?:amp|lt|gt|quot|#39);/g,
    (entity) => BASIC_ENTITIES[entity] ?? entity,
  );
}

// ---------------------------------------------------------------------------
// URL safety
// ---------------------------------------------------------------------------

/**
 * Return a normalised, safe href for http, https and mailto URLs, or null.
 *
 * Parsing with the WHATWG URL parser first means obfuscated schemes such as
 * "JaVaScRiPt:", "java\tscript:" or a leading space are seen as what the browser
 * would see. Relative URLs and URLs with embedded credentials are rejected.
 */
export function sanitizeUrl(raw: string): string | null {
  if (raw.length === 0 || raw.length > MAX_URL_CHARS) {
    return null;
  }
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  if (
    url.protocol !== "http:" &&
    url.protocol !== "https:" &&
    url.protocol !== "mailto:"
  ) {
    return null;
  }
  if (url.username !== "" || url.password !== "") {
    return null;
  }
  return url.href;
}

const MAX_TARGET_DISPLAY_CHARS = 100;

/**
 * Where a sanitized link really goes, for display next to the link text: the host
 * (with a non-default port, in the ASCII form the browser uses) for http and
 * https, the address for mailto. Null when `href` is not a URL.
 */
export function describeLinkTarget(href: string): string | null {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  const target =
    url.protocol === "mailto:"
      ? url.pathname !== ""
        ? url.pathname
        : "mailto"
      : url.host;
  if (target === "") {
    return null;
  }
  return target.length > MAX_TARGET_DISPLAY_CHARS
    ? `${target.slice(0, MAX_TARGET_DISPLAY_CHARS)}\u2026`
    : target;
}

/** The plain text of inline nodes, capped so a huge link label stays cheap. */
export function inlinePlainText(
  nodes: readonly Inline[],
  limit = MAX_URL_CHARS + 1,
): string {
  let out = "";
  const visit = (list: readonly Inline[]) => {
    for (const node of list) {
      if (out.length >= limit) {
        return;
      }
      if (node.type === "text" || node.type === "code") {
        out += node.value;
      } else if (node.type === "strong" || node.type === "em") {
        visit(node.children);
      } else if (node.type === "link") {
        visit(node.children);
      } else {
        out += " ";
      }
    }
  };
  visit(nodes);
  return out.slice(0, limit);
}

/**
 * True when the visible link text is itself the destination: one http(s) URL
 * (no credentials, no spaces) on the same host as `href`, or the mailto address.
 * Only then is the host not repeated next to the link; in every other case,
 * including a label that names a different host, the real host is shown.
 */
export function linkLabelShowsTarget(label: string, href: string): boolean {
  const text = label.trim();
  if (text === "" || text.length > MAX_URL_CHARS) {
    return false;
  }
  for (let k = 0; k < text.length; k += 1) {
    if (text.charCodeAt(k) <= 32) {
      return false;
    }
  }
  let target: URL;
  try {
    target = new URL(href);
  } catch {
    return false;
  }
  if (target.protocol === "mailto:") {
    const lower = text.toLowerCase();
    return (
      lower === target.href.toLowerCase() ||
      (target.pathname !== "" && lower === target.pathname.toLowerCase())
    );
  }
  if (!/^https?:\/\//i.test(text)) {
    return false;
  }
  try {
    const shown = new URL(text);
    return (
      shown.username === "" &&
      shown.password === "" &&
      shown.host === target.host
    );
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export function parseMarkdown(source: string): Block[] {
  if (source.length === 0) {
    return [];
  }
  if (source.length > MAX_MARKDOWN_CHARS) {
    return plainBlocks(source);
  }
  const lines = source.split(/\r\n|\r|\n/).map(normalizeLeadingTabs);
  return parseBlocks(lines, 0);
}

function plainBlocks(source: string): Block[] {
  const blocks: Block[] = [];
  let current: Inline[] = [];
  for (const line of source.split(/\r\n|\r|\n/)) {
    if (isBlank(line)) {
      if (current.length > 0) {
        blocks.push({ type: "paragraph", children: current });
        current = [];
      }
      continue;
    }
    if (current.length > 0) {
      current.push({ type: "br" });
    }
    current.push({ type: "text", value: line });
  }
  if (current.length > 0) {
    blocks.push({ type: "paragraph", children: current });
  }
  return blocks;
}

// ---------------------------------------------------------------------------
// Line helpers (no regular expressions over unbounded input)
// ---------------------------------------------------------------------------

function normalizeLeadingTabs(line: string): string {
  let k = 0;
  let column = 0;
  let sawTab = false;
  while (k < line.length) {
    const code = line.charCodeAt(k);
    if (code === 32) {
      column += 1;
    } else if (code === 9) {
      sawTab = true;
      column += 4 - (column % 4);
    } else {
      break;
    }
    k += 1;
  }
  return sawTab ? " ".repeat(column) + line.slice(k) : line;
}

function isBlank(line: string): boolean {
  for (let k = 0; k < line.length; k += 1) {
    const code = line.charCodeAt(k);
    if (code !== 32 && code !== 9) {
      return false;
    }
  }
  return true;
}

function leadingSpaces(line: string): number {
  let n = 0;
  while (n < line.length && line.charCodeAt(n) === 32) {
    n += 1;
  }
  return n;
}

function isDigit(ch: string): boolean {
  return ch >= "0" && ch <= "9" && ch.length === 1;
}

interface Fence {
  char: string;
  length: number;
  indent: number;
  language: string | null;
}

function sanitizeLanguage(info: string): string | null {
  let end = 0;
  while (end < info.length && info.charCodeAt(end) > 32) {
    end += 1;
  }
  const word = info.slice(0, end);
  if (word.length === 0 || word.length > 24) {
    return null;
  }
  for (let k = 0; k < word.length; k += 1) {
    const code = word.charCodeAt(k);
    const ok =
      (code >= 48 && code <= 57) ||
      (code >= 65 && code <= 90) ||
      (code >= 97 && code <= 122) ||
      code === 95 ||
      code === 43 ||
      code === 35 ||
      code === 46 ||
      code === 45;
    if (!ok) {
      return null;
    }
  }
  return word.toLowerCase();
}

function matchFenceOpen(
  line: string,
  indent: number,
  maxIndent: number,
): Fence | null {
  if (indent > maxIndent) {
    return null;
  }
  const ch = line.charAt(indent);
  if (ch !== "`" && ch !== "~") {
    return null;
  }
  let p = indent;
  while (line.charAt(p) === ch) {
    p += 1;
  }
  const length = p - indent;
  if (length < 3) {
    return null;
  }
  const info = line.slice(p).trim();
  if (ch === "`" && info.includes("`")) {
    return null;
  }
  return { char: ch, length, indent, language: sanitizeLanguage(info) };
}

function isFenceClose(line: string, fence: Fence): boolean {
  const indent = leadingSpaces(line);
  let p = indent;
  while (line.charAt(p) === fence.char) {
    p += 1;
  }
  if (p - indent < fence.length) {
    return false;
  }
  return isBlank(line.slice(p));
}

function matchHeading(
  line: string,
  indent: number,
): { level: 1 | 2 | 3 | 4 | 5 | 6; text: string } | null {
  if (indent > 3 || line.charAt(indent) !== "#") {
    return null;
  }
  let p = indent;
  while (line.charAt(p) === "#") {
    p += 1;
  }
  const level = p - indent;
  if (level > 6) {
    return null;
  }
  const after = line.charAt(p);
  if (after !== "" && after !== " " && after !== "\t") {
    return null;
  }
  let text = line.slice(p).trim();
  let end = text.length;
  while (end > 0 && text.charAt(end - 1) === "#") {
    end -= 1;
  }
  if (end < text.length && (end === 0 || /[ \t]/.test(text.charAt(end - 1)))) {
    text = text.slice(0, end).trimEnd();
  }
  return { level: level as 1 | 2 | 3 | 4 | 5 | 6, text };
}

function isThematicBreak(line: string, indent: number): boolean {
  if (indent > 3) {
    return false;
  }
  const ch = line.charAt(indent);
  if (ch !== "-" && ch !== "*" && ch !== "_") {
    return false;
  }
  let count = 0;
  for (let k = indent; k < line.length; k += 1) {
    const c = line.charAt(k);
    if (c === ch) {
      count += 1;
    } else if (c !== " " && c !== "\t") {
      return false;
    }
  }
  return count >= 3;
}

interface ListMarker {
  ordered: boolean;
  number: number;
  indent: number;
  contentCol: number;
  content: string;
}

function matchListMarker(line: string, indent: number): ListMarker | null {
  const first = line.charAt(indent);
  let markerEnd: number;
  let ordered = false;
  let number = 0;
  if (first === "-" || first === "*" || first === "+") {
    markerEnd = indent + 1;
  } else if (isDigit(first)) {
    let j = indent;
    while (j < line.length && j - indent <= 9 && isDigit(line.charAt(j))) {
      j += 1;
    }
    if (j - indent > 9) {
      return null;
    }
    const delimiter = line.charAt(j);
    if (delimiter !== "." && delimiter !== ")") {
      return null;
    }
    ordered = true;
    number = Number.parseInt(line.slice(indent, j), 10);
    markerEnd = j + 1;
  } else {
    return null;
  }
  const after = line.charAt(markerEnd);
  if (after !== " " && after !== "\t") {
    return null;
  }
  let k = markerEnd;
  while (line.charAt(k) === " " || line.charAt(k) === "\t") {
    k += 1;
  }
  if (k >= line.length) {
    return null;
  }
  const spaces = k - markerEnd;
  const contentCol = markerEnd + (spaces >= 5 ? 1 : spaces);
  return {
    ordered,
    number,
    indent,
    contentCol,
    content: line.slice(contentCol),
  };
}

/** True when a non-blank line would start a new block (interrupts a paragraph). */
function startsBlock(line: string, maxFenceIndent: number): boolean {
  const indent = leadingSpaces(line);
  if (matchFenceOpen(line, indent, maxFenceIndent) !== null) {
    return true;
  }
  if (indent <= 3) {
    if (matchHeading(line, indent) !== null) {
      return true;
    }
    if (isThematicBreak(line, indent)) {
      return true;
    }
    if (line.charAt(indent) === ">") {
      return true;
    }
  }
  const marker = matchListMarker(line, indent);
  return marker !== null && (!marker.ordered || marker.number === 1);
}

// ---------------------------------------------------------------------------
// Block parser
// ---------------------------------------------------------------------------

function parseBlocks(lines: readonly string[], depth: number): Block[] {
  const blocks: Block[] = [];
  const flat = depth >= MAX_BLOCK_DEPTH;
  const maxFenceIndent = depth === 0 ? 3 : 7;
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (isBlank(line)) {
      i += 1;
      continue;
    }
    if (flat) {
      // Nested too deeply: keep the text, drop the structure.
      const [paragraph, next] = collectParagraph(
        lines,
        i,
        true,
        maxFenceIndent,
      );
      blocks.push(paragraph);
      i = next;
      continue;
    }
    const indent = leadingSpaces(line);

    const fence = matchFenceOpen(line, indent, maxFenceIndent);
    if (fence !== null) {
      const [block, next] = parseFence(lines, i, fence);
      blocks.push(block);
      i = next;
      continue;
    }

    const heading = matchHeading(line, indent);
    if (heading !== null) {
      blocks.push({
        type: "heading",
        level: heading.level,
        children: parseInlines(heading.text, 0, false),
      });
      i += 1;
      continue;
    }

    if (isThematicBreak(line, indent)) {
      blocks.push({ type: "hr" });
      i += 1;
      continue;
    }

    if (indent <= 3 && line.charAt(indent) === ">") {
      const [block, next] = parseBlockquote(lines, i, depth, maxFenceIndent);
      blocks.push(block);
      i = next;
      continue;
    }

    if (matchListMarker(line, indent) !== null) {
      const [block, next] = parseList(lines, i, depth, maxFenceIndent);
      blocks.push(block);
      i = next;
      continue;
    }

    const [paragraph, next] = collectParagraph(lines, i, false, maxFenceIndent);
    blocks.push(paragraph);
    i = next;
  }
  return blocks;
}

function collectParagraph(
  lines: readonly string[],
  start: number,
  flat: boolean,
  maxFenceIndent: number,
): [Block, number] {
  const parts: string[] = [lines[start].trim()];
  let i = start + 1;
  while (i < lines.length) {
    const line = lines[i];
    if (isBlank(line) || (!flat && startsBlock(line, maxFenceIndent))) {
      break;
    }
    parts.push(line.trim());
    i += 1;
  }
  const text = parts.join("\n");
  return [
    {
      type: "paragraph",
      children: flat
        ? [{ type: "text", value: text }]
        : parseInlines(text, 0, false),
    },
    i,
  ];
}

function parseFence(
  lines: readonly string[],
  start: number,
  fence: Fence,
): [Block, number] {
  const body: string[] = [];
  let i = start + 1;
  while (i < lines.length) {
    const line = lines[i];
    if (isFenceClose(line, fence)) {
      i += 1;
      break;
    }
    body.push(line.slice(Math.min(leadingSpaces(line), fence.indent)));
    i += 1;
  }
  return [
    { type: "code", language: fence.language, value: body.join("\n") },
    i,
  ];
}

function parseBlockquote(
  lines: readonly string[],
  start: number,
  depth: number,
  maxFenceIndent: number,
): [Block, number] {
  const inner: string[] = [];
  let i = start;
  while (i < lines.length) {
    const line = lines[i];
    const indent = leadingSpaces(line);
    if (indent <= 3 && line.charAt(indent) === ">") {
      let c = indent + 1;
      if (line.charAt(c) === " ") {
        c += 1;
      }
      inner.push(line.slice(c));
      i += 1;
      continue;
    }
    const previous = inner[inner.length - 1];
    if (
      !isBlank(line) &&
      !startsBlock(line, maxFenceIndent) &&
      previous !== undefined &&
      !isBlank(previous)
    ) {
      inner.push(line);
      i += 1;
      continue;
    }
    break;
  }
  return [{ type: "blockquote", children: parseBlocks(inner, depth + 1) }, i];
}

function parseList(
  lines: readonly string[],
  start: number,
  depth: number,
  maxFenceIndent: number,
): [Block, number] {
  const firstLine = lines[start];
  const first = matchListMarker(firstLine, leadingSpaces(firstLine));
  if (first === null) {
    // Callers check the marker first; keep the types honest.
    return [{ type: "paragraph", children: [] }, start + 1];
  }
  const items: Block[][] = [];
  let i = start;
  while (i < lines.length) {
    const line = lines[i];
    const marker = matchListMarker(line, leadingSpaces(line));
    if (
      marker === null ||
      marker.ordered !== first.ordered ||
      marker.indent > first.indent + 1
    ) {
      break;
    }
    const itemLines: string[] = [marker.content];
    let j = i + 1;
    let blanks = 0;
    while (j < lines.length) {
      const next = lines[j];
      if (isBlank(next)) {
        blanks += 1;
        j += 1;
        continue;
      }
      const indent = leadingSpaces(next);
      const nestedMarker = matchListMarker(next, indent);
      const nested =
        indent >= marker.contentCol ||
        (nestedMarker !== null && indent >= marker.indent + 2);
      if (nested) {
        for (let b = 0; b < blanks; b += 1) {
          itemLines.push("");
        }
        blanks = 0;
        itemLines.push(next.slice(Math.min(indent, marker.contentCol)));
        j += 1;
        continue;
      }
      const previous = itemLines[itemLines.length - 1];
      if (
        blanks === 0 &&
        nestedMarker === null &&
        !startsBlock(next, maxFenceIndent) &&
        !isBlank(previous)
      ) {
        itemLines.push(next.trim());
        j += 1;
        continue;
      }
      break;
    }
    items.push(parseBlocks(itemLines, depth + 1));
    i = j;
  }
  return [
    {
      type: "list",
      ordered: first.ordered,
      start: first.ordered ? first.number : 1,
      items,
    },
    i,
  ];
}

// ---------------------------------------------------------------------------
// Inline parser
// ---------------------------------------------------------------------------

function isWhitespaceChar(ch: string): boolean {
  return (
    ch === "" ||
    ch === " " ||
    ch === "\t" ||
    ch === "\n" ||
    ch === "\r" ||
    ch === "\u00a0"
  );
}

const WORD_CHAR = /[\p{L}\p{N}]/u;

function isWordChar(ch: string): boolean {
  return ch !== "" && WORD_CHAR.test(ch);
}

/** Map each "[" index to its matching "]" index in one linear pass. */
function pairBrackets(text: string): Map<number, number> {
  const pairs = new Map<number, number>();
  const stack: number[] = [];
  for (let k = 0; k < text.length; k += 1) {
    const code = text.charCodeAt(k);
    if (code === 92) {
      k += 1;
    } else if (code === 91) {
      stack.push(k);
    } else if (code === 93) {
      const open = stack.pop();
      if (open !== undefined) {
        pairs.set(open, k);
      }
    }
  }
  return pairs;
}

interface Destination {
  url: string;
  end: number;
}

/** Work spent on link and image attempts that did not produce a link. */
interface LinkBudget {
  failedWork: number;
}

/**
 * Parse `url`, `<url>` and an optional "title" up to the closing ")". Every
 * failed attempt is charged to `budget` by the number of characters it scanned.
 */
function parseDestination(
  text: string,
  start: number,
  budget: LinkBudget,
): Destination | null {
  const fail = (reached: number): null => {
    budget.failedWork += Math.max(1, reached - start);
    return null;
  };
  const n = text.length;
  let p = start;
  while (p < n && (text.charAt(p) === " " || text.charAt(p) === "\n")) {
    p += 1;
  }
  let url: string;
  if (text.charAt(p) === "<") {
    const limit = Math.min(n, p + 1 + MAX_URL_CHARS);
    let q = p + 1;
    while (
      q < limit &&
      text.charAt(q) !== ">" &&
      text.charAt(q) !== "<" &&
      text.charAt(q) !== "\n"
    ) {
      q += 1;
    }
    if (text.charAt(q) !== ">") {
      return fail(q);
    }
    url = text.slice(p + 1, q);
    p = q + 1;
  } else {
    const limit = Math.min(n, p + MAX_URL_CHARS + 1);
    let depth = 0;
    let q = p;
    while (q < limit) {
      const code = text.charCodeAt(q);
      if (code <= 32) {
        break;
      }
      if (code === 92) {
        q += 2;
        continue;
      }
      if (code === 40) {
        depth += 1;
      } else if (code === 41) {
        if (depth === 0) {
          break;
        }
        depth -= 1;
      }
      q += 1;
    }
    if (q - p > MAX_URL_CHARS) {
      return fail(q);
    }
    url = text.slice(p, q);
    p = q;
  }
  while (p < n && (text.charAt(p) === " " || text.charAt(p) === "\n")) {
    p += 1;
  }
  const quote = text.charAt(p);
  if (quote === '"' || quote === "'" || quote === "(") {
    const closer = quote === "(" ? ")" : quote;
    const limit = Math.min(n, p + 1 + MAX_TITLE_CHARS);
    let q = p + 1;
    while (q < limit && text.charAt(q) !== closer) {
      if (text.charAt(q) === "\\") {
        q += 1;
      }
      q += 1;
    }
    if (text.charAt(q) !== closer) {
      return fail(q);
    }
    p = q + 1;
    while (p < n && (text.charAt(p) === " " || text.charAt(p) === "\n")) {
      p += 1;
    }
  }
  if (text.charAt(p) !== ")" || url === "") {
    return fail(p);
  }
  return { url, end: p + 1 };
}

/**
 * Trim trailing sentence punctuation and unbalanced ")" from a bare URL. The
 * parentheses are counted once and then adjusted as characters are trimmed, so
 * a run of thousands of ")" costs linear time, not quadratic.
 */
function trimUrlEnd(text: string, start: number, end: number): number {
  let open = 0;
  let close = 0;
  for (let k = start; k < end; k += 1) {
    const c = text.charCodeAt(k);
    if (c === 40) {
      open += 1;
    } else if (c === 41) {
      close += 1;
    }
  }
  let e = end;
  while (e > start) {
    const ch = text.charAt(e - 1);
    if (".,;:!?'\"*_~".includes(ch)) {
      e -= 1;
    } else if (ch === ")" && close > open) {
      close -= 1;
      e -= 1;
    } else {
      break;
    }
  }
  return e;
}

function mergeText(nodes: Inline[]): Inline[] {
  const merged: Inline[] = [];
  for (const node of nodes) {
    const last = merged[merged.length - 1];
    if (node.type === "text" && last !== undefined && last.type === "text") {
      merged[merged.length - 1] = {
        type: "text",
        value: last.value + node.value,
      };
    } else if (node.type !== "text" || node.value !== "") {
      merged.push(node);
    }
  }
  return merged;
}

function parseInlines(text: string, depth: number, inLink: boolean): Inline[] {
  if (text === "") {
    return [];
  }
  if (depth > MAX_INLINE_DEPTH) {
    return [{ type: "text", value: text }];
  }
  const out: Inline[] = [];
  const n = text.length;
  let i = 0;
  let textStart = 0;
  const failedDelimiters = new Set<string>();
  const failedTicks = new Set<number>();
  const budget: LinkBudget = { failedWork: 0 };
  let brackets: Map<number, number> | null = null;

  const flushText = (end: number) => {
    if (end > textStart) {
      out.push({ type: "text", value: text.slice(textStart, end) });
    }
  };
  const emit = (node: Inline, next: number) => {
    flushText(i);
    out.push(node);
    i = next;
    textStart = next;
  };

  const findTickCloser = (from: number, length: number): number => {
    if (failedTicks.has(length)) {
      return -1;
    }
    let p = from;
    while (p < n) {
      const at = text.indexOf("`", p);
      if (at === -1) {
        break;
      }
      let end = at + 1;
      while (end < n && text.charCodeAt(end) === 96) {
        end += 1;
      }
      if (end - at === length) {
        return at;
      }
      p = end;
    }
    failedTicks.add(length);
    return -1;
  };

  /** Index where the closing delimiter run (exactly `length` chars) starts. */
  const findEmphasisCloser = (
    from: number,
    ch: string,
    length: number,
  ): number => {
    let p = from;
    while (p < n) {
      const c = text.charAt(p);
      if (c === "\\") {
        p += 2;
        continue;
      }
      if (c === ch) {
        let end = p + 1;
        while (end < n && text.charAt(end) === ch) {
          end += 1;
        }
        const run = end - p;
        const closes =
          p > from &&
          !isWhitespaceChar(text.charAt(p - 1)) &&
          (ch === "*" || !isWordChar(text.charAt(end)));
        if (closes && (run === length || (run === 3 && length < 3))) {
          return p + (run - length);
        }
        p = end;
        continue;
      }
      p += 1;
    }
    return -1;
  };

  while (i < n) {
    const code = text.charCodeAt(i);

    if (code === 92) {
      // Backslash escape or hard break.
      const next = text.charAt(i + 1);
      if (next !== "" && ASCII_PUNCTUATION.includes(next)) {
        emit({ type: "text", value: next }, i + 2);
      } else if (next === "\n") {
        emit({ type: "br" }, i + 2);
      } else {
        i += 1;
      }
      continue;
    }

    if (code === 10) {
      emit({ type: "br" }, i + 1);
      continue;
    }

    if (code === 96) {
      let run = 1;
      while (text.charCodeAt(i + run) === 96) {
        run += 1;
      }
      const close = findTickCloser(i + run, run);
      if (close === -1) {
        i += run;
        continue;
      }
      let value = text.slice(i + run, close).replace(/\n/g, " ");
      if (
        value.length > 2 &&
        value.startsWith(" ") &&
        value.endsWith(" ") &&
        value.trim() !== ""
      ) {
        value = value.slice(1, -1);
      }
      emit({ type: "code", value }, close + run);
      continue;
    }

    if (code === 42 || code === 95) {
      const ch = code === 42 ? "*" : "_";
      let run = 1;
      while (text.charCodeAt(i + run) === code) {
        run += 1;
      }
      const prev = text.charAt(i - 1);
      const next = text.charAt(i + run);
      const canOpen =
        run <= 3 &&
        !isWhitespaceChar(next) &&
        (ch === "*" || !isWordChar(prev));
      const key = `${ch}${run}`;
      if (canOpen && !failedDelimiters.has(key)) {
        const close = findEmphasisCloser(i + run, ch, run);
        if (close !== -1) {
          const children = parseInlines(
            text.slice(i + run, close),
            depth + 1,
            inLink,
          );
          let node: Inline;
          if (run === 1) {
            node = { type: "em", children };
          } else if (run === 2) {
            node = { type: "strong", children };
          } else {
            node = {
              type: "strong",
              children: [{ type: "em", children }],
            };
          }
          emit(node, close + run);
          continue;
        }
        failedDelimiters.add(key);
      }
      i += run;
      continue;
    }

    if (code === 33 && text.charAt(i + 1) === "[") {
      // Image: never loaded; the alt text is shown as plain text.
      brackets ??= pairBrackets(text);
      const close = brackets.get(i + 1);
      if (
        close !== undefined &&
        text.charAt(close + 1) === "(" &&
        budget.failedWork <= MAX_FAILED_LINK_WORK
      ) {
        const destination = parseDestination(text, close + 2, budget);
        if (destination !== null) {
          const alt = text
            .slice(i + 2, close)
            .replace(/\n/g, " ")
            .trim();
          emit(
            { type: "text", value: alt === "" ? "[image]" : alt },
            destination.end,
          );
          continue;
        }
      }
      i += 1;
      continue;
    }

    if (code === 91 && !inLink) {
      brackets ??= pairBrackets(text);
      const close = brackets.get(i);
      if (
        close !== undefined &&
        text.charAt(close + 1) === "(" &&
        budget.failedWork <= MAX_FAILED_LINK_WORK
      ) {
        const destination = parseDestination(text, close + 2, budget);
        if (destination !== null) {
          // Only the basic entities are decoded; "&#106;avascript:" and
          // "javascript&colon;" stay as written and are refused below.
          const href = sanitizeUrl(decodeBasicEntities(destination.url));
          if (href !== null) {
            const label = text.slice(i + 1, close);
            const children = parseInlines(label, depth + 1, true);
            emit(
              {
                type: "link",
                href,
                children:
                  children.length > 0
                    ? children
                    : [{ type: "text", value: destination.url }],
              },
              destination.end,
            );
            continue;
          }
          budget.failedWork += destination.end - close;
        }
      }
      i += 1;
      continue;
    }

    if (code === 60 && !inLink) {
      // Autolink <https://...>. Any other "<" is plain text (raw HTML is inert).
      const limit = Math.min(n, i + 1 + MAX_URL_CHARS);
      let p = i + 1;
      while (p < limit) {
        const c = text.charCodeAt(p);
        if (c === 62 || c === 60 || c <= 32) {
          break;
        }
        p += 1;
      }
      if (p < limit && text.charCodeAt(p) === 62 && p > i + 1) {
        const raw = text.slice(i + 1, p);
        const href = /^(?:https?:|mailto:)/i.test(raw)
          ? sanitizeUrl(raw)
          : null;
        if (href !== null) {
          emit(
            { type: "link", href, children: [{ type: "text", value: raw }] },
            p + 1,
          );
          continue;
        }
      }
      i += 1;
      continue;
    }

    if ((code === 104 || code === 72) && !inLink) {
      // Bare http(s) URL.
      if (i === 0 || !isWordChar(text.charAt(i - 1))) {
        const head = text.slice(i, i + 8).toLowerCase();
        const schemeLength = head.startsWith("https://")
          ? 8
          : head.startsWith("http://")
            ? 7
            : 0;
        if (schemeLength > 0) {
          const limit = Math.min(n, i + MAX_URL_CHARS);
          let p = i + schemeLength;
          while (p < limit) {
            const c = text.charCodeAt(p);
            if (c <= 32 || c === 60 || c === 62 || c === 34 || c === 96) {
              break;
            }
            p += 1;
          }
          const end = trimUrlEnd(text, i, p);
          const raw = text.slice(i, end);
          const href = end > i + schemeLength ? sanitizeUrl(raw) : null;
          if (href !== null) {
            emit(
              { type: "link", href, children: [{ type: "text", value: raw }] },
              end,
            );
            continue;
          }
        }
      }
      i += 1;
      continue;
    }

    i += 1;
  }
  flushText(n);
  return mergeText(out);
}
