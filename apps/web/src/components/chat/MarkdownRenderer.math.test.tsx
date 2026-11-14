import React from "react";
import { render, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownRenderer } from "./MarkdownRenderer";

async function typeset(content: string) {
  const view = render(<MarkdownRenderer content={content} />);
  await waitFor(() =>
    expect(view.container.querySelector(".katex")).not.toBeNull(),
  );
  return view;
}

describe("MarkdownRenderer typeset math", () => {
  it("shows the text fallback first, in a box with the same role and label", () => {
    // KaTeX is a lazy chunk: the first paint is the readable text, never an empty box.
    const { container } = render(<MarkdownRenderer content={"Is $x^2$ big"} />);
    const math = container.querySelector('[role="math"]');
    expect(math).not.toBeNull();
    expect(math?.getAttribute("aria-label")).toBe("x^2");
    expect(container.textContent).toContain("x²");
  });

  it("typesets inline math with the LaTeX as label and title", async () => {
    const { container } = await typeset("Euler: $e^{i\\pi} + 1 = 0$ holds.");
    const math = container.querySelector('span[role="math"]');
    expect(math?.getAttribute("aria-label")).toBe("e^{i\\pi} + 1 = 0");
    expect(math?.getAttribute("title")).toBe("e^{i\\pi} + 1 = 0");
    expect(math?.classList.contains("maya-math--inline")).toBe(true);
    expect(math?.querySelector("math")).not.toBeNull();
    expect(container.textContent).toContain("holds.");
  });

  it("typesets display math in a focusable, scrollable block", async () => {
    const { container } = await typeset(
      "$$\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}$$",
    );
    const block = container.querySelector('div[role="math"]');
    expect(block?.classList.contains("maya-math--block")).toBe(true);
    expect(block?.getAttribute("tabindex")).toBe("0");
    expect(block?.querySelector(".katex-display")).not.toBeNull();
  });

  it("typesets matrices and aligned environments", async () => {
    const { container } = await typeset(
      "$$\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}$$\n\n$$\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}$$",
    );
    expect(container.querySelectorAll(".katex-display")).toHaveLength(2);
  });

  it("leaves currency alone", async () => {
    const { container } = render(
      <MarkdownRenderer content={"It costs $5 and $10 and $x$."} />,
    );
    await waitFor(() =>
      expect(container.querySelector(".katex")).not.toBeNull(),
    );
    expect(container.textContent).toContain("It costs $5 and $10 and");
    expect(container.querySelectorAll('[role="math"]')).toHaveLength(1);
  });

  it("leaves code spans and fences untouched", async () => {
    const { container } = render(
      <MarkdownRenderer
        content={"`$x^2$`\n\n```\n$$y^2$$\n```\n\nand $z$ typeset"}
      />,
    );
    await waitFor(() =>
      expect(container.querySelector(".katex")).not.toBeNull(),
    );
    expect(container.querySelectorAll(".katex")).toHaveLength(1);
    expect(container.textContent).toContain("$x^2$");
    expect(container.textContent).toContain("$$y^2$$");
  });

  it("keeps the source visible when KaTeX rejects an invalid formula", async () => {
    const { container } = render(
      <MarkdownRenderer content={"Broken $\\frac{a$ here"} />,
    );
    // Let the lazy load settle, then check nothing was typeset.
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(container.querySelector(".katex")).toBeNull();
    expect(container.textContent).toContain("Broken");
    expect(container.textContent).toContain("here");
    // Either the source or its text form is visible; never an empty message.
    expect(container.textContent).toMatch(/\\frac\{a|frac|a\/|a$/);
  });

  it.each([
    "$\\href{javascript:alert(1)}{x}$",
    "$\\htmlClass{evil}{x}$",
    "$\\htmlData{a=b}{x}$",
    "$$\\url{javascript:alert(1)}$$",
  ])("creates no link, handler or script for %s", async (content) => {
    const { container } = render(<MarkdownRenderer content={content} />);
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(container.querySelector("a, script, style, img")).toBeNull();
    expect(container.querySelector(".evil")).toBeNull();
    expect(container.querySelector("[data-a]")).toBeNull();
    for (const element of Array.from(container.querySelectorAll("*"))) {
      for (const attribute of Array.from(element.attributes)) {
        expect(attribute.name.startsWith("on")).toBe(false);
        expect(attribute.name).not.toBe("href");
      }
    }
    expect(container.textContent?.length).toBeGreaterThan(0);
  });
});
