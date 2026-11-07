"use client";

import React from "react";
import { ExternalLink, BookOpen } from "lucide-react";

export interface CitationItem {
  title: string;
  url: string;
  snippet?: string;
}

export interface ResearchCitationsViewProps {
  citations: CitationItem[];
  className?: string;
}

export function ResearchCitationsView({
  citations,
  className = "",
}: ResearchCitationsViewProps): React.JSX.Element {
  if (!citations || citations.length === 0) {
    return <></>;
  }

  return (
    <div
      className={`border rounded-lg overflow-hidden border-zinc-800 bg-zinc-950 p-3 text-xs ${className}`}
    >
      <div className="flex items-center gap-1.5 font-semibold text-zinc-300 mb-2.5 pb-2 border-b border-zinc-800">
        <BookOpen className="w-3.5 h-3.5 text-indigo-400" />
        <span>Verified Research Citations ({citations.length})</span>
      </div>

      <div className="space-y-2">
        {citations.map((c, idx) => (
          <div
            key={idx}
            className="p-2 rounded bg-zinc-900/60 border border-zinc-800/80"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-zinc-200 truncate">
                {c.title || c.url}
              </span>
              <a
                href={c.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-indigo-400 hover:text-indigo-300 font-mono text-[11px] shrink-0"
              >
                <span>Link</span>
                <ExternalLink className="w-3 h-3" />
              </a>
            </div>
            {c.snippet && (
              <p className="mt-1 text-zinc-400 text-[11px] leading-relaxed line-clamp-2">
                {c.snippet}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
