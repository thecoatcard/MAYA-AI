"use client";

import React, { useState } from "react";

import { MarkdownRenderer } from "./MarkdownRenderer";

/**
 * A scheduled report (Markdown) shown rendered: headings, bullets, numbered sources. It scrolls
 * inside its own box (the keyboard can focus it) so a long report never stretches the page, and
 * a "Show full report" toggle lifts the height limit when the person wants to read it in place.
 */
export function ReportBox({
  markdown,
  label,
}: {
  readonly markdown: string;
  readonly label: string;
}): React.JSX.Element {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="grid min-w-0 gap-1">
      <div
        role="region"
        aria-label={label}
        tabIndex={0}
        dir="auto"
        data-testid="report-box"
        data-expanded={expanded ? "true" : "false"}
        className={`min-w-0 overflow-y-auto overscroll-contain rounded-maya-md border border-maya-border bg-maya-bg p-3 text-maya-small text-maya-text [overflow-wrap:anywhere] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-maya-focus ${
          expanded ? "max-h-none" : "max-h-[min(24rem,50vh)]"
        }`}
      >
        <MarkdownRenderer content={markdown} />
      </div>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
        className="justify-self-start text-maya-small text-maya-link underline"
      >
        {expanded ? "Show less" : "Show full report"}
      </button>
    </div>
  );
}
