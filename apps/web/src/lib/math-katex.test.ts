import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { beforeAll, describe, expect, it } from "vitest";

import {
  loadKatex,
  sanitizeMathHtml,
  typesetMath,
  type KatexApi,
} from "./math-katex";

function html(fragment: DocumentFragment | null): string {
  const div = document.createElement("div");
  if (fragment) div.appendChild(fragment);
  return div.innerHTML;
}

describe("sanitizeMathHtml", () => {
  it("drops script, style, anchors, images and foreignObject with their content", () => {
    const out = html(
      sanitizeMathHtml(
        '<span class="a">ok</span><script>alert(1)</script><style>*{}</style>' +
          '<a href="javascript:alert(1)">x</a><img src=x onerror=alert(1)>' +
          "<svg><foreignObject><span>z</span></foreignObject><path d='M0'/></svg>",
      ),
    );
    expect(out).toContain('<span class="a">ok</span>');
    expect(out).not.toMatch(/script|style|<a|img|foreignObject|alert/i);
    expect(out).toContain("<path");
  });

  it("drops event handlers, href, unknown attributes and data- attributes", () => {
    const out = html(
      sanitizeMathHtml(
        '<span onclick="x()" href="https://e.example" data-x="1" id="i" class="k">t</span>',
      ),
    );
    expect(out).toBe('<span class="k">t</span>');
  });

  it("drops javascript:/data: values even when obfuscated", () => {
    const out = html(
      sanitizeMathHtml(
        '<span class="java&#9;script:x">a</span><span class="ok" width="data:text/html,1">b</span>',
      ),
    );
    expect(out).not.toMatch(/javascript|data:/i);
  });

  it("keeps plain style declarations and removes url(), expression and unknown properties", () => {
    const out = html(
      sanitizeMathHtml(
        '<span style="height:1.5em;margin-right:0.05em;background:url(x);position:fixed;color:expression(a)">a</span>',
      ),
    );
    expect(out).toContain('style="height:1.5em;margin-right:0.05em"');
    expect(out).not.toMatch(/url|expression|fixed/);
  });

  it("removes a style attribute that ends up empty, and comments", () => {
    const out = html(
      sanitizeMathHtml('<span style="background:url(x)"><!-- c -->a</span>'),
    );
    expect(out).toBe("<span>a</span>");
  });

  it("returns an empty fragment for hostile-only input", () => {
    expect(sanitizeMathHtml("<script>1</script>").childNodes).toHaveLength(0);
  });
});

describe("typesetMath", () => {
  let katex: KatexApi;
  beforeAll(async () => {
    katex = await loadKatex();
  });
  const render = (tex: string, display = false) =>
    html(typesetMath(katex, tex, display));

  it.each([
    ["inline power", "x^2 + y^2 = z^2"],
    ["fraction", "\\frac{a}{b}"],
    ["sum", "\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}"],
    ["greek", "\\alpha + \\beta = \\Gamma"],
    ["sqrt", "\\sqrt{x^2+1}"],
    ["matrix", "\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}"],
    ["aligned", "\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}"],
    ["macro", "x \\in \\R"],
  ])("typesets %s", (_name, tex) => {
    const out = render(tex);
    expect(out).toContain("katex");
    expect(out).toContain("<math");
    expect(out).not.toMatch(/katex-error/);
  });

  it("uses display mode for display nodes", () => {
    expect(render("x", true)).toContain("katex-display");
    expect(render("x", false)).not.toContain("katex-display");
  });

  it("returns null for invalid formulas and unknown commands", () => {
    expect(typesetMath(katex, "\\frac{a", false)).toBeNull();
    expect(typesetMath(katex, "\\nosuchcommand{x}", false)).toBeNull();
  });

  it.each([
    "\\href{javascript:alert(1)}{x}",
    "\\url{javascript:alert(1)}",
    "\\htmlClass{evil}{x}",
    "\\htmlData{a=b}{x}",
    "\\htmlId{evil}{x}",
    "\\htmlStyle{color:red}{x}",
    "\\includegraphics{http://e.example/x.png}",
  ])("produces no active content for %s", (tex) => {
    const out = html(typesetMath(katex, tex, false));
    expect(out).not.toMatch(/<a[\s>]|<img|href|javascript|onerror|<script/i);
    expect(out).not.toMatch(/data-|id="evil"|class="[^"]*evil/);
  });

  it("refuses huge macro expansion and clamps huge sizes", () => {
    const tex =
      "\\def\\a{bbbbbbbbbb}\\def\\b{\\a\\a\\a\\a\\a\\a\\a\\a\\a\\a}" +
      "\\def\\c{\\b\\b\\b\\b\\b\\b\\b\\b\\b\\b}\\c\\c\\c\\c\\c\\c\\c\\c\\c\\c";
    expect(typesetMath(katex, tex, false)).toBeNull();
    // maxSize clamps a huge rule to 20em instead of rendering 1000em.
    const rule = html(typesetMath(katex, "\\rule{1000em}{1000em}", false));
    expect(rule).toContain("20em");
    // (the TeX source in the MathML annotation still says 1000em; only styles matter)
    expect(rule).not.toMatch(/(style|width|height)="[^"]*1000em/);
  });
});

describe("bundled katex stylesheet", () => {
  it("references no remote URL and only local font files", () => {
    const require = createRequire(import.meta.url);
    const css = readFileSync(
      require.resolve("katex/dist/katex.min.css"),
      "utf8",
    );
    expect(css).not.toMatch(/https?:\/\//i);
    expect(css).not.toMatch(/\/\//);
    const urls = Array.from(css.matchAll(/url\(([^)]+)\)/g)).map((m) => m[1]);
    expect(urls.length).toBeGreaterThan(0);
    for (const url of urls) expect(url).toMatch(/^fonts\/[\w-]+\.(woff2?|ttf)/);
  });
});
