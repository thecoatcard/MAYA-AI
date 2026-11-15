import { describe, expect, it } from "vitest";

import { seededRandom } from "./chat-test-utils";
import {
  MAX_HIGHLIGHT_CHARS,
  highlight,
  normalizeLanguage,
  type HighlightToken,
} from "./highlight";

function joined(tokens: HighlightToken[]): string {
  return tokens.map((token) => token.text).join("");
}

function typeOf(tokens: HighlightToken[], text: string): string | undefined {
  return tokens.find((token) => token.text === text)?.type;
}

describe("normalizeLanguage", () => {
  it.each([
    ["ts", "js"],
    ["TypeScript", "js"],
    ["tsx", "js"],
    ["javascript", "js"],
    ["jsx", "js"],
    ["json", "json"],
    ["py", "python"],
    ["python", "python"],
    ["bash", "bash"],
    ["sh", "bash"],
    ["zsh", "bash"],
    ["sql", "sql"],
  ])("maps %s to %s", (input, expected) => {
    expect(normalizeLanguage(input)).toBe(expected);
  });

  it("returns null for unknown or missing languages", () => {
    expect(normalizeLanguage("rust")).toBeNull();
    expect(normalizeLanguage("")).toBeNull();
    expect(normalizeLanguage(null)).toBeNull();
    expect(normalizeLanguage(undefined)).toBeNull();
    expect(normalizeLanguage("__proto__")).toBeNull();
    expect(normalizeLanguage("constructor")).toBeNull();
  });
});

describe("highlight", () => {
  it("leaves unknown languages as one plain token", () => {
    expect(highlight("let x = 1; // hi", "rust")).toEqual([
      { type: "plain", text: "let x = 1; // hi" },
    ]);
    expect(highlight("let x = 1;", null)).toEqual([
      { type: "plain", text: "let x = 1;" },
    ]);
    expect(highlight("", "ts")).toEqual([]);
  });

  it("highlights TypeScript keywords, strings, numbers, comments, calls and types", () => {
    const code = 'const total: Money = add(1.5, "two"); // note\n/* block */';
    const tokens = highlight(code, "ts");
    expect(joined(tokens)).toBe(code);
    expect(typeOf(tokens, "const")).toBe("keyword");
    expect(typeOf(tokens, "Money")).toBe("type");
    expect(typeOf(tokens, "add")).toBe("name");
    expect(typeOf(tokens, "1.5")).toBe("number");
    expect(typeOf(tokens, '"two"')).toBe("string");
    expect(typeOf(tokens, "// note")).toBe("comment");
    expect(typeOf(tokens, "/* block */")).toBe("comment");
  });

  it("handles template literals across lines and escaped quotes", () => {
    const code = "const s = `a\nb ${x}`; const t = 'it\\'s';";
    const tokens = highlight(code, "js");
    expect(joined(tokens)).toBe(code);
    expect(typeOf(tokens, "`a\nb ${x}`")).toBe("string");
    expect(typeOf(tokens, "'it\\'s'")).toBe("string");
  });

  it("marks JSON keys apart from string values", () => {
    const code = '{ "name": "maya", "ok": true, "n": -3, "x": null }';
    const tokens = highlight(code, "json");
    expect(joined(tokens)).toBe(code);
    expect(typeOf(tokens, '"name"')).toBe("name");
    expect(typeOf(tokens, '"maya"')).toBe("string");
    expect(typeOf(tokens, "true")).toBe("keyword");
    expect(typeOf(tokens, "null")).toBe("keyword");
    expect(typeOf(tokens, "3")).toBe("number");
  });

  it("highlights Python including triple-quoted strings and # comments", () => {
    const code = 'def f(x):\n    """doc\n    string"""\n    return x  # done';
    const tokens = highlight(code, "python");
    expect(joined(tokens)).toBe(code);
    expect(typeOf(tokens, "def")).toBe("keyword");
    expect(typeOf(tokens, "return")).toBe("keyword");
    expect(typeOf(tokens, '"""doc\n    string"""')).toBe("string");
    expect(typeOf(tokens, "# done")).toBe("comment");
  });

  it("highlights Bash variables and only treats # as a comment at a word start", () => {
    const code = 'echo "$HOME" ${USER} a#b # real comment';
    const tokens = highlight(code, "bash");
    expect(joined(tokens)).toBe(code);
    expect(typeOf(tokens, "echo")).toBe("keyword");
    expect(typeOf(tokens, "${USER}")).toBe("name");
    expect(typeOf(tokens, "# real comment")).toBe("comment");
    expect(
      tokens.some(
        (token) => token.type === "comment" && token.text.includes("a#b"),
      ),
    ).toBe(false);
  });

  it("highlights SQL case-insensitively with doubled-quote strings", () => {
    const code = "SELECT id, name FROM users WHERE name = 'O''Brien' -- who";
    const tokens = highlight(code, "sql");
    expect(joined(tokens)).toBe(code);
    expect(typeOf(tokens, "SELECT")).toBe("keyword");
    expect(typeOf(tokens, "FROM")).toBe("keyword");
    expect(typeOf(tokens, "'O''Brien'")).toBe("string");
    expect(typeOf(tokens, "-- who")).toBe("comment");
  });

  it("does not highlight an enormous block (keeps the DOM small)", () => {
    const code = "const a = 1;\n".repeat(Math.ceil(MAX_HIGHLIGHT_CHARS / 10));
    expect(code.length).toBeGreaterThan(MAX_HIGHLIGHT_CHARS);
    expect(highlight(code, "ts")).toEqual([{ type: "plain", text: code }]);
  });

  it("terminates on unterminated strings, comments and odd input", () => {
    for (const language of ["ts", "json", "python", "bash", "sql"]) {
      for (const code of [
        '"never ends',
        "'x",
        "`open",
        "/* open",
        '"""open',
        "${open",
        "$",
        "\\",
        "0x",
        ".",
        "..5",
      ]) {
        expect(joined(highlight(code, language))).toBe(code);
      }
    }
  });

  it("round-trips random input for every language (never changes the code)", () => {
    const random = seededRandom(7);
    const alphabet = [
      '"',
      "'",
      "`",
      "\\",
      "/",
      "*",
      "#",
      "-",
      "$",
      "{",
      "}",
      "(",
      ")",
      ":",
      "\n",
      " ",
      "a",
      "Z",
      "_",
      "1",
      ".",
      "if",
      "SELECT",
      "true",
      "def",
      "echo",
    ];
    for (const language of ["ts", "json", "python", "bash", "sql"]) {
      for (let round = 0; round < 150; round += 1) {
        let code = "";
        const length = 1 + Math.floor(random() * 120);
        for (let i = 0; i < length; i += 1) {
          code += alphabet[Math.floor(random() * alphabet.length)];
        }
        expect(joined(highlight(code, language))).toBe(code);
      }
    }
  });

  it("stays fast on long hostile lines", () => {
    const started = performance.now();
    for (const code of [
      '"'.repeat(50_000),
      "/*".repeat(25_000),
      "1".repeat(50_000),
      "a".repeat(50_000),
      "${".repeat(25_000),
    ]) {
      for (const language of ["ts", "bash", "sql", "python", "json"]) {
        highlight(code, language);
      }
    }
    expect(performance.now() - started).toBeLessThan(3000);
  });
});
