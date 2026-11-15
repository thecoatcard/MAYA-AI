"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";

import { fetchArtifactText } from "../../lib/artifacts-client";
import {
  bookFacts,
  chapterPieces,
  firstLines,
  type BookFacts,
} from "../../lib/book-outline";
import { MarkdownRenderer } from "../chat/MarkdownRenderer";
import { Button, WrapText } from "../ui";

/** Tool name of the whole-book Notion write (backend app/mcp/notion_book.py). */
export const NOTION_BOOK_TOOL = "notion_create_page_from_job";

const PREVIEW_LINES = 40;
const PIECES_AT_FIRST = 2;
const PIECES_PER_STEP = 3;
const OUTLINE_LIMIT = 60;

type Json = Record<string, unknown>;

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

interface Loaded {
  readonly state: "loading" | "ready" | "failed";
  readonly text: string;
}

/**
 * The approval of a whole book for Notion: what will be created, the chapter outline, the counts,
 * the first lines, and the full text on demand (rendered with the chat's Markdown renderer, a few
 * chapters at a time, so ten thousand words never freeze the page). The exact payload stays in the
 * approval's own "exact payload" view. Approve and Reject are not here: they stay in the approval.
 */
export function NotionBookPreview({
  args,
}: {
  readonly args: Json;
}): React.JSX.Element {
  const artifactId = str(args.content_artifact_id);
  const [loaded, setLoaded] = useState<Loaded>({
    state: artifactId === "" ? "failed" : "loading",
    text: "",
  });
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState(PIECES_AT_FIRST);
  const sentinel = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (artifactId === "") return;
    let live = true;
    setLoaded({ state: "loading", text: "" });
    fetchArtifactText(artifactId)
      .then((text) => {
        if (live) setLoaded({ state: "ready", text });
      })
      .catch(() => {
        if (live) setLoaded({ state: "failed", text: "" });
      });
    return () => {
      live = false;
    };
  }, [artifactId]);

  const facts: BookFacts | null = useMemo(
    () => (loaded.state === "ready" ? bookFacts(loaded.text) : null),
    [loaded],
  );
  const pieces = useMemo(
    () => (loaded.state === "ready" && open ? chapterPieces(loaded.text) : []),
    [loaded, open],
  );

  useEffect(() => {
    const node = sentinel.current;
    if (!open || node === null || typeof IntersectionObserver === "undefined")
      return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting))
        setShown((n) => n + PIECES_PER_STEP);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [open, shown, pieces.length]);

  const counts: [string, number | null][] = [
    ["Words", facts?.words ?? num(args.words)],
    ["Chapters", facts?.chapters ?? num(args.chapters)],
    ["Blocks in Notion", num(args.blocks)],
    ["Code blocks", facts?.codeBlocks ?? num(args.code_blocks)],
    ["Tables", facts?.tables ?? num(args.tables)],
    ["Equations", facts?.equations ?? num(args.equations)],
  ];
  const outline = (facts?.outline ?? [])
    .filter((h) => h.level <= 2)
    .slice(0, OUTLINE_LIMIT);

  return (
    <div
      className="flex min-w-0 flex-col gap-3 text-maya-small"
      data-testid="notion-book-preview"
    >
      <dl className="m-0 grid min-w-0 grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-x-4 gap-y-2">
        <div className="min-w-0 [grid-column:1/-1]">
          <dt className="maya-label">Page title</dt>
          <dd className="m-0 font-medium">
            <WrapText dir="auto">
              {`${str(args.icon)} ${str(args.title)}`.trim()}
            </WrapText>
          </dd>
        </div>
        <div className="min-w-0 [grid-column:1/-1]">
          <dt className="maya-label">Created under</dt>
          <dd className="m-0 font-mono text-maya-mono [overflow-wrap:anywhere]">
            {str(args.parent_label) !== ""
              ? `${str(args.parent_label)} (${str(args.parent_page_id)})`
              : str(args.parent_page_id)}
          </dd>
        </div>
        {counts.map(([label, value]) =>
          value === null ? null : (
            <div key={label} className="min-w-0">
              <dt className="maya-label">{label}</dt>
              <dd className="m-0">{value.toLocaleString("en-US")}</dd>
            </div>
          ),
        )}
      </dl>
      <p className="m-0 text-maya-secondary">
        Approving creates this page in Notion in batches of up to 100 blocks,
        then reads it back to check it. Nothing is written before you approve.
      </p>

      {loaded.state === "loading" && (
        <p role="status" className="m-0 text-maya-secondary">
          Loading the book text…
        </p>
      )}
      {loaded.state === "failed" && (
        <p className="m-0 text-maya-warning">
          The full text could not be loaded here. The counts above and the exact
          payload below are still what will be written.
        </p>
      )}

      {outline.length > 0 && (
        <div className="min-w-0">
          <h4 className="maya-label m-0 mb-1">Chapters</h4>
          <ul
            aria-label="Chapter outline"
            className="m-0 max-h-60 list-none overflow-y-auto p-0"
          >
            {outline.map((item, i) => (
              <li
                key={i}
                className={
                  item.level === 1 ? "font-medium" : "text-maya-secondary"
                }
                style={{ paddingInlineStart: item.level === 1 ? 0 : "1rem" }}
              >
                <WrapText dir="auto">{item.text}</WrapText>
              </li>
            ))}
          </ul>
        </div>
      )}

      {loaded.state === "ready" && (
        <div className="min-w-0">
          <h4 className="maya-label m-0 mb-1">First {PREVIEW_LINES} lines</h4>
          <div
            data-testid="book-first-lines"
            className="max-h-72 overflow-y-auto rounded-maya-md border border-maya-border p-2"
          >
            <MarkdownRenderer
              content={firstLines(loaded.text, PREVIEW_LINES)}
            />
          </div>
          <Button
            size="sm"
            variant="secondary"
            className="mt-2"
            aria-expanded={open}
            onClick={() => {
              setOpen((v) => !v);
              setShown(PIECES_AT_FIRST);
            }}
          >
            {open ? "Hide the full book" : "Read the full book"}
          </Button>
          {open && (
            <div
              data-testid="book-full-text"
              aria-label="Full book text"
              className="mt-2 max-h-[60vh] overflow-y-auto rounded-maya-md border border-maya-border p-3"
            >
              {pieces.slice(0, shown).map((piece, i) => (
                <MarkdownRenderer key={i} content={piece} />
              ))}
              {shown < pieces.length ? (
                <div ref={sentinel} className="pt-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setShown((n) => n + PIECES_PER_STEP)}
                  >
                    {`Show more chapters (${Math.min(shown, pieces.length)} of ${pieces.length} shown)`}
                  </Button>
                </div>
              ) : (
                <p className="m-0 pt-2 text-maya-secondary">
                  End of the book ({pieces.length} parts).
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
