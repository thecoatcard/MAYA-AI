"use client";

import React from "react";
import { Plus, Minus, FileCode } from "lucide-react";

export interface GitDiffViewerProps {
  diffText: string;
  filePath?: string;
  className?: string;
}

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
      className={`flex flex-col border rounded-lg overflow-hidden border-zinc-800 bg-zinc-950 text-xs font-mono ${className}`}
    >
      {/* File Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-zinc-900 border-b border-zinc-800 text-zinc-300">
        <div className="flex items-center gap-2">
          <FileCode className="w-4 h-4 text-zinc-400" />
          <span className="font-semibold">{filePath}</span>
        </div>
        <div className="flex items-center gap-2 font-medium">
          <span className="flex items-center text-emerald-400">
            <Plus className="w-3 h-3" />
            {additions}
          </span>
          <span className="flex items-center text-rose-400">
            <Minus className="w-3 h-3" />
            {deletions}
          </span>
        </div>
      </div>

      {/* Diff Lines */}
      <div className="p-2 overflow-x-auto max-h-96 text-zinc-200">
        {lines.map((line, idx) => {
          let lineClass = "text-zinc-400";
          if (line.startsWith("@@")) {
            lineClass = "text-cyan-400 bg-cyan-950/30 px-1 rounded";
          } else if (line.startsWith("+") && !line.startsWith("+++")) {
            lineClass = "text-emerald-300 bg-emerald-950/40 px-1";
          } else if (line.startsWith("-") && !line.startsWith("---")) {
            lineClass = "text-rose-300 bg-rose-950/40 px-1";
          }

          return (
            <div
              key={idx}
              className={`leading-5 whitespace-pre flex ${lineClass}`}
            >
              <span className="w-8 select-none text-zinc-600 text-right pr-3 inline-block">
                {idx + 1}
              </span>
              <span className="flex-1">{line || " "}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
