import { describe, expect, it } from "vitest";

import { latexToText } from "./latex-text";
import { parseMarkdown, type Block, type Inline } from "./markdown";

function text(tex: string): string {
  const out = latexToText(tex);
  expect(out.ok).toBe(true);
  return out.text;
}

function inlineMath(source: string): Inline[] {
  const block = parseMarkdown(source)[0];
  return block.type === "paragraph" ? block.children : [];
}

describe("latexToText", () => {
  it("converts the strings from the reported answer", () => {
    expect(text("O(n^2)")).toBe("O(n²)");
    expect(text("O(n)")).toBe("O(n)");
    expect(
      text("\\text{complement} = \\text{target} - \\text{current number}"),
    ).toBe("complement = target - current number");
  });

  it("handles scripts, fractions, roots and symbols", () => {
    expect(text("2^{10}")).toBe("2¹⁰");
    expect(text("x^{n+1}")).toBe("xⁿ⁺¹");
    expect(text("a_{ij}")).toBe("aᵢⱼ");
    expect(text("x^y")).toBe("x^y");
    expect(text("e^{xy}")).toBe("e^(xy)");
    expect(text("\\frac{a}{b}")).toBe("a/b");
    expect(text("\\frac{n+1}{2}")).toBe("(n+1)/2");
    expect(text("\\sqrt{x}")).toBe("√x");
    expect(text("\\sqrt{a+b}")).toBe("√(a+b)");
    expect(text("a \\times b \\cdot c")).toBe("a × b · c");
    expect(text("a \\le b \\ge c \\neq d \\approx e")).toBe(
      "a ≤ b ≥ c ≠ d ≈ e",
    );
    expect(text("\\sum_{i=1}^{n} i")).toBe("Σᵢ₌₁ⁿ i");
    expect(text("\\alpha + \\beta \\to \\infty")).toBe("α + β → ∞");
    expect(text("x \\in \\mathbb{R}")).toBe("x ∈ ℝ");
    expect(text("\\log n")).toBe("log n");
    expect(text("\\left( \\frac{a}{b} \\right)")).toBe("( a/b )");
  });

  it("handles nested braces", () => {
    expect(text("\\frac{\\frac{a}{b}}{c}")).toBe("(a/b)/c");
    expect(text("\\text{a {b} c}")).toBe("a b c");
  });

  it("refuses to half-convert: an unknown command returns the source", () => {
    const out = latexToText("\\binom{n}{k} + \\alpha");
    expect(out).toEqual({ text: "\\binom{n}{k} + \\alpha", ok: false });
    expect(latexToText("\\begin{matrix} a & b \\end{matrix}").ok).toBe(false);
    expect(latexToText("{unclosed").ok).toBe(false);
    expect(latexToText("").ok).toBe(false);
  });
});

describe("math in markdown", () => {
  it("parses inline dollars, parentheses and the escaped form", () => {
    const nodes = inlineMath("Time $O(n^2)$ and \\(x^2\\) here");
    const maths = nodes.filter((n) => n.type === "math");
    expect(maths.map((n) => (n.type === "math" ? n.text : ""))).toEqual([
      "O(n²)",
      "x²",
    ]);
    expect(maths[0]).toMatchObject({ tex: "O(n^2)", ok: true });
  });

  it("leaves currency alone", () => {
    for (const source of [
      "It costs $5 and $10 today",
      "Pay $5 or $10",
      "between $5-$10 maybe",
      "price is $ 5 and $ x",
    ]) {
      expect(inlineMath(source).some((n) => n.type === "math")).toBe(false);
    }
  });

  it("parses a display block on one or several lines", () => {
    const one = parseMarkdown(
      "$$\\text{complement} = \\text{target} - \\text{current number}$$",
    );
    expect(one).toHaveLength(1);
    expect(one[0]).toMatchObject({
      type: "math",
      text: "complement = target - current number",
      ok: true,
    });
    const many: Block[] = parseMarkdown(
      "before\n\n$$\nx^2 + y^2\n= z^2\n$$\n\nafter",
    );
    expect(many.map((b) => b.type)).toEqual(["paragraph", "math", "paragraph"]);
    const bracket = parseMarkdown("\\[ a \\le b \\]");
    expect(bracket[0]).toMatchObject({ type: "math", text: "a ≤ b" });
  });

  it("never touches code fences or inline code", () => {
    const fence = parseMarkdown("```js\nconst a = '$x^2$'; // $$ y $$\n```");
    expect(fence).toHaveLength(1);
    expect(fence[0].type).toBe("code");
    const inline = inlineMath("use `$x^2$` and `\\(y\\)` here");
    expect(inline.some((n) => n.type === "math")).toBe(false);
    expect(inline.filter((n) => n.type === "code")).toHaveLength(2);
  });

  it("keeps an unknown command as an unconverted math node", () => {
    const nodes = inlineMath("see $\\binom{n}{k}$ ok");
    const math = nodes.find((n) => n.type === "math");
    expect(math).toMatchObject({ ok: false, tex: "\\binom{n}{k}" });
  });
});
