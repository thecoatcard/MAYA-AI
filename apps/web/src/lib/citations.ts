/**
 * Clean citations for web-search answers.
 *
 * The model is told to cite as `[n]` only; MAYA appends a "Sources" list (see the backend
 * `app/services/citations.py`). Models still sometimes write the marker with quotes, doubled
 * brackets or an inline link, for example `"[2]" (www.msn.com)` or `["[4]"](https://site/a`. This
 * module repairs those shapes (also in partial, still-streaming text), and turns each `[n]` that
 * has a matching entry in the Sources list into a link to that source. A `[n]` with no matching
 * source stays plain text. A link is never invented: the destination only ever comes from the
 * Sources list the server built from the real search results.
 */

/** The line that opens the server-built sources list. */
export const SOURCES_MARKER = "**Sources**";

const QUOTES = "\"“”'";

// ["[4]"](https://site/a)  [[4]](url)  (also while the link is still being typed)
const QUOTED_LINK = new RegExp(
  `\\[\\s*[${QUOTES}]?\\s*\\[(\\d{1,3})\\]\\s*[${QUOTES}]?\\s*\\]\\([^)\\s]*\\)?`,
  "g",
);
// "[2]"
const QUOTED_MARKER = new RegExp(`[${QUOTES}]\\[(\\d{1,3})\\][${QUOTES}]`, "g");
// [4](https://site/a)
const NUMBER_LINK = /\[(\d{1,3})\]\(https?:\/\/[^)\s]*\)?/g;
// [2] (www.msn.com)
const HOST_NOTE =
  /\[(\d{1,3})\]\s*\((?:https?:\/\/)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:\/[^)\s]*)?\)/gi;
// a quoted or doubled marker cut off at the end of the streamed text
const DANGLING = new RegExp(
  `\\[\\s*[${QUOTES}]\\s*\\[\\d{0,3}\\]?\\s*[${QUOTES}]?\\s*\\]?\\(?[^)\\s]*$`,
);

/** The text with every broken citation shape reduced to a plain `[n]`. */
export function normaliseCitationMarkup(text: string): string {
  if (!text.includes("[")) return text;
  return text
    .replace(QUOTED_LINK, "[$1]")
    .replace(QUOTED_MARKER, "[$1]")
    .replace(NUMBER_LINK, "[$1]")
    .replace(HOST_NOTE, "[$1]")
    .replace(DANGLING, "");
}

export interface CiteSource {
  n: number;
  title: string;
  url: string;
}

const SOURCE_LINE = /^(\d{1,3})\.\s+\[(.+?)\]\((https?:\/\/[^)\s]+)\)/;

/** Splits the reply at the last Sources list and reads its entries. */
export function splitSources(content: string): {
  body: string;
  tail: string;
  sources: Map<number, CiteSource>;
} {
  const at = content.lastIndexOf(`\n${SOURCES_MARKER}`);
  const start = at >= 0 ? at + 1 : content.startsWith(SOURCES_MARKER) ? 0 : -1;
  const sources = new Map<number, CiteSource>();
  if (start < 0) return { body: content, tail: "", sources };
  const tail = content.slice(start);
  for (const line of tail.split("\n").slice(1)) {
    const found = SOURCE_LINE.exec(line.trim());
    if (found?.[1] && found[2] && found[3]) {
      const n = Number(found[1]);
      sources.set(n, { n, title: found[2], url: found[3] });
    }
  }
  return { body: content.slice(0, start), tail, sources };
}

function mapOutsideCode(
  text: string,
  change: (plain: string) => string,
): string {
  // Fenced blocks and inline code are left exactly as written.
  return text
    .split(/(```[\s\S]*?(?:```|$))/)
    .map((part, index) =>
      index % 2 === 1
        ? part
        : part
            .split(/(`[^`\n]*`)/)
            .map((piece, i) => (i % 2 === 1 ? piece : change(piece)))
            .join(""),
    )
    .join("");
}

/** The reply as it should be rendered: repaired markers, linked to the Sources list when it exists. */
export function prepareCitations(content: string): string {
  if (!content.includes("[")) return content;
  const { body, tail, sources } = splitSources(content);
  const cleaned = mapOutsideCode(body, (plain) => {
    const repaired = normaliseCitationMarkup(plain);
    if (sources.size === 0) return repaired;
    return repaired.replace(/\[(\d{1,3})\](?!\()/g, (whole, digits: string) => {
      const source = sources.get(Number(digits));
      return source ? `[[${digits}]](${source.url})` : whole;
    });
  });
  return cleaned + tail;
}

/** True for link text that is a citation marker, "[3]". */
export function isCitationLabel(label: string): boolean {
  return /^\[\d{1,3}\]$/.test(label);
}
