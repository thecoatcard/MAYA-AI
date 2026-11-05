/**
 * A small, dependency-free syntax highlighter for chat code blocks.
 *
 * Supports TypeScript and JavaScript, JSON, Python, Bash and SQL. Unknown
 * languages come back as one plain token. The scanner is a single left-to-right
 * pass that always advances, never uses a backtracking regular expression over
 * unbounded input, and the concatenation of all token texts always equals the
 * input exactly, so highlighting can never change or drop code.
 */

export type TokenType =
  "plain" | "keyword" | "string" | "comment" | "number" | "name" | "type";

export interface HighlightToken {
  type: TokenType;
  text: string;
}

export type HighlightLanguage = "js" | "json" | "python" | "bash" | "sql";

/** Longer code is shown without highlighting to keep the DOM small. */
export const MAX_HIGHLIGHT_CHARS = 60_000;

interface LanguageSpec {
  keywords: ReadonlySet<string>;
  caseInsensitive: boolean;
  lineComments: readonly string[];
  /** A line comment only starts at the line start or after whitespace (bash). */
  commentNeedsBoundary: boolean;
  blockComment: readonly [string, string] | null;
  quotes: string;
  /** Quote characters whose strings honour backslash escapes. */
  escapes: string;
  /** Quote characters whose strings may span lines. */
  multiline: string;
  tripleQuotes: boolean;
  /** Capitalised identifiers are types. */
  typeLike: boolean;
  /** `$NAME` and `${NAME}` are variables. */
  shellVariables: boolean;
  /** A string followed by ":" is a property name. */
  propertyKeys: boolean;
  /** Treat "$" as an identifier character. */
  dollarIdentifiers: boolean;
}

function words(list: string): ReadonlySet<string> {
  return new Set(list.split(" "));
}

const SPECS: Record<HighlightLanguage, LanguageSpec> = {
  js: {
    keywords: words(
      "abstract as async await break case catch class const continue debugger default delete do else enum export extends false finally for from function if implements import in instanceof interface keyof let namespace new null of private protected public readonly return satisfies static super switch this throw true try typeof undefined var void while with yield",
    ),
    caseInsensitive: false,
    lineComments: ["//"],
    commentNeedsBoundary: false,
    blockComment: ["/*", "*/"],
    quotes: "\"'`",
    escapes: "\"'`",
    multiline: "`",
    tripleQuotes: false,
    typeLike: true,
    shellVariables: false,
    propertyKeys: false,
    dollarIdentifiers: true,
  },
  json: {
    keywords: words("true false null"),
    caseInsensitive: false,
    lineComments: [],
    commentNeedsBoundary: false,
    blockComment: null,
    quotes: '"',
    escapes: '"',
    multiline: "",
    tripleQuotes: false,
    typeLike: false,
    shellVariables: false,
    propertyKeys: true,
    dollarIdentifiers: false,
  },
  python: {
    keywords: words(
      "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield",
    ),
    caseInsensitive: false,
    lineComments: ["#"],
    commentNeedsBoundary: false,
    blockComment: null,
    quotes: "\"'",
    escapes: "\"'",
    multiline: "",
    tripleQuotes: true,
    typeLike: true,
    shellVariables: false,
    propertyKeys: false,
    dollarIdentifiers: false,
  },
  bash: {
    keywords: words(
      "if then else elif fi for while until do done case esac in function select return exit break continue local export readonly declare unset source alias echo cd set",
    ),
    caseInsensitive: false,
    lineComments: ["#"],
    commentNeedsBoundary: true,
    blockComment: null,
    quotes: "\"'",
    escapes: '"',
    multiline: "\"'",
    tripleQuotes: false,
    typeLike: false,
    shellVariables: true,
    propertyKeys: false,
    dollarIdentifiers: false,
  },
  sql: {
    keywords: words(
      "select from where and or not insert into values update set delete create table alter drop index view join inner left right outer full cross on as group by order having limit offset union all distinct case when then else end null is in like between exists primary key foreign references default unique constraint with returning asc desc",
    ),
    caseInsensitive: true,
    lineComments: ["--"],
    commentNeedsBoundary: false,
    blockComment: ["/*", "*/"],
    quotes: "'\"",
    escapes: "",
    multiline: "'\"",
    tripleQuotes: false,
    typeLike: false,
    shellVariables: false,
    propertyKeys: false,
    dollarIdentifiers: false,
  },
};

const ALIASES: Record<string, HighlightLanguage> = {
  js: "js",
  javascript: "js",
  jsx: "js",
  mjs: "js",
  cjs: "js",
  ts: "js",
  typescript: "js",
  tsx: "js",
  json: "json",
  jsonc: "json",
  python: "python",
  py: "python",
  bash: "bash",
  sh: "bash",
  shell: "bash",
  zsh: "bash",
  sql: "sql",
};

/** Map a fence language label to a supported language, or null. */
export function normalizeLanguage(
  language: string | null | undefined,
): HighlightLanguage | null {
  if (language === null || language === undefined) {
    return null;
  }
  const key = language.toLowerCase();
  return Object.prototype.hasOwnProperty.call(ALIASES, key)
    ? ALIASES[key]
    : null;
}

function isDigit(ch: string): boolean {
  return ch.length === 1 && ch >= "0" && ch <= "9";
}

