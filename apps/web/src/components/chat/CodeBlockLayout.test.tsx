import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { MarkdownRenderer } from "./MarkdownRenderer";

describe("fenced code in assistant text", () => {
  it("renders a long code block as a scrollable region with a copy button", () => {
    const longLine = "const value = " + "x".repeat(400) + ";";
    const content = ["Here is the code:", "", "```ts", longLine, "```"].join(
      "\n",
    );
    render(<MarkdownRenderer content={content} streaming={false} />);
    const region = screen.getByRole("region", { name: /ts code|code/i });
    expect(region.tagName).toBe("PRE");
    // The block scrolls sideways inside itself; the page does not.
    expect(region.className).toContain("overflow-auto");
    expect(region.className).toContain("max-w-full");
    expect(region).toHaveAttribute("tabindex", "0");
    expect(
      screen.getByRole("button", { name: "Copy code" }),
    ).toBeInTheDocument();
  });
});
