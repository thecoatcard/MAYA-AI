import { describe, expect, it } from "vitest";

import {
  bookFacts,
  chapterPieces,
  firstLines,
  outlineOf,
} from "./book-outline";

const BOOK = [
  "*A story-based book*",
  "",
  "[toc]",
  "",
  "# Chapter 1: Start",
  "",
  "## Part one",
  "",
  "### Understanding",
  "",
  "Some words here for the reader.",
  "",
  "```python",
  "# not a heading",
  "print('x')",
  "```",
  "",
  "| A | B |",
  "|---|---|",
  "| 1 | 2 |",
  "",
  "$$ a = b $$",
  "",
  "# Chapter 2: Next",
  "",
  "## Part two",
  "",
  "More words.",
].join("\n");

describe("book outline helpers", () => {
  it("finds headings outside code fences only, never deeper than level 3", () => {
    expect(outlineOf(BOOK)).toEqual([
      { level: 1, text: "Chapter 1: Start" },
      { level: 2, text: "Part one" },
      { level: 3, text: "Understanding" },
      { level: 1, text: "Chapter 2: Next" },
      { level: 2, text: "Part two" },
    ]);
    expect(outlineOf("#### deep")).toEqual([{ level: 3, text: "deep" }]);
  });

  it("counts chapters, code blocks, tables, equations and words without code", () => {
    const facts = bookFacts(BOOK);
    expect(facts.chapters).toBe(2);
    expect(facts.codeBlocks).toBe(1);
    expect(facts.tables).toBe(1);
    expect(facts.equations).toBe(1);
    expect(facts.words).toBeGreaterThan(10);
    expect(facts.words).toBeLessThan(60); // the python lines are not words
  });

  it("leaves the contents marker out of the preview and the pieces", () => {
    expect(firstLines(BOOK, 5)).not.toContain("[toc]");
    expect(firstLines(BOOK, 3).split("\n")).toHaveLength(3);
    expect(chapterPieces(BOOK).join("\n")).not.toContain("[toc]");
  });

  it("cuts the book at each chapter heading and keeps fenced hashes inside", () => {
    const pieces = chapterPieces(BOOK);
    expect(pieces).toHaveLength(3); // the intro line, chapter 1, chapter 2
    expect(pieces[1]).toContain("# not a heading");
    expect(pieces[1]).toContain("print('x')");
    expect(pieces[2]?.startsWith("# Chapter 2: Next")).toBe(true);
    expect(chapterPieces("")).toEqual([]);
  });
});
