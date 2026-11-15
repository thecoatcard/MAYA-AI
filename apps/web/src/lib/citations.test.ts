import { describe, expect, it } from "vitest";

import {
  normaliseCitationMarkup,
  prepareCitations,
  splitSources,
} from "./citations";

describe("normaliseCitationMarkup", () => {
  it("repairs the shapes seen in real answers", () => {
    expect(normaliseCitationMarkup('Oil rose "[2]" (www.msn.com) today')).toBe(
      "Oil rose [2] today",
    );
    expect(
      normaliseCitationMarkup(
        'Cuts said ["[4]"](https://indianexpress.com/article/world) there',
      ),
    ).toBe("Cuts said [4] there");
    expect(normaliseCitationMarkup("see [[3]](https://x.test/a) now")).toBe(
      "see [3] now",
    );
    expect(normaliseCitationMarkup("a [5](https://x.test/p?q=1) b")).toBe(
      "a [5] b",
    );
  });

  it("is tolerant of text cut off while streaming", () => {
    expect(
      normaliseCitationMarkup(
        'Cuts said ["[4]"](https://indianexpress.com/article/world',
      ),
    ).toBe("Cuts said [4]");
    expect(normaliseCitationMarkup('Cuts said ["[4')).toBe("Cuts said ");
    expect(normaliseCitationMarkup("plain [2] stays")).toBe("plain [2] stays");
  });
});

const SOURCES = `

**Sources**

1. [Reuters report](https://www.reuters.com/a) · reuters.com
2. [MSN](https://www.msn.com/b) · msn.com`;

describe("prepareCitations", () => {
  it("links [n] to the n-th source and leaves unknown numbers as text", () => {
    const out = prepareCitations(`Up [1] and down [2], unknown [9].${SOURCES}`);
    expect(out).toContain("[[1]](https://www.reuters.com/a)");
    expect(out).toContain("[[2]](https://www.msn.com/b)");
    expect(out).toContain("unknown [9].");
    expect(out).not.toContain("[[9]]");
  });

  it("never turns a model-written URL into a link: the list is the only source", () => {
    const out = prepareCitations(
      `Said ["[1]"](https://evil.test/x).${SOURCES}`,
    );
    expect(out).not.toContain("evil.test");
    expect(out).toContain("[[1]](https://www.reuters.com/a)");
  });

  it("leaves code alone and does nothing without a list", () => {
    expect(prepareCitations("no list [1] here")).toBe("no list [1] here");
    expect(prepareCitations(`use \`a[1]\` and [2]${SOURCES}`)).toContain(
      "`a[1]`",
    );
  });

  it("reads the list", () => {
    expect(splitSources(`x${SOURCES}`).sources.size).toBe(2);
  });
});
