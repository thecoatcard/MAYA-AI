"use client";

import React from "react";
import { FileCheck, Download, Hash } from "lucide-react";

export interface ArtifactCardProps {
  filename: string;
  checksum: string;
  sizeBytes?: number;
  downloadUrl?: string;
  className?: string;
}

export function ArtifactCard({
  filename,
  checksum,
  sizeBytes,
  downloadUrl,
  className = "",
}: ArtifactCardProps): React.JSX.Element {
  const formatBytes = (bytes?: number) => {
    if (!bytes || bytes <= 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB", "PB"];
    const i = Math.min(
      Math.floor(Math.log(bytes) / Math.log(k)),
      sizes.length - 1,
    );
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
  };

  return (
    <div
      className={`flex items-center justify-between p-3 border rounded-lg border-zinc-800 bg-zinc-950 text-xs font-mono ${className}`}
    >
      <div className="flex items-center gap-3">
        <div className="p-2 rounded bg-emerald-950/40 border border-emerald-500/30 text-emerald-400">
          <FileCheck className="w-4 h-4" />
        </div>
        <div>
          <div className="font-semibold text-zinc-200 text-sm">{filename}</div>
          <div className="flex items-center gap-2 text-zinc-500 text-[11px] mt-0.5">
            {sizeBytes !== undefined && <span>{formatBytes(sizeBytes)}</span>}
            <span>•</span>
            <span className="flex items-center gap-1 text-zinc-400">
              <Hash className="w-3 h-3 opacity-60" />
              <span>{checksum.slice(0, 12)}…</span>
            </span>
          </div>
        </div>
      </div>

      {downloadUrl && (
        <a
          href={downloadUrl}
          download={filename}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-zinc-900 border border-zinc-700 text-zinc-200 hover:bg-zinc-800 transition-colors"
        >
          <Download className="w-3.5 h-3.5" />
          <span>Download</span>
        </a>
      )}
    </div>
  );
}
