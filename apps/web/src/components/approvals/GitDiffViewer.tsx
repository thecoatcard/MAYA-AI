"use client";

import { FileCode, Minus, Plus } from "lucide-react";
import React from "react";

import { ScrollArea } from "../ui";

export interface GitDiffViewerProps {
  diffText: string;
  filePath?: string;
  className?: string;
}

/**
 * A unified diff. Added and removed lines carry their "+" and "-" characters
 * and a tinted background, so they never rely on colour alone. Long lines
 * scroll inside the diff box; the page never widens.
 */
export function GitDiffViewer({
  diffText,
  filePath = "code.patch",
  className = "",
}: GitDiffViewerProps): React.JSX.Element {
  const lines = diffText.split("\n");

  let additions = 0;
  let deletions = 0;
  for (const line of lines) {
    if (line.startsWith("+") && !line.startsWith("+++")) additions++;
    if (line.startsWith("-") && !line.startsWith("---")) deletions++;
  }

  return (
    <div
      className={`flex min-w-0 max-w-full flex-col rounded-maya-md overflow-hidden border border-maya-border bg-maya-code font-mono text-maya-mono ${className}`.trim()}
    >
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-maya-border bg-maya-raised px-3 py-2 text-maya-text">
        <span className="flex min-w-0 items-center gap-2">
          <FileCode
            aria-hidden="true"
            size={16}
            className="shrink-0 text-maya-secondary"
          />
          <span className="min-w-0 font-semibold [overflow-wrap:anywhere]">
            {filePath}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-3 font-medium">
          <span
            className="flex items-center gap-0.5 text-maya-success"
            title="Added lines"
          >
            <Plus aria-hidden="true" size={14} />
            {additions}
          </span>
          <span
            className="flex items-center gap-0.5 text-maya-danger"
            title="Removed lines"
          >
            <Minus aria-hidden="true" size={14} />
            {deletions}
          </span>
        </span>
      </div>

      <ScrollArea label="Diff lines" axis="both" className="max-h-96 p-2">
        <div className="min-w-max">
          {lines.map((line, idx) => {
            let lineClass = "text-maya-secondary";
            if (line.startsWith("@@")) {
              lineClass = "bg-maya-info-soft text-maya-info";
            } else if (line.startsWith("+") && !line.startsWith("+++")) {
              lineClass = "bg-maya-success-soft text-maya-text";
            } else if (line.startsWith("-") && !line.startsWith("---")) {
              lineClass = "bg-maya-danger-soft text-maya-text";
            }
            return (
              <div
                key={idx}
                className={`flex px-1 leading-5 whitespace-pre ${lineClass}`}
              >
                <span className="inline-block w-14 shrink-0 pr-3 text-right text-maya-muted select-none">
                  {idx + 1}
                </span>
                <span className="flex-1">{line || " "}</span>
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </div>
  );
}
