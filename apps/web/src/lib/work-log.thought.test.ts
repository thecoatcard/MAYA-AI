import { describe, expect, it } from "vitest";

import { prepareThought } from "./work-log";

describe("prepareThought", () => {
  it("puts a bold lead-in glued into one line on its own paragraph", () => {
    const out = prepareThought(
      "I will draft it. **Refining the Content** I am now focusing on Anand. **Crafting the Email Body** Next.",
    );
    expect(out).toBe(
      "I will draft it.\n\n**Refining the Content**\n\nI am now focusing on Anand.\n\n**Crafting the Email Body**\n\nNext.",
    );
  });

  it("leaves text that already has line breaks alone", () => {
    const text = "**Plan**\n\nDo the thing.";
    expect(prepareThought(text)).toBe(text);
  });

  it("returns plain text unchanged", () => {
    expect(prepareThought("Just thinking.")).toBe("Just thinking.");
  });
});
