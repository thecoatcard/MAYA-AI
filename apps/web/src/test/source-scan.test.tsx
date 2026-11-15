import { describe, expect, it } from "vitest";

import { FORBIDDEN_PATTERNS, stripComments } from "./source-scan";

function flagged(source: string): string[] {
  const code = stripComments(source);
  return FORBIDDEN_PATTERNS.filter(([, pattern]) => pattern.test(code)).map(
    ([name]) => name,
  );
}

describe("stripComments", () => {
  it("removes line and block comments", () => {
    const code = stripComments(
      ["const a = 1; // el.innerHTML = x", "/* eval(y) */ const b = 2;"].join(
        "\n",
      ),
    );
    expect(code).toContain("const a = 1;");
    expect(code).toContain("const b = 2;");
    expect(code).not.toContain("innerHTML");
    expect(code).not.toContain("eval");
  });

  it("keeps code that follows a URL string on the same line", () => {
    // The old regex deleted everything after the first // on a line, including
    // the call that follows this string.
    const source = 'const u = "https://example.com"; el.innerHTML = value;';
    expect(stripComments(source)).toBe(source);
    expect(flagged(source)).toEqual(["innerHTML"]);
  });

  it("keeps code that follows a single-quoted or template URL", () => {
    expect(flagged("fetch('http://x.test/a'); eval(code);")).toEqual(["eval("]);
    expect(
      flagged("const u = `http://x.test/${id}`; document.write(u);"),
    ).toEqual(["document.write"]);
  });

  it("does not treat an escaped quote as the end of a string", () => {
    const source = 'const s = "say \\"//\\" now"; el.outerHTML = s;';
    expect(flagged(source)).toEqual(["outerHTML"]);
  });

  it("keeps code after a regular expression that contains escaped slashes", () => {
    const source = "const ok = /^https?:\\/\\//.test(raw); eval(raw);";
    expect(flagged(source)).toEqual(["eval("]);
  });

  it("drops a mention that is only in a comment", () => {
    expect(
      flagged("// never use dangerouslySetInnerHTML\nconst a = 1;"),
    ).toEqual([]);
    expect(
      flagged("/**\n * innerHTML is not used.\n */\nconst a = 1;"),
    ).toEqual([]);
  });

  it("still flags a real use that has a comment after it", () => {
    expect(flagged("el.innerHTML = x; // trusted")).toEqual(["innerHTML"]);
    expect(flagged("<div dangerouslySetInnerHTML={{ __html: x }} />")).toEqual([
      "dangerouslySetInnerHTML",
    ]);
  });

  it("recovers at the end of the line after a stray quote", () => {
    // An apostrophe in JSX text opens a string that is never closed on its line.
    const source = [
      "<p>Don't do that</p>",
      "el.insertAdjacentHTML('beforeend', x);",
    ].join("\n");
    expect(flagged(source)).toEqual(["insertAdjacentHTML"]);
  });

  it("flags string-based timers and Function constructors", () => {
    expect(flagged('setTimeout("run()", 10);')).toEqual([
      "setTimeout with a string",
    ]);
    expect(flagged("const f = new Function('return 1');")).toEqual([
      "new Function(",
    ]);
  });

  it("does not flag ordinary code", () => {
    expect(
      flagged(
        'const total = items.reduce((a, b) => a + b, 0); setTimeout(() => tick(), 10); return <p className="x">{text}</p>;',
      ),
    ).toEqual([]);
  });
});
