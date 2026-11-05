import React from "react";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  FORBIDDEN_PATTERNS,
  INVISIBLE_CHARACTERS,
  listSourceFiles,
  readCode,
  readRaw,
} from "../../test/source-scan";
import { MarkdownRenderer } from "./MarkdownRenderer";

const RLO = String.fromCharCode(0x202e);
const PDF = String.fromCharCode(0x202c);
const ZWSP = String.fromCharCode(0x200b);
const NBSP = String.fromCharCode(0x00a0);

/** No element may carry an on* attribute; text that merely contains "onerror=" is fine. */
function expectNoEventHandlerAttributes(container: HTMLElement) {
  for (const element of Array.from(container.querySelectorAll("*"))) {
    for (const attribute of Array.from(element.attributes)) {
      expect(attribute.name.toLowerCase().startsWith("on")).toBe(false);
      expect(attribute.name.toLowerCase()).not.toBe("style");
    }
  }
}

function renderMarkdown(content: string, streaming = false) {
  return render(<MarkdownRenderer content={content} streaming={streaming} />);
}

describe("MarkdownRenderer: supported subset", () => {
  it("renders paragraphs, headings, emphasis, inline code and a rule", () => {
    const { container } = renderMarkdown(
      "# Title\n\nSome **bold**, *italic* and `code`.\n\n---\n\n### Third",
    );
    expect(container.querySelector("h3")?.textContent).toBe("Title");
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("em")?.textContent).toBe("italic");
    expect(container.querySelector("code.maya-md__code")?.textContent).toBe(
      "code",
    );
    expect(container.querySelector("hr")).not.toBeNull();
    expect(container.querySelector("h4")?.textContent).toBe("Third");
  });

  it("renders lists with start numbers, nesting and blockquotes", () => {
    const { container } = renderMarkdown(
      "3. three\n4. four\n   - nested\n\n> quoted *text*",
    );
    const ol = container.querySelector("ol");
    expect(ol?.getAttribute("start")).toBe("3");
    expect(ol?.querySelectorAll("li")).toHaveLength(3);
    expect(ol?.querySelector("li ul li")?.textContent).toBe("nested");
    expect(container.querySelector("blockquote em")?.textContent).toBe("text");
  });

  it("renders fenced code through the CodeBlock with a language label", () => {
    const { container } = renderMarkdown("```ts\nconst a = 1;\n```");
    expect(container.querySelector(".maya-code__lang")?.textContent).toBe("ts");
    expect(container.querySelector("pre code")?.textContent).toBe(
      "const a = 1;",
    );
  });

  it("opens safe links in a new tab with rel noopener noreferrer", () => {
    const { container } = renderMarkdown(
      "[docs](https://example.com/a), <https://example.org> and https://example.net/x.",
    );
    const links = Array.from(container.querySelectorAll("a"));
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "https://example.com/a",
      "https://example.org/",
      "https://example.net/x",
    ]);
    for (const link of links) {
      expect(link.getAttribute("target")).toBe("_blank");
      expect(link.getAttribute("rel")).toBe("noopener noreferrer");
      expect(link.getAttribute("referrerpolicy")).toBe("no-referrer");
    }
  });

  it("allows mailto links", () => {
    const { container } = renderMarkdown("[mail](mailto:team@example.com)");
    expect(container.querySelector("a")?.getAttribute("href")).toBe(
      "mailto:team@example.com",
    );
  });

  it("shows a streaming caret at the end of the text, and none when finished", () => {
    const streaming = renderMarkdown("Hello wor", true);
    expect(streaming.container.querySelector("p .maya-caret")).not.toBeNull();
    streaming.unmount();
    const done = renderMarkdown("Hello world", false);
    expect(done.container.querySelector(".maya-caret")).toBeNull();
  });

  it("places the caret in the last list item and after a trailing code block", () => {
    const list = renderMarkdown("- a\n- b", true);
    const items = list.container.querySelectorAll("li");
    expect(items[0]?.querySelector(".maya-caret")).toBeNull();
    expect(items[1]?.querySelector(".maya-caret")).not.toBeNull();
    list.unmount();
    const code = renderMarkdown("```js\nlet x", true);
    expect(code.container.querySelector(".maya-caret")).not.toBeNull();
  });
});

