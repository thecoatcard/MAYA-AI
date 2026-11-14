import { describe, expect, it } from "vitest";

import { seededRandom } from "../components/chat/chat-test-utils";
import {
  MAX_MARKDOWN_CHARS,
  MAX_TABLE_COLUMNS,
  decodeBasicEntities,
  describeLinkTarget,
  inlinePlainText,
  linkLabelShowsTarget,
  markBidiControls,
  parseMarkdown,
  sanitizeUrl,
  stripBidiControls,
  type Block,
  type Inline,
} from "./markdown";

// Invisible and direction-changing characters are built from code points so the
// source file itself contains no hidden characters.
const NBSP = String.fromCharCode(0x00a0);
const ZWSP = String.fromCharCode(0x200b);
const ZWNJ = String.fromCharCode(0x200c);
const ZWJ = String.fromCharCode(0x200d);
const WORD_JOINER = String.fromCharCode(0x2060);
const BOM = String.fromCharCode(0xfeff);
const RLO = String.fromCharCode(0x202e);
const LRO = String.fromCharCode(0x202d);
const RLE = String.fromCharCode(0x202b);
const PDF = String.fromCharCode(0x202c);
const LRI = String.fromCharCode(0x2066);
const PDI = String.fromCharCode(0x2069);

function inlines(source: string): Inline[] {
  const block = parseMarkdown(source)[0];
  if (
    block === undefined ||
    (block.type !== "paragraph" && block.type !== "heading")
  ) {
    throw new Error(`expected a paragraph, got ${block?.type}`);
  }
  return block.children;
}

function collectLinks(blocks: readonly Block[]): string[] {
  const hrefs: string[] = [];
  const visitInline = (nodes: readonly Inline[]) => {
    for (const node of nodes) {
      if (node.type === "link") {
        hrefs.push(node.href);
        visitInline(node.children);
      } else if (node.type === "strong" || node.type === "em") {
        visitInline(node.children);
      }
    }
  };
  const visit = (list: readonly Block[]) => {
    for (const block of list) {
      if (block.type === "paragraph" || block.type === "heading") {
        visitInline(block.children);
      } else if (block.type === "blockquote") {
        visit(block.children);
      } else if (block.type === "list") {
        for (const item of block.items) {
          visit(item);
        }
      }
    }
  };
  visit(blocks);
  return hrefs;
}

function timed<T>(run: () => T): { result: T; ms: number } {
  const started = performance.now();
  const result = run();
  return { result, ms: performance.now() - started };
}

describe("sanitizeUrl", () => {
  it.each([
    ["https://example.com/a?b=1#c", "https://example.com/a?b=1#c"],
    ["http://example.com", "http://example.com/"],
    ["mailto:someone@example.com", "mailto:someone@example.com"],
    ["  https://example.com  ", "https://example.com/"],
    ["HTTPS://EXAMPLE.COM/Path", "https://example.com/Path"],
    // A bare domain is a website the model forgot the scheme for: it becomes https.
    ["esc-plus.com", "https://esc-plus.com/"],
    ["example.org/a/b", "https://example.org/a/b"],
  ])("allows %s", (raw, expected) => {
    expect(sanitizeUrl(raw)).toBe(expected);
  });

  it.each([
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "  javascript:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "\u0001javascript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "DATA:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "blob:https://example.com/abc",
    "//evil.example.com/x",
    "/relative/path",
    "#fragment",
    "relative.html",
    "https://user:pass@example.com/",
    "https://google.com@evil.example.com:pass@x.test",
    "",
    "https://" + "a".repeat(3000) + ".example.com",
  ])("rejects %s", (raw) => {
    expect(sanitizeUrl(raw)).toBeNull();
  });
});