function isIdentStart(ch: string, dollar: boolean): boolean {
  if (ch.length !== 1) {
    return false;
  }
  return (
    (ch >= "a" && ch <= "z") ||
    (ch >= "A" && ch <= "Z") ||
    ch === "_" ||
    (dollar && ch === "$")
  );
}

function isIdentPart(ch: string, dollar: boolean): boolean {
  return isIdentStart(ch, dollar) || isDigit(ch);
}

function isNumberPart(ch: string): boolean {
  return isIdentPart(ch, false) || ch === ".";
}

function isUpper(ch: string): boolean {
  return ch >= "A" && ch <= "Z" && ch.length === 1;
}

/** End index (exclusive) of the string that starts at `start`. */
function scanString(
  code: string,
  start: number,
  quote: string,
  spec: LanguageSpec,
): number {
  const n = code.length;
  if (spec.tripleQuotes && code.startsWith(quote.repeat(3), start)) {
    const close = code.indexOf(quote.repeat(3), start + 3);
    return close === -1 ? n : close + 3;
  }
  const honoursEscapes = spec.escapes.includes(quote);
  const spansLines = spec.multiline.includes(quote);
  let j = start + 1;
  while (j < n) {
    const c = code.charAt(j);
    if (c === "\\" && honoursEscapes) {
      j += 2;
      continue;
    }
    if (c === quote) {
      return j + 1;
    }
    if (c === "\n" && !spansLines) {
      return j;
    }
    j += 1;
  }
  return n;
}

function isFollowedByColon(code: string, from: number): boolean {
  let k = from;
  while (code.charAt(k) === " " || code.charAt(k) === "\t") {
    k += 1;
  }
  return code.charAt(k) === ":";
}

function tokenize(code: string, spec: LanguageSpec): HighlightToken[] {
  const tokens: HighlightToken[] = [];
  const push = (type: TokenType, text: string) => {
    if (text === "") {
      return;
    }
    const last = tokens[tokens.length - 1];
    if (last !== undefined && last.type === type) {
      last.text += text;
    } else {
      tokens.push({ type, text });
    }
  };

  const n = code.length;
  let i = 0;
  scan: while (i < n) {
    const ch = code.charAt(i);

    for (const marker of spec.lineComments) {
      if (
        code.startsWith(marker, i) &&
        (!spec.commentNeedsBoundary || i === 0 || /\s/.test(code.charAt(i - 1)))
      ) {
        let end = code.indexOf("\n", i);
        if (end === -1) {
          end = n;
        }
        push("comment", code.slice(i, end));
        i = end;
        continue scan;
      }
    }

    if (
      spec.blockComment !== null &&
      code.startsWith(spec.blockComment[0], i)
    ) {
      const close = code.indexOf(
        spec.blockComment[1],
        i + spec.blockComment[0].length,
      );
      const end = close === -1 ? n : close + spec.blockComment[1].length;
      push("comment", code.slice(i, end));
      i = end;
      continue;
    }

    if (spec.quotes.includes(ch)) {
      const end = scanString(code, i, ch, spec);
      const text = code.slice(i, end);
      if (spec.propertyKeys && isFollowedByColon(code, end)) {
        push("name", text);
      } else {
        push("string", text);
      }
      i = end;
      continue;
    }

    if (isDigit(ch) || (ch === "." && isDigit(code.charAt(i + 1)))) {
      let j = i + 1;
      while (j < n && isNumberPart(code.charAt(j))) {
        j += 1;
      }
      push("number", code.slice(i, j));
      i = j;
      continue;
    }

    if (spec.shellVariables && ch === "$") {
      let j = i + 1;
      if (code.charAt(j) === "{") {
        while (j < n && code.charAt(j) !== "}" && code.charAt(j) !== "\n") {
          j += 1;
        }
        if (code.charAt(j) === "}") {
          j += 1;
        }
      } else {
        while (j < n && isIdentPart(code.charAt(j), false)) {
          j += 1;
        }
        if (j === i + 1 && "@#?*!$-".includes(code.charAt(j)) && j < n) {
          j += 1;
        }
      }
      push("name", code.slice(i, j));
      i = j;
      continue;
    }

    if (isIdentStart(ch, spec.dollarIdentifiers)) {
      let j = i + 1;
      while (j < n && isIdentPart(code.charAt(j), spec.dollarIdentifiers)) {
        j += 1;
      }
      const word = code.slice(i, j);
      const lookup = spec.caseInsensitive ? word.toLowerCase() : word;
      if (spec.keywords.has(lookup)) {
        push("keyword", word);
      } else if (code.charAt(j) === "(") {
        push("name", word);
      } else if (spec.typeLike && isUpper(ch) && word.length > 1) {
        push("type", word);
      } else {
        push("plain", word);
      }
      i = j;
      continue;
    }

    push("plain", ch);
    i += 1;
  }
  return tokens;
}

/**
 * Split `code` into highlighted tokens. Joining the token texts always gives
 * back `code` unchanged.
 */
export function highlight(
  code: string,
  language: string | null | undefined,
): HighlightToken[] {
  const lang = normalizeLanguage(language);
  if (lang === null || code.length > MAX_HIGHLIGHT_CHARS) {
    return code === "" ? [] : [{ type: "plain", text: code }];
  }
  return tokenize(code, SPECS[lang]);
}