describe("MarkdownRenderer: where a link goes", () => {
  function firstLink(source: string): HTMLAnchorElement {
    const { container } = renderMarkdown(source);
    const link = container.querySelector("a");
    if (link === null) {
      throw new Error("no link rendered");
    }
    return link;
  }

  function visibleHost(link: HTMLAnchorElement): string | null {
    return link.querySelector(".maya-md__host")?.textContent ?? null;
  }

  function spoken(link: HTMLAnchorElement): string {
    return Array.from(link.querySelectorAll(".maya-sr-only"))
      .map((node) => node.textContent)
      .join("");
  }

  it("shows the real host next to link text that names another site", () => {
    const link = firstLink(
      "[https://mybank.example/login](https://evil.example/phish)",
    );
    expect(link.getAttribute("href")).toBe("https://evil.example/phish");
    expect(link.textContent).toContain("https://mybank.example/login");
    expect(visibleHost(link)).toBe("(evil.example)");
    expect(link.querySelector(".maya-md__host")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(spoken(link)).toContain("(goes to evil.example)");
    expect(spoken(link)).toContain("(opens in a new tab)");
  });

  it.each([
    ["[mybank.example](https://evil.example/)", "evil.example"],
    ["[Visit mybank.example today](https://evil.example/)", "evil.example"],
    ["[**mybank.example**](https://evil.example/)", "evil.example"],
    [
      "[https://mybank.example@evil.example/](https://evil.example/)",
      "evil.example",
    ],
    [
      "[https://mybank.example.evil.example/](https://evil.example/)",
      "evil.example",
    ],
  ])("shows the host for %s", (source, host) => {
    expect(visibleHost(firstLink(source))).toBe(`(${host})`);
  });

  it("shows the host of every ordinary link too", () => {
    const link = firstLink("[the docs](https://docs.example.com/guide)");
    expect(visibleHost(link)).toBe("(docs.example.com)");
    expect(spoken(link)).toContain("(goes to docs.example.com)");
  });

  it("includes a non-default port and an international host in its ASCII form", () => {
    expect(visibleHost(firstLink("[x](https://a.example:8443/)"))).toBe(
      "(a.example:8443)",
    );
    expect(
      visibleHost(
        firstLink(
          "[x](https://b" + String.fromCharCode(0xfc) + "cher.example/)",
        ),
      ),
    ).toBe("(xn--bcher-kva.example)");
  });

  it("shows the address for a mailto link", () => {
    expect(visibleHost(firstLink("[mail us](mailto:team@example.com)"))).toBe(
      "(team@example.com)",
    );
  });

  it("does not repeat the host when the link text is the address itself", () => {
    for (const source of [
      "https://example.com/a",
      "<https://example.com/a>",
      "[https://example.com/a](https://example.com/b)",
      "<mailto:a@example.com>",
    ]) {
      const link = firstLink(source);
      expect(visibleHost(link), source).toBeNull();
      expect(spoken(link), source).not.toContain("goes to");
    }
  });

  it("keeps the host out of the underlined link text", () => {
    const link = firstLink("[docs](https://example.com/)");
    expect(link.querySelector(".maya-md__host")?.parentElement).toBe(link);
    expect(link.textContent).toContain("docs (example.com)");
  });

  it("strips direction controls from link text so they cannot reorder the host", () => {
    const link = firstLink(
      `[${RLO}https://mybank.example${PDF}](https://evil.example/)`,
    );
    expect(link.textContent).not.toContain(RLO);
    expect(link.textContent).not.toContain(PDF);
    expect(visibleHost(link)).toBe("(evil.example)");
  });

  it("marks direction controls in inline code instead of obeying them", () => {
    const { container } = renderMarkdown(`run \`a${RLO}b\``);
    const code = container.querySelector("code.maya-md__code");
    expect(code?.textContent).toBe("a<U+202E>b");
  });

  it("still allows only http, https and mailto links, with no credentials", () => {
    const { container } = renderMarkdown(
      [
        "[a](https://u:p@evil.example/)",
        "[b](javascript:alert(1))",
        "[c](ftp://example.com/)",
        "[d](https://ok.example/)",
      ].join("\n\n"),
    );
    const hrefs = Array.from(container.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toEqual(["https://ok.example/"]);
  });
});

describe("MarkdownRenderer: security", () => {
  const rawHtml = [
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(1)">',
    '<a href="javascript:alert(1)" onclick="alert(2)">x</a>',
    '<iframe src="https://evil.example"></iframe>',
    "<style>body{display:none}</style>",
    "<svg onload=alert(1)>",
    '<div style="position:fixed">overlay</div>',
    "&lt;script&gt;alert(1)&lt;/script&gt;",
  ];

  it.each(rawHtml)("shows raw HTML as text: %s", (source) => {
    const { container } = renderMarkdown(source);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector("style")).toBeNull();
    expect(container.querySelector("svg")).toBeNull();
    expect(container.querySelector("div[style]")).toBeNull();
    // A bare https:// URL inside the text may become a plain safe link; nothing
    // else can become a link.
    for (const anchor of Array.from(container.querySelectorAll("a"))) {
      expect(anchor.getAttribute("href")).toMatch(/^(https?:|mailto:)/);
    }
    expect(container.textContent).toContain(source.slice(0, 12));
    // No attribute from the attack ever reaches the DOM.
    expectNoEventHandlerAttributes(container);
  });

  it("keeps HTML inside bold, links and headings inert", () => {
    const { container } = renderMarkdown(
      "**<img src=x onerror=alert(1)>**\n\n# <script>alert(1)</script>\n\n[<b onmouseover=alert(1)>x</b>](https://example.com)",
    );
    expect(container.querySelector("img, script, b")).toBeNull();
    expectNoEventHandlerAttributes(container);
    expect(container.querySelector("a")?.getAttribute("href")).toBe(
      "https://example.com/",
    );
  });

  it.each([
    "[click](javascript:alert(1))",
    "[click](JaVaScRiPt:alert(1))",
    "[click](  javascript:alert(1))",
    "[click](java\tscript:alert(1))",
    "[click](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "[click](vbscript:msgbox(1))",
    "[click](//evil.example.com)",
    "[click](https://user:secret@evil.example.com)",
    "<javascript:alert(1)>",
    "javascript:alert(1)",
    "[click](&#106;avascript:alert(1))",
    "[click](javascript&colon;alert(1))",
    "[click](javascript&#58;alert(1))",
    `[click](${NBSP}javascript:alert(1))`,
    `[click](${ZWSP}javascript:alert(1))`,
    `[click](${RLO}javascript:alert(1))`,
  ])("creates no link for the dangerous destination in %s", (source) => {
    const { container } = renderMarkdown(source);
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("[href]")).toBeNull();
    // The source stays visible as plain text, so nothing is hidden from the reader.
    expect(container.textContent).toBe(source);
  });

  it("shows the source text of a refused link so nothing is hidden", () => {
    const { container } = renderMarkdown("[click](javascript:alert(1))");
    expect(container.textContent).toContain("javascript:alert(1)");
  });

  it("does not load images: no img element and no remote request, alt text shown", () => {
    const { container } = renderMarkdown(
      "![tracking pixel](https://tracker.example/p.png)\n\n![](https://tracker.example/q.png)\n\n![x](javascript:alert(1))",
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[src]")).toBeNull();
    expect(container.textContent).toContain("tracking pixel");
    expect(container.textContent).toContain("[image]");
    expect(container.textContent).not.toContain("tracker.example");
  });

  it("renders an image inside a link as the alt text inside the link only", () => {
    const { container } = renderMarkdown(
      "[![logo](https://cdn.example/l.png)](https://example.com)",
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")?.textContent).toContain("logo");
  });

  it("handles malformed and nested markdown without throwing or leaking markup", () => {
    const samples = [
      "**[unclosed link](https://example.com",
      "[a]( [b]( [c](https://example.com) ) )",
      "*a **b *c** d* e*",
      "```\n</pre><script>alert(1)</script>",
      "> > > > > <img src=x onerror=1>",
      "- <b>x</b>\n  - <script>1</script>",
      "`<script>alert(1)</script>`",
      '[x](https://example.com/"onmouseover="alert(1))',
    ];
    for (const sample of samples) {
      const { container, unmount } = renderMarkdown(sample);
      expect(container.querySelector("script, img, iframe")).toBeNull();
      expectNoEventHandlerAttributes(container);
      unmount();
    }
  });

  it("keeps a quote in a URL from becoming an attribute", () => {
    const { container } = renderMarkdown(
      '[x](https://example.com/"onmouseover="alert(1))',
    );
    const anchor = container.querySelector("a");
    for (const attribute of Array.from(anchor?.attributes ?? [])) {
      expect(attribute.name.startsWith("on")).toBe(false);
    }
  });

  it("renders extremely long lines and ReDoS-style input quickly", () => {
    const started = performance.now();
    for (const source of [
      "a".repeat(100_000),
      "**a ".repeat(10_000),
      "[a](".repeat(5_000),
      "`".repeat(20_000),
      "<".repeat(20_000),
      ">".repeat(20_000),
      "_a ".repeat(10_000),
    ]) {
      const { unmount } = renderMarkdown(source);
      unmount();
    }
    expect(performance.now() - started).toBeLessThan(15_000);
  });
});

describe("rendering never goes through innerHTML", () => {
  const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  const files = listSourceFiles(sourceRoot);

  it("scans every shipped source file, not a hand-picked list", () => {
    // If enumeration silently found nothing, the scan below would prove nothing.
    expect(files.length).toBeGreaterThanOrEqual(19);
    for (const required of [
      "lib/markdown.ts",
      "lib/api-client.ts",
      "lib/auth-session.ts",
      "lib/sse.ts",
      "hooks/useChatStream.ts",
      "components/chat/highlight.ts",
      "components/chat/CodeBlock.tsx",
      "components/chat/MarkdownRenderer.tsx",
      "components/chat/ChatNotice.tsx",
      "components/chat/ThemeToggle.tsx",
      "components/chat/useReducedMotion.ts",
      "components/chat/ChatWorkspace.tsx",
      "app/chat/page.tsx",
      "app/layout.tsx",
    ]) {
      expect(files).toContain(required);
    }
    expect(files.some((file) => /\.test\./.test(file))).toBe(false);
    expect(files.some((file) => file.startsWith("test/"))).toBe(false);
  });

  it.each(files)(
    "%s contains no invisible or direction-changing character",
    (file) => {
      // Write such a character as an escape, so a reader can see it.
      expect(readRaw(sourceRoot, file), file).not.toMatch(INVISIBLE_CHARACTERS);
    },
  );

  it.each(files)(
    "%s has no dangerouslySetInnerHTML, innerHTML, outerHTML, document.write or eval",
    (file) => {
      const code = readCode(sourceRoot, file);
      for (const [name, pattern] of FORBIDDEN_PATTERNS) {
        expect(code, `${file} uses ${name}`).not.toMatch(pattern);
      }
    },
  );
});