describe("blocks", () => {
  it("parses paragraphs separated by blank lines and keeps soft breaks", () => {
    const blocks = parseMarkdown("one\ntwo\n\nthree");
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toEqual({
      type: "paragraph",
      children: [
        { type: "text", value: "one" },
        { type: "br" },
        { type: "text", value: "two" },
      ],
    });
  });

  it("parses headings, with closing hashes and without a space rule violations", () => {
    expect(parseMarkdown("# Title")[0]).toMatchObject({
      type: "heading",
      level: 1,
    });
    expect(parseMarkdown("###### Six ######")[0]).toEqual({
      type: "heading",
      level: 6,
      children: [{ type: "text", value: "Six" }],
    });
    expect(parseMarkdown("####### seven")[0]?.type).toBe("paragraph");
    expect(parseMarkdown("#hashtag")[0]?.type).toBe("paragraph");
  });

  it("parses fenced code blocks, keeping their content literal", () => {
    const blocks = parseMarkdown(
      "```ts\nconst a = **1**;\n# not a heading\n```\nafter",
    );
    expect(blocks[0]).toEqual({
      type: "code",
      language: "ts",
      value: "const a = **1**;\n# not a heading",
    });
    expect(blocks[1]?.type).toBe("paragraph");
  });

  it("treats an unclosed fence as code to the end (a reply still streaming)", () => {
    expect(parseMarkdown("```python\nprint('hi')")[0]).toEqual({
      type: "code",
      language: "python",
      value: "print('hi')",
    });
  });

  it("supports tilde fences and longer closing fences", () => {
    expect(parseMarkdown("~~~\nx\n~~~")[0]).toMatchObject({
      type: "code",
      value: "x",
    });
    expect(parseMarkdown("```\na\n```\n\n````\nb\n```\nc\n````")).toMatchObject(
      [
        { type: "code", value: "a" },
        { type: "code", value: "b\n```\nc" },
      ],
    );
  });

  it("drops an unsafe-looking language label", () => {
    expect(parseMarkdown('```"><script>\nx\n```')[0]).toMatchObject({
      language: null,
    });
    expect(parseMarkdown("```" + "a".repeat(40) + "\nx\n```")[0]).toMatchObject(
      {
        language: null,
      },
    );
  });

  it("parses unordered and ordered lists, including start numbers", () => {
    const bullets = parseMarkdown("- a\n- b\n* c");
    expect(bullets[0]).toMatchObject({ type: "list", ordered: false });
    expect((bullets[0] as { items: Block[][] }).items).toHaveLength(3);
    const ordered = parseMarkdown("3. x\n4. y");
    expect(ordered[0]).toMatchObject({ type: "list", ordered: true, start: 3 });
  });

  it("nests lists by indentation of 2, 3 or 4 spaces", () => {
    for (const indent of ["  ", "   ", "    "]) {
      const blocks = parseMarkdown(
        `1. parent\n${indent}- child\n${indent}- child two\n2. next`,
      );
      const list = blocks[0] as Extract<Block, { type: "list" }>;
      expect(list.items).toHaveLength(2);
      const nested = list.items[0]?.find((block) => block.type === "list");
      expect(nested).toMatchObject({ type: "list", ordered: false });
      expect((nested as Extract<Block, { type: "list" }>).items).toHaveLength(
        2,
      );
    }
  });

  it("keeps a fenced code block inside a list item", () => {
    const list = parseMarkdown(
      "1. Run:\n   ```bash\n   npm test\n   ```\n2. Done",
    )[0] as Extract<Block, { type: "list" }>;
    expect(list.items).toHaveLength(2);
    expect(list.items[0]?.[1]).toEqual({
      type: "code",
      language: "bash",
      value: "npm test",
    });
  });

  it("lets a bullet list follow a paragraph without a blank line", () => {
    const blocks = parseMarkdown("Steps:\n- one\n- two");
    expect(blocks.map((block) => block.type)).toEqual(["paragraph", "list"]);
  });

  it("does not turn 'text 2024. more' or a bare number into a list", () => {
    expect(parseMarkdown("2024.5 was a year")[0]?.type).toBe("paragraph");
    expect(parseMarkdown("1.")[0]?.type).toBe("paragraph");
    expect(parseMarkdown("-nospace")[0]?.type).toBe("paragraph");
  });

  it("parses blockquotes, including nested ones and lazy continuation", () => {
    const quote = parseMarkdown(
      "> quoted\n> > deeper\nlazy line",
    )[0] as Extract<Block, { type: "blockquote" }>;
    expect(quote.type).toBe("blockquote");
    expect(quote.children.some((block) => block.type === "blockquote")).toBe(
      true,
    );
  });

  it("parses horizontal rules in the usual spellings", () => {
    for (const rule of ["---", "***", "___", "- - -", "* * *", "_____"]) {
      expect(
        parseMarkdown(`a\n\n${rule}\n\nb`).map((block) => block.type),
      ).toEqual(["paragraph", "hr", "paragraph"]);
    }
  });

  it("parses a pipe table into header, alignment and body rows", () => {
    const blocks = parseMarkdown(
      "| a | b | c |\n| :-- | :-: | --: |\n| 1 | **2** | 3 |\n| 4 | 5 | 6 |",
    );
    expect(blocks).toHaveLength(1);
    const table = blocks[0];
    if (table?.type !== "table") throw new Error("expected a table");
    expect(table.align).toEqual(["left", "center", "right"]);
    expect(table.header).toHaveLength(3);
    expect(table.rows).toHaveLength(2);
    expect(table.rows[0]?.[1]?.[0]?.type).toBe("strong");
  });

  it("accepts a table without outer pipes, escaped pipes, and short or long rows", () => {
    const blocks = parseMarkdown("a | b\n- | -\nx \\| y | z | extra\nonly | ");
    const table = blocks[0];
    if (table?.type !== "table") throw new Error("expected a table");
    expect(table.header).toHaveLength(2);
    expect(table.rows).toHaveLength(2);
    // Every row has exactly one cell per header column.
    expect(table.rows.every((row) => row.length === 2)).toBe(true);
    expect(JSON.stringify(table.rows[0]?.[0])).toContain("x | y");
  });

  it("ends a table at a blank line and lets a table interrupt a paragraph", () => {
    const blocks = parseMarkdown(
      "intro\n| a | b |\n| - | - |\n| 1 | 2 |\n\nafter",
    );
    expect(blocks.map((block) => block.type)).toEqual([
      "paragraph",
      "table",
      "paragraph",
    ]);
  });

  it("keeps a header without a delimiter row, or with the wrong column count, as text", () => {
    expect(parseMarkdown("| a | b |\n| 1 | 2 |")[0]?.type).toBe("paragraph");
    expect(parseMarkdown("| a | b |\n| - |\n| 1 | 2 |")[0]?.type).toBe(
      "paragraph",
    );
    expect(parseMarkdown("a\n---")[0]?.type).not.toBe("table");
  });

  it("shows a table with too many columns as plain text", () => {
    const wide = Array.from({ length: MAX_TABLE_COLUMNS + 1 }, () => "x");
    const blocks = parseMarkdown(
      `| ${wide.join(" | ")} |\n| ${wide.map(() => "-").join(" | ")} |\n| ${wide.join(" | ")} |`,
    );
    expect(blocks.every((block) => block.type !== "table")).toBe(true);
  });

  it("keeps raw HTML and links inside table cells inert and safe", () => {
    const blocks = parseMarkdown(
      "| a |\n| - |\n| <script>x</script> |\n| [k](javascript:alert(1)) |",
    );
    const table = blocks[0];
    if (table?.type !== "table") throw new Error("expected a table");
    const text = JSON.stringify(table);
    expect(text).not.toContain('"link"');
    expect(text).toContain("<script>x</script>");
  });

  it("handles CRLF and tabs in indentation", () => {
    expect(parseMarkdown("- a\r\n\t- b\r\n- c")[0]).toMatchObject({
      type: "list",
    });
    expect(parseMarkdown("a\r\n\r\nb")).toHaveLength(2);
  });

  it("returns nothing for empty input", () => {
    expect(parseMarkdown("")).toEqual([]);
    expect(parseMarkdown("\n\n  \n")).toEqual([]);
  });
});

