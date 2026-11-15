/**
 * Reading helpers for the Markdown of a learning book shown on the Notion approval card.
 *
 * Pure functions, no I/O. They mirror what the server measures (`describe_markdown` in
 * `backend/app/automation/book_markdown.py`): headings only count outside code fences, a
 * `[toc]` line is the Notion contents block (not text), and nothing deeper than `###` exists.
 */

export interface OutlineItem {
  readonly level: 1 | 2 | 3;
  readonly text: string;
}

export interface BookFacts {
  readonly words: number;
  readonly chapters: number;
  readonly codeBlocks: number;
  readonly tables: number;
  readonly equations: number;
  readonly outline: readonly OutlineItem[];
}

const FENCE = /^\s*(`{3,}|~{3,})/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const TOC_LINE = /^\s*\[(?:toc|table of contents)\]\s*$/i;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$/;

/** Each line with whether it is inside a fenced code block (the fence lines included). */
function lineStates(markdown: string): { line: string; code: boolean }[] {
  const out: { line: string; code: boolean }[] = [];
  let marker = "";
  for (const line of markdown.replace(/\r\n/g, "\n").split("\n")) {
    const fence = FENCE.exec(line);
    if (marker !== "") {
      out.push({ line, code: true });
      const first = fence?.[1];
      if (first && first[0] === marker[0] && first.length >= marker.length) {
        marker = "";
      }
    } else if (fence) {
      marker = fence[1] ?? "";
      out.push({ line, code: true });
    } else {
      out.push({ line, code: false });
    }
  }
  return out;
}

/** The headings of the book, in order (`#`, `##`, `###`), outside code fences. */
export function outlineOf(markdown: string): OutlineItem[] {
  const items: OutlineItem[] = [];
  for (const { line, code } of lineStates(markdown)) {
    if (code) continue;
    const match = HEADING.exec(line);
    if (match) {
      items.push({
        level: Math.min(match[1]?.length ?? 1, 3) as 1 | 2 | 3,
        text: match[2] ?? "",
      });
    }
  }
  return items;
}

/** Words (code is not counted), chapters, code blocks, tables, equations and the outline. */
export function bookFacts(markdown: string): BookFacts {
  const outline = outlineOf(markdown);
  let words = 0;
  let codeBlocks = 0;
  let tables = 0;
  let equations = 0;
  let wasCode = false;
  let inEquation = false;
  let previous = "";
  for (const { line, code } of lineStates(markdown)) {
    if (code) {
      if (!wasCode) codeBlocks += 1;
      wasCode = true;
      continue;
    }
    wasCode = false;
    const trimmed = line.trim();
    if (inEquation) {
      inEquation = !trimmed.endsWith("$$");
      continue;
    }
    if (trimmed.startsWith("$$")) {
      equations += 1;
      inEquation = !(trimmed.length > 2 && trimmed.endsWith("$$"));
      continue;
    }
    if (TABLE_SEPARATOR.test(line) && previous.includes("|")) tables += 1;
    if (!TOC_LINE.test(line)) {
      words += line
        .replace(/[`*_>#|-]+/g, " ")
        .split(/\s+/)
        .filter(Boolean).length;
    }
    previous = line;
  }
  return {
    words,
    chapters: outline.filter(
      (h) => h.level === 1 && /^Chapter \d+/.test(h.text),
    ).length,
    codeBlocks,
    tables,
    equations,
    outline,
  };
}

/** The first `count` lines (the contents marker left out), for the card's short preview. */
export function firstLines(markdown: string, count: number): string {
  return markdown
    .replace(/\r\n/g, "\n")
    .split("\n")
    .filter((line) => !TOC_LINE.test(line))
    .slice(0, count)
    .join("\n");
}

/**
 * The book cut into pieces at each level-1 heading (a chapter), so a long book can be shown a few
 * chapters at a time. Text before the first heading is the first piece. Fences are respected: a
 * `#` inside a code block never starts a piece.
 */
export function chapterPieces(markdown: string): string[] {
  const pieces: string[] = [];
  let current: string[] = [];
  for (const { line, code } of lineStates(markdown)) {
    if (TOC_LINE.test(line) && !code) continue;
    if (!code && /^#\s+\S/.test(line) && current.some((l) => l.trim() !== "")) {
      pieces.push(current.join("\n"));
      current = [];
    }
    current.push(line);
  }
  if (current.some((l) => l.trim() !== "")) pieces.push(current.join("\n"));
  return pieces;
}