describe("inlines", () => {
  it("parses bold, italic, bold italic and inline code", () => {
    expect(inlines("**b** *i* `c`")).toEqual([
      { type: "strong", children: [{ type: "text", value: "b" }] },
      { type: "text", value: " " },
      { type: "em", children: [{ type: "text", value: "i" }] },
      { type: "text", value: " " },
      { type: "code", value: "c" },
    ]);
    expect(inlines("***both***")[0]).toEqual({
      type: "strong",
      children: [{ type: "em", children: [{ type: "text", value: "both" }] }],
    });
    expect(inlines("__bold__ and _it_")[0]?.type).toBe("strong");
  });

  it("nests emphasis in the common LLM spellings", () => {
    expect(inlines("**bold *italic***")[0]).toMatchObject({ type: "strong" });
    expect(inlines("*a **b** c*")[0]).toMatchObject({ type: "em" });
  });

  it("keeps markup characters inside code spans literal", () => {
    expect(inlines("`**not bold** <b>x</b>`")).toEqual([
      { type: "code", value: "**not bold** <b>x</b>" },
    ]);
    expect(inlines("`` a`b ``")).toEqual([{ type: "code", value: "a`b" }]);
  });

  it("does not make emphasis out of snake_case, math or lone asterisks", () => {
    expect(inlines("snake_case_name here")).toEqual([
      { type: "text", value: "snake_case_name here" },
    ]);
    expect(inlines("5 * 3 * 2")).toEqual([
      { type: "text", value: "5 * 3 * 2" },
    ]);
    expect(inlines("2*3*4")[0]?.type).toBe("text");
  });

  it("leaves an unclosed delimiter as text (a reply still streaming)", () => {
    expect(inlines("**bold te")).toEqual([
      { type: "text", value: "**bold te" },
    ]);
    expect(inlines("a `code")).toEqual([{ type: "text", value: "a `code" }]);
  });

  it("honours backslash escapes", () => {
    expect(inlines("\\*not italic\\*")).toEqual([
      { type: "text", value: "*not italic*" },
    ]);
  });

  it("parses inline links and drops titles", () => {
    const [link] = inlines('[docs](https://example.com/a_(b) "Title")');
    expect(link).toEqual({
      type: "link",
      href: "https://example.com/a_(b)",
      children: [{ type: "text", value: "docs" }],
    });
  });

  it("parses autolinks and bare http(s) URLs, trimming trailing punctuation", () => {
    expect(collectLinks(parseMarkdown("<https://example.com/x>"))).toEqual([
      "https://example.com/x",
    ]);
    expect(
      collectLinks(
        parseMarkdown(
          "See https://example.com/path, then (https://example.org/a_b).",
        ),
      ),
    ).toEqual(["https://example.com/path", "https://example.org/a_b"]);
    expect(collectLinks(parseMarkdown("<mailto:a@example.com>"))).toEqual([
      "mailto:a@example.com",
    ]);
  });

  it("turns an image into its alt text and never a link", () => {
    expect(inlines("![a cat](https://tracker.example/pixel.png)")).toEqual([
      { type: "text", value: "a cat" },
    ]);
    expect(inlines("![](https://tracker.example/pixel.png)")).toEqual([
      { type: "text", value: "[image]" },
    ]);
    expect(
      collectLinks(parseMarkdown("![x](https://tracker.example/p.png)")),
    ).toEqual([]);
  });

  it("nests an image inside a link as alt text only", () => {
    const [link] = inlines(
      "[![logo](https://cdn.example/l.png)](https://example.com)",
    );
    expect(link).toEqual({
      type: "link",
      href: "https://example.com/",
      children: [{ type: "text", value: "logo" }],
    });
  });
});

describe("security: URLs that must stay plain text", () => {
  const attacks = [
    "[click](javascript:alert(1))",
    "[click](JAVASCRIPT:alert(1))",
    "[click]( javascript:alert(1))",
    "[click](java\tscript:alert(1))",
    "[click](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "[click](vbscript:msgbox(1))",
    "[click](<javascript:alert(1)>)",
    "[click](//evil.example.com)",
    "[click](/relative)",
    "[click](https://user:pw@evil.example.com)",
    "<javascript:alert(1)>",
    "<data:text/html,x>",
    "![x](javascript:alert(1))",
    // Entities must not spell a scheme.
    "[click](&#106;avascript:alert(1))",
    "[click](&#x6A;avascript:alert(1))",
    "[click](&#0000106avascript:alert(1))",
    "[click](javascript&colon;alert(1))",
    "[click](javascript&#58;alert(1))",
    "[click](javascript&#x3a;alert(1))",
    "[click](&amp;#106;avascript:alert(1))",
    "[click](&amp;colon;javascript:alert(1))",
    "[click](https&colon;//evil.example)",
    "[click](&lt;javascript:alert(1)&gt;)",
    // Invisible characters in front of, or inside, a scheme.
    `[click](${NBSP}javascript:alert(1))`,
    `[click](${ZWSP}javascript:alert(1))`,
    `[click](${ZWNJ}javascript:alert(1))`,
    `[click](${ZWJ}javascript:alert(1))`,
    `[click](${WORD_JOINER}javascript:alert(1))`,
    `[click](${BOM}javascript:alert(1))`,
    `[click](java${ZWSP}script:alert(1))`,
    `[click](javascript${ZWSP}:alert(1))`,
    `[click](${NBSP}${NBSP}data:text/html,x)`,
    `[click](<${NBSP}javascript:alert(1)>)`,
    `<${ZWSP}javascript:alert(1)>`,
    // Bidirectional controls in or around a dangerous destination.
    `[click](${RLO}javascript:alert(1))`,
    `[click](${LRO}javascript:alert(1))`,
    `[click](javascript${RLO}:alert(1))`,
    `[click](${LRI}javascript:alert(1)${PDI})`,
    `[click](${RLE}javascript:alert(1)${PDF})`,
    `[${RLO}safe](javascript:alert(1))`,
  ];

  it.each(attacks)("creates no link for %s", (source) => {
    expect(collectLinks(parseMarkdown(source))).toEqual([]);
  });

  it("keeps a dangerous destination visible as text even with hidden characters", () => {
    const text = JSON.stringify(
      parseMarkdown(`[click](${ZWSP}javascript:alert(1))`),
    );
    expect(text).toContain("javascript:alert(1)");
  });

  it("never lets a hidden character or direction control end up inside an href", () => {
    for (const hidden of [NBSP, ZWSP, ZWNJ, ZWJ, WORD_JOINER, BOM, RLO, LRO]) {
      const hrefs = collectLinks(
        parseMarkdown(`[a](https://example.com/${hidden}path)`),
      );
      expect(hrefs).toHaveLength(1);
      for (const href of hrefs) {
        expect(href).toMatch(/^https:\/\/example\.com\//);
        expect(/[^\x21-\x7e]/.test(href)).toBe(false);
      }
    }
  });

  it("keeps the dangerous text visible as text", () => {
    const text = JSON.stringify(parseMarkdown("[click](javascript:alert(1))"));
    expect(text).toContain("javascript:alert(1)");
  });
});

describe("robustness: bounded work on hostile input", () => {
  const cases: Array<[string, string]> = [
    ["many asterisks", "*".repeat(50_000)],
    ["many underscores", "_".repeat(50_000)],
    ["unclosed bold openers", "**a ".repeat(25_000)],
    ["unclosed italic openers", "*a ".repeat(30_000)],
    ["unclosed underscore openers", "_a ".repeat(30_000)],
    [
      "unmatched backticks of growing length",
      Array.from({ length: 400 }, (_, i) => "`".repeat(i + 1) + "x").join(" "),
    ],
    ["alternating backtick runs", "`a``b```c".repeat(8_000)],
    ["unmatched brackets", "[".repeat(50_000)],
    ["bracket pairs without destinations", "[a]".repeat(30_000)],
    ["link starts without end", "[a](".repeat(20_000)],
    ["link starts without end, at the cap", "[a](".repeat(50_000)],
    ["bare urls without end", "https://".repeat(10_000)],
    [
      "bare urls ending in a long run of closing parentheses",
      ("http://a.com/" + ")".repeat(2000) + " ").repeat(99),
    ],
    [
      "bare urls ending in closing parentheses, one per line",
      ("http://a.com/" + ")".repeat(2000) + "\n").repeat(99),
    ],
    [
      "bare urls with balanced parentheses",
      ("http://a.com/" + "(".repeat(1000) + ")".repeat(1000) + " ").repeat(99),
    ],
    [
      "bare urls ending in punctuation",
      ("http://a.com/" + ".".repeat(2000) + " ").repeat(99),
    ],
    ["angle brackets", "<".repeat(50_000)],
    ["angle brackets then close", "<".repeat(50_000) + ">"],
    ["autolink starts", "<https://a".repeat(10_000)],
    ["images without end", "![a](".repeat(20_000)],
    ["images without end, at the cap", "![a](".repeat(40_000)],
    ["images inside link starts", "[![a](x)](".repeat(15_000)],
    ["link starts with unfinished titles", '[a](b "'.repeat(20_000)],
    ["link starts with spaces", "[a](b ".repeat(30_000)],
    ["deeply nested quotes on one line", ">".repeat(100_000)],
    [
      "deeply nested quotes on many lines",
      "> ".repeat(2_000).trim() + "\ntext",
    ],
    [
      "deeply nested lists",
      Array.from(
        { length: 3_000 },
        (_, i) => " ".repeat(i * 2) + "- item",
      ).join("\n"),
    ],
    [
      "deeply nested lists, under the size cap",
      Array.from({ length: 380 }, (_, i) => " ".repeat(i * 2) + "- item").join(
        "\n",
      ),
    ],
    ["many list items", "- item\n".repeat(20_000)],
    [
      "many ordered items",
      Array.from({ length: 20_000 }, (_, i) => `${i + 1}. item`).join("\n"),
    ],
    ["many headings", "# h\n".repeat(20_000)],
    ["heading markers and spaces", "# " + "# ".repeat(50_000) + "x"],
    ["hash run", "#".repeat(100_000)],
    ["very long single line", "word ".repeat(30_000)],
    ["very long unbroken line", "a".repeat(150_000)],
    ["many blank lines", "\n".repeat(100_000) + "x"],
    ["many fences", "```\n".repeat(20_000)],
    ["spaces only", " ".repeat(100_000)],
    ["backslashes", "\\".repeat(50_000)],
    ["emphasis soup", "*_**__***___".repeat(5_000)],
    ["classic ReDoS bait", "a".repeat(5_000) + "!"],
    ["nested emphasis", "*a ".repeat(2_000) + "b" + "* ".repeat(2_000)],
    [
      "link text nesting",
      "[".repeat(5_000) + "x" + "](https://a.example)".repeat(5_000),
    ],
  ];

  // Measured at 2 to 60 ms each. 500 ms leaves room for a slow machine but still
  // fails on the quadratic cases this guards against (they took 300 to 800 ms).
  // Input over the size cap is only split into lines, never parsed, but a source
  // of several megabytes costs allocation and garbage collection, so it gets more
  // room; it is a test of the cap, not of parser complexity.
  it.each(cases)("finishes quickly: %s", (_name, source) => {
    const { result, ms } = timed(() => parseMarkdown(source));
    expect(Array.isArray(result)).toBe(true);
    expect(ms).toBeLessThan(source.length > MAX_MARKDOWN_CHARS ? 1500 : 500);
  });

  it("keeps the cost of a URL's closing parentheses linear, not quadratic", () => {
    // The same total size, built from long or from short runs of ")". Counting
    // parentheses again for every character made the long runs several times
    // slower per character; linear counting makes the two the same.
    const build = (run: number) =>
      ("http://a.com/" + ")".repeat(run) + " ").repeat(
        Math.floor(150_000 / (run + 14)),
      );
    parseMarkdown(build(200));
    const short = timed(() => parseMarkdown(build(200))).ms;
    const long = timed(() => parseMarkdown(build(2000))).ms;
    expect(long).toBeLessThan(Math.max(short * 3, 40));
  });

  it("charges failed link attempts to a work budget, then shows the rest as text", () => {
    // A mailto destination: unlike an http URL it is never linked as a bare URL.
    const hostile = "[a](".repeat(30_000) + "[late](mailto:a@example.com)";
    const { result, ms } = timed(() => parseMarkdown(hostile));
    expect(ms).toBeLessThan(500);
    // Past the budget no link is attempted: plain text, never a wrong link.
    expect(collectLinks(result)).toEqual([]);
    expect(JSON.stringify(result)).toContain("[late]");
  });

  it("does not spend the budget on ordinary text and still links afterwards", () => {
    const source =
      "[a](/relative) ".repeat(150) + "[ok](https://example.com/ok)";
    expect(collectLinks(parseMarkdown(source))).toEqual([
      "https://example.com/ok",
    ]);
  });

  it("skips formatting for oversized input and keeps the text", () => {
    const source = "**bold** ".repeat(MAX_MARKDOWN_CHARS / 8);
    expect(source.length).toBeGreaterThan(MAX_MARKDOWN_CHARS);
    const { result, ms } = timed(() => parseMarkdown(source));
    expect(ms).toBeLessThan(500);
    expect(JSON.stringify(result)).not.toContain('"strong"');
    expect(JSON.stringify(result)).toContain("**bold**");
  });

  it("flattens nesting beyond the depth limit instead of recursing forever", () => {
    const blocks = parseMarkdown(">".repeat(2_000) + " deep");
    let depth = 0;
    let current: Block | undefined = blocks[0];
    while (current?.type === "blockquote") {
      depth += 1;
      current = current.children[0];
    }
    expect(depth).toBeLessThanOrEqual(10);
    expect(JSON.stringify(blocks)).toContain("deep");
  });

  it("never throws on random markup soup", () => {
    const alphabet = [
      "*",
      "_",
      "`",
      "[",
      "]",
      "(",
      ")",
      "<",
      ">",
      "!",
      "#",
      "-",
      "1.",
      " ",
      "\n",
      "\\",
      "a",
      "http://x.y",
      "~~~",
      "|",
    ];
    let state = 12345;
    const next = () => {
      state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
      return state / 0x100000000;
    };
    for (let round = 0; round < 300; round += 1) {
      let source = "";
      const length = 1 + Math.floor(next() * 200);
      for (let i = 0; i < length; i += 1) {
        source += alphabet[Math.floor(next() * alphabet.length)];
      }
      expect(() => parseMarkdown(source)).not.toThrow();
    }
  });
});

describe("bare URLs: trailing punctuation and parentheses", () => {
  it.each([
    ["see https://a.example/x))))", ["https://a.example/x"]],
    [
      "(see https://en.wikipedia.org/wiki/Foo_(bar))",
      ["https://en.wikipedia.org/wiki/Foo_(bar)"],
    ],
    ["https://a.example/x(y)))", ["https://a.example/x(y)"]],
    ["https://a.example/x...)", ["https://a.example/x"]],
    ["https://a.example/x?!.", ["https://a.example/x"]],
    ["https://a.example/(a(b)c)", ["https://a.example/(a(b)c)"]],
    ["https://a.example/(a(b)c))", ["https://a.example/(a(b)c)"]],
  ])("links %s", (source, expected) => {
    expect(collectLinks(parseMarkdown(source))).toEqual(expected);
  });

  it("trims a long run of closing parentheses to the URL", () => {
    expect(
      collectLinks(parseMarkdown("http://a.example/" + ")".repeat(2000))),
    ).toEqual(["http://a.example/"]);
  });
});

describe("entities inside link URLs", () => {
  it("decodes only the five basic entities, in one pass", () => {
    expect(decodeBasicEntities("a&amp;b&lt;c&gt;d&quot;e&#39;f")).toBe(
      "a&b<c>d\"e'f",
    );
    expect(decodeBasicEntities("&amp;lt;")).toBe("&lt;");
    expect(decodeBasicEntities("&amp;amp;")).toBe("&amp;");
    expect(decodeBasicEntities("no entities")).toBe("no entities");
  });

  it.each([
    "&#106;",
    "&#x6A;",
    "&colon;",
    "&#58;",
    "&nbsp;",
    "&Tab;",
    "&#x26;",
    "&amp",
  ])("leaves %s alone", (entity) => {
    expect(decodeBasicEntities(`a${entity}b`)).toBe(`a${entity}b`);
  });

  it("decodes &amp; in an inline link destination", () => {
    expect(
      collectLinks(parseMarkdown("[x](https://a.example/?a=1&amp;b=2)")),
    ).toEqual(["https://a.example/?a=1&b=2"]);
  });

  it("decodes the other basic entities into a safely encoded URL", () => {
    const [href] = collectLinks(
      parseMarkdown("[x](https://a.example/?q=&quot;x&quot;&#39;&lt;&gt;)"),
    );
    expect(href).toBe(new URL('https://a.example/?q="x"\'<>').href);
    expect(href).not.toMatch(/[<>"']/);
  });

  it("leaves entities in autolinks and bare URLs as written", () => {
    expect(
      collectLinks(parseMarkdown("<https://a.example/?a=1&amp;b=2>")),
    ).toEqual(["https://a.example/?a=1&amp;b=2"]);
  });
});

describe("where a link goes", () => {
  it("describes the destination host, with a non-default port", () => {
    expect(describeLinkTarget("https://evil.example/phish")).toBe(
      "evil.example",
    );
    expect(describeLinkTarget("https://evil.example:8443/x")).toBe(
      "evil.example:8443",
    );
    expect(describeLinkTarget("http://127.0.0.1:8000/")).toBe("127.0.0.1:8000");
  });

  it("shows an international host in the ASCII form the browser uses", () => {
    expect(describeLinkTarget("https://b\u00fccher.example/")).toBe(
      "xn--bcher-kva.example",
    );
  });

  it("describes a mailto link by its address", () => {
    expect(describeLinkTarget("mailto:team@example.com")).toBe(
      "team@example.com",
    );
    expect(describeLinkTarget("mailto:team@example.com?subject=Hi")).toBe(
      "team@example.com",
    );
  });

  it("caps a very long target and rejects a non-URL", () => {
    const long = describeLinkTarget(`mailto:${"a".repeat(500)}@example.com`);
    expect(long?.length).toBeLessThanOrEqual(101);
    expect(describeLinkTarget("not a url")).toBeNull();
  });

  it("recognises link text that is itself the destination", () => {
    expect(
      linkLabelShowsTarget("https://example.com/a", "https://example.com/b"),
    ).toBe(true);
    expect(
      linkLabelShowsTarget("HTTPS://EXAMPLE.COM", "https://example.com/b"),
    ).toBe(true);
    expect(
      linkLabelShowsTarget("mailto:a@example.com", "mailto:a@example.com"),
    ).toBe(true);
    expect(linkLabelShowsTarget("a@example.com", "mailto:a@example.com")).toBe(
      true,
    );
  });

  it.each([
    // A label that names another site: the host must be shown.
    ["https://mybank.example/login", "https://evil.example/phish"],
    ["mybank.example", "https://evil.example/"],
    ["https://mybank.example.evil.example/", "https://evil.example/"],
    // Credentials in the label look like a host but are not.
    ["https://mybank.example@evil.example/", "https://evil.example/"],
    // Words, spaces and an empty label never count as the destination.
    ["docs", "https://example.com/"],
    ["https://example.com/ read this", "https://example.com/"],
    ["", "https://example.com/"],
    ["a@example.com", "mailto:b@example.com"],
  ])("does not treat %j as showing %s", (label, href) => {
    expect(linkLabelShowsTarget(label, href)).toBe(false);
  });

  it("flattens nested inline nodes into plain text, with a cap", () => {
    const nodes = inlines(
      "**bold** and *em* and `code` and [x](https://a.example)",
    );
    expect(inlinePlainText(nodes)).toBe("bold and em and code and x");
    expect(
      inlinePlainText(inlines("a".repeat(10_000))).length,
    ).toBeLessThanOrEqual(2049);
  });
});

describe("direction controls", () => {
  it("marks each control character visibly", () => {
    expect(markBidiControls(`a${RLO}b${PDF}c`)).toBe("a<U+202E>b<U+202C>c");
    expect(markBidiControls(`${LRI}x${PDI}`)).toBe("<U+2066>x<U+2069>");
    expect(markBidiControls("plain \u00e9 text")).toBe("plain \u00e9 text");
  });

  it("strips them from link text", () => {
    expect(stripBidiControls(`ab${RLO}cd${PDF}`)).toBe("abcd");
  });
});

describe("fuzz: every link that comes out is safe", () => {
  const pieces = [
    "[",
    "]",
    "(",
    ")",
    "<",
    ">",
    "!",
    "*",
    "_",
    "`",
    "\\",
    " ",
    "\n",
    "\t",
    "x",
    "label",
    "http://",
    "https://",
    "HTTPS://",
    "mailto:",
    "javascript:",
    "JaVaScRiPt:",
    "java\tscript:",
    "data:text/html,",
    "vbscript:",
    "file://",
    "ftp://",
    "//",
    "/",
    "@",
    "user:pw@",
    "a.example",
    "evil.example",
    "xn--",
    "?q=1&amp;r=2",
    "&#106;",
    "&#x6A;",
    "&colon;",
    "&amp;",
    "&lt;",
    "&quot;",
    "%",
    "%6A",
    "#",
    ".",
    ",",
    '"',
    "'",
    NBSP,
    ZWSP,
    ZWJ,
    BOM,
    RLO,
    LRI,
    PDI,
    "\u0000",
    "\u0001",
  ];

  function randomDocument(next: () => number): string {
    const length = 1 + Math.floor(next() * 60);
    let source = "";
    for (let i = 0; i < length; i += 1) {
      source += pieces[Math.floor(next() * pieces.length)];
    }
    return source;
  }

  const offenders: Array<{ source: string; href: string }> = [];

  /** Cheap on purpose: thousands of links are checked, so no expect() per link. */
  function check(href: string, source: string) {
    let safe = /^(https?:|mailto:)/.test(href);
    if (safe) {
      const url = new URL(href);
      safe = url.username === "" && url.password === "";
    }
    if (!safe) {
      offenders.push({ source, href });
    }
  }

  it.each([1, 20261105, 777])(
    "produces only http, https and mailto links (seed %i)",
    (seed) => {
      offenders.length = 0;
      const next = seededRandom(seed);
      let linksSeen = 0;
      for (let round = 0; round < 4000; round += 1) {
        const source = randomDocument(next);
        for (const href of collectLinks(parseMarkdown(source))) {
          linksSeen += 1;
          check(href, source);
        }
      }
      expect(offenders).toEqual([]);
      // The generator must actually reach the link code, or this proves nothing.
      expect(linksSeen).toBeGreaterThan(50);
    },
  );

  it("also holds when the pieces are wrapped into link syntax", () => {
    offenders.length = 0;
    const next = seededRandom(424242);
    let linksSeen = 0;
    for (let round = 0; round < 1500; round += 1) {
      const target = randomDocument(next);
      const label = randomDocument(next);
      for (const source of [
        `[${label}](${target})`,
        `[${label}](<${target}>)`,
        `<${target}>`,
        `**[${label}](${target})**`,
        `> [${label}](${target} "t")`,
        `- ![${label}](${target})`,
      ]) {
        for (const href of collectLinks(parseMarkdown(source))) {
          linksSeen += 1;
          check(href, source);
        }
      }
    }
    expect(offenders).toEqual([]);
    expect(linksSeen).toBeGreaterThan(50);
  });
});
