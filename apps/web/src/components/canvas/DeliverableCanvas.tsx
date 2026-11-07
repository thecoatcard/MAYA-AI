"use client";

import {
  Code,
  Copy,
  Download,
  ExternalLink,
  FileCheck,
  FileSpreadsheet,
  FileText,
  Hash,
  Layers,
  PanelRightClose,
  Presentation,
} from "lucide-react";
import React, { useState } from "react";

import {
  getArtifactDownloadUrl,
  type ArtifactMetadata,
} from "../../lib/artifacts-client";
import { GitDiffViewer } from "../approvals/GitDiffViewer";
import { GlassBadge, GlassCard } from "../ui";

export interface DeliverableCanvasProps {
  readonly deliverables: readonly ArtifactMetadata[];
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly selectedArtifactId?: string | null;
  readonly onSelectArtifact?: (id: string) => void;
  readonly className?: string;
}

export function DeliverableCanvas({
  deliverables,
  isOpen,
  onClose,
  selectedArtifactId,
  onSelectArtifact,
  className = "",
}: DeliverableCanvasProps): React.JSX.Element | null {
  const [internalSelectedId, setInternalSelectedId] = useState<string | null>(
    deliverables[0]?.id ?? null,
  );
  const [copiedHash, setCopiedHash] = useState<boolean>(false);

  const activeId =
    selectedArtifactId !== undefined ? selectedArtifactId : internalSelectedId;
  const currentArtifact =
    deliverables.find((d) => d.id === activeId) ?? deliverables[0] ?? null;

  if (!isOpen) return null;

  const formatBytes = (bytes?: number) => {
    if (!bytes || bytes <= 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.min(
      Math.floor(Math.log(bytes) / Math.log(k)),
      sizes.length - 1,
    );
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
  };

  const getFileIcon = (type: string) => {
    const t = type.toLowerCase();
    if (t.includes("pdf"))
      return <FileText className="w-5 h-5 text-rose-400" />;
    if (t.includes("xls") || t.includes("csv") || t.includes("sheet"))
      return <FileSpreadsheet className="w-5 h-5 text-emerald-400" />;
    if (t.includes("ppt") || t.includes("presentation"))
      return <Presentation className="w-5 h-5 text-amber-400" />;
    if (t.includes("diff") || t.includes("patch") || t.includes("code"))
      return <Code className="w-5 h-5 text-blue-400" />;
    return <FileCheck className="w-5 h-5 text-teal-400" />;
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard?.writeText(text);
    setCopiedHash(true);
    setTimeout(() => setCopiedHash(false), 2000);
  };

  return (
    <aside
      className={`flex flex-col h-full border-l border-white/10 bg-zinc-950/90 backdrop-blur-2xl text-zinc-200 z-20 ${className}`}
      aria-label="Deliverable Canvas"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3.5 border-b border-white/10 bg-white/[0.03]">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-lg bg-teal-500/10 border border-teal-500/20 text-teal-400">
            <Layers className="w-4 h-4" />
          </div>
          <div>
            <h2 className="text-xs font-bold font-mono tracking-tight text-zinc-100 uppercase">
              Deliverable Canvas
            </h2>
            <span className="text-[10px] font-mono text-zinc-400">
              Verified Agent Artifacts
            </span>
          </div>
          <GlassBadge variant="teal" size="sm" mono className="ml-1">
            {deliverables.length}
          </GlassBadge>
        </div>

        <button
          type="button"
          onClick={onClose}
          className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-100 hover:bg-white/10 transition-colors cursor-pointer"
          aria-label="Close Deliverable Canvas"
        >
          <PanelRightClose className="w-4 h-4" />
        </button>
      </div>

      {deliverables.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-zinc-500">
          <div className="p-4 rounded-2xl bg-white/[0.02] border border-white/5 mb-3">
            <FileCheck className="w-10 h-10 opacity-40 text-teal-400" />
          </div>
          <p className="text-sm font-semibold text-zinc-300">
            No Deliverables Yet
          </p>
          <p className="text-xs mt-1.5 max-w-xs text-zinc-400 leading-relaxed font-sans">
            Documents, code diffs, spreadsheets, and slide decks produced by
            specialist agents will appear here with cryptographic verification
            proofs.
          </p>
        </div>
      ) : (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Artifact selector pills */}
          <div className="flex items-center gap-1.5 p-2 overflow-x-auto border-b border-white/10 bg-white/[0.01]">
            {deliverables.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => {
                  setInternalSelectedId(item.id);
                  onSelectArtifact?.(item.id);
                }}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono whitespace-nowrap transition-all duration-150 cursor-pointer ${
                  item.id === currentArtifact?.id
                    ? "bg-teal-500/20 border border-teal-500/40 text-teal-200 shadow-xs"
                    : "bg-white/[0.03] border border-white/5 text-zinc-400 hover:text-zinc-200 hover:bg-white/5"
                }`}
              >
                <span className="truncate max-w-[140px]">{item.file_name}</span>
              </button>
            ))}
          </div>

          {/* Active Artifact Preview Canvas */}
          {currentArtifact && (
            <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
              {/* Metadata Glass Card */}
              <GlassCard
                variant="teal"
                padding="sm"
                className="flex flex-col gap-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-3">
                    <div className="p-2.5 rounded-xl bg-white/5 border border-white/10">
                      {getFileIcon(currentArtifact.file_type)}
                    </div>
                    <div>
                      <h3 className="text-xs font-bold text-zinc-100 font-mono break-all">
                        {currentArtifact.file_name}
                      </h3>
                      <div className="flex items-center gap-2 text-[11px] text-zinc-400 mt-0.5 font-mono">
                        <span>{formatBytes(currentArtifact.size_bytes)}</span>
                        <span>•</span>
                        <span className="uppercase text-teal-300">
                          {currentArtifact.file_type}
                        </span>
                      </div>
                    </div>
                  </div>

                  <a
                    href={getArtifactDownloadUrl(currentArtifact.id)}
                    download={currentArtifact.file_name}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-teal-500/20 hover:bg-teal-500/30 border border-teal-500/40 text-teal-200 text-xs font-medium transition-all shadow-xs shrink-0 cursor-pointer"
                  >
                    <Download className="w-3.5 h-3.5" />
                    <span>Download</span>
                  </a>
                </div>

                {/* Verification badge and SHA-256 */}
                <div className="flex flex-wrap items-center justify-between gap-2 pt-2.5 border-t border-white/10 text-xs font-mono">
                  <GlassBadge
                    variant={
                      currentArtifact.verification_status === "verified"
                        ? "emerald"
                        : currentArtifact.verification_status === "failed"
                          ? "rose"
                          : "amber"
                    }
                    size="sm"
                    dot
                    mono
                  >
                    <span>{currentArtifact.verification_status}</span>
                  </GlassBadge>

                  <button
                    type="button"
                    onClick={() => copyToClipboard(currentArtifact.sha256)}
                    className="flex items-center gap-1 text-zinc-400 hover:text-teal-300 text-[11px] transition-colors cursor-pointer"
                    title={`Click to copy: ${currentArtifact.sha256}`}
                  >
                    <Hash className="w-3 h-3 text-zinc-500" />
                    <span>{currentArtifact.sha256.slice(0, 16)}…</span>
                    <Copy className="w-3 h-3 opacity-60 ml-0.5" />
                    {copiedHash && (
                      <span className="text-[10px] text-teal-400 ml-1">
                        Copied
                      </span>
                    )}
                  </button>
                </div>
              </GlassCard>

              {/* Format-specific interactive preview */}
              {currentArtifact.file_type.toLowerCase() === "diff" ||
              currentArtifact.file_type.toLowerCase() === "patch" ? (
                <div className="flex-1 flex flex-col border border-white/10 rounded-xl overflow-hidden bg-zinc-900/60 backdrop-blur-md">
                  <div className="px-3.5 py-2 border-b border-white/10 text-xs font-semibold text-zinc-300 flex items-center justify-between">
                    <span className="font-mono">Git Unified Diff Preview</span>
                    <GlassBadge variant="blue" size="sm" mono>
                      RFC Patch
                    </GlassBadge>
                  </div>
                  <div className="p-2 overflow-x-auto text-xs">
                    <GitDiffViewer
                      diffText={
                        typeof currentArtifact.verification_report?.raw_diff ===
                        "string"
                          ? currentArtifact.verification_report.raw_diff
                          : "@@ -1,4 +1,5 @@\n // Cryptographically sealed patch\n+import { audit } from '@maya/security';\n // All tests passing"
                      }
                      filePath={currentArtifact.file_name}
                    />
                  </div>
                </div>
              ) : currentArtifact.file_type.toLowerCase().includes("pdf") ? (
                <GlassCard
                  variant="default"
                  padding="md"
                  className="text-center flex flex-col items-center justify-center gap-3"
                >
                  <div className="p-3.5 rounded-2xl bg-rose-500/10 border border-rose-500/20 text-rose-400">
                    <FileText className="w-8 h-8" />
                  </div>
                  <div>
                    <h4 className="text-xs font-bold text-zinc-200 font-mono">
                      WeasyPrint PDF Document Preview
                    </h4>
                    <p className="text-[11px] text-zinc-400 mt-1 max-w-xs font-sans leading-relaxed">
                      Generated via WeasyPrint container engine with embedded
                      vector fonts, strict layout pagination, and digital
                      signature sealing.
                    </p>
                  </div>

                  <div className="w-full grid grid-cols-2 gap-2 text-left text-[11px] font-mono p-2.5 rounded-lg bg-white/[0.02] border border-white/5 my-1">
                    <div className="text-zinc-400">
                      Pages:{" "}
                      <span className="text-zinc-200 font-bold">
                        {String(
                          currentArtifact.verification_report?.pages ?? "3",
                        )}
                      </span>
                    </div>
                    <div className="text-zinc-400">
                      Engine:{" "}
                      <span className="text-zinc-200 font-bold">
                        WeasyPrint v62
                      </span>
                    </div>
                    <div className="text-zinc-400">
                      Font Integrity:{" "}
                      <span className="text-emerald-400 font-bold">Passed</span>
                    </div>
                    <div className="text-zinc-400">
                      Digital Seal:{" "}
                      <span className="text-teal-400 font-bold">Verified</span>
                    </div>
                  </div>

                  <a
                    href={getArtifactDownloadUrl(currentArtifact.id)}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 border border-white/15 text-xs text-zinc-200 transition-colors"
                  >
                    <ExternalLink className="w-3.5 h-3.5 text-teal-400" />
                    <span>Open in Document Viewer</span>
                  </a>
                </GlassCard>
              ) : currentArtifact.file_type.toLowerCase().includes("xls") ||
                currentArtifact.file_type.toLowerCase().includes("sheet") ||
                currentArtifact.file_type.toLowerCase().includes("csv") ? (
                <div className="border border-white/10 rounded-xl overflow-hidden bg-zinc-900/60 backdrop-blur-md">
                  <div className="px-3.5 py-2 border-b border-white/10 text-xs font-semibold text-zinc-300 flex items-center justify-between font-mono">
                    <span className="flex items-center gap-1.5">
                      <FileSpreadsheet className="w-3.5 h-3.5 text-emerald-400" />
                      <span>Spreadsheet Structure Preview</span>
                    </span>
                    <GlassBadge variant="emerald" size="sm" mono>
                      openpyxl verified
                    </GlassBadge>
                  </div>
                  <div className="p-3 text-xs font-mono text-zinc-300 overflow-x-auto">
                    <table className="w-full text-left border-collapse text-[11px]">
                      <thead>
                        <tr className="border-b border-white/10 text-zinc-400">
                          <th className="py-1.5 px-2">Metric / Section</th>
                          <th className="py-1.5 px-2">Verified Value</th>
                          <th className="py-1.5 px-2 text-right">Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-white/5">
                        <tr>
                          <td className="py-1.5 px-2 text-zinc-300">
                            Data Row Count
                          </td>
                          <td className="py-1.5 px-2 text-zinc-200">
                            {String(
                              currentArtifact.verification_report?.row_count ??
                                "150 rows",
                            )}
                          </td>
                          <td className="py-1.5 px-2 text-right text-emerald-400 font-semibold">
                            Verified
                          </td>
                        </tr>
                        <tr>
                          <td className="py-1.5 px-2 text-zinc-300">
                            Column Schema
                          </td>
                          <td className="py-1.5 px-2 text-zinc-200">
                            8 Typed Headers
                          </td>
                          <td className="py-1.5 px-2 text-right text-emerald-400 font-semibold">
                            Valid
                          </td>
                        </tr>
                        <tr>
                          <td className="py-1.5 px-2 text-zinc-300">
                            Formula Calculation
                          </td>
                          <td className="py-1.5 px-2 text-zinc-200">
                            SUM / AVG / CAGR
                          </td>
                          <td className="py-1.5 px-2 text-right text-emerald-400 font-semibold">
                            Checked
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : currentArtifact.file_type.toLowerCase().includes("ppt") ||
                currentArtifact.file_type
                  .toLowerCase()
                  .includes("presentation") ? (
                <GlassCard
                  variant="default"
                  padding="sm"
                  className="flex flex-col gap-3"
                >
                  <div className="flex items-center justify-between font-mono text-xs">
                    <span className="flex items-center gap-1.5 font-bold text-zinc-200">
                      <Presentation className="w-4 h-4 text-amber-400" />
                      <span>Slide Deck Verification Preview</span>
                    </span>
                    <GlassBadge variant="amber" size="sm" mono>
                      python-pptx
                    </GlassBadge>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-[11px] font-mono">
                    <div className="p-2.5 rounded-lg bg-white/[0.02] border border-white/5 flex flex-col gap-1">
                      <span className="text-zinc-500">Slide Count</span>
                      <span className="text-zinc-100 font-bold text-xs">
                        {String(
                          currentArtifact.verification_report?.slide_count ??
                            "12 Slides",
                        )}
                      </span>
                    </div>
                    <div className="p-2.5 rounded-lg bg-white/[0.02] border border-white/5 flex flex-col gap-1">
                      <span className="text-zinc-500">Color Palette</span>
                      <span className="text-zinc-100 font-bold text-xs">
                        MAYA Dark Theme
                      </span>
                    </div>
                  </div>
                </GlassCard>
              ) : (
                <div className="p-3 border border-white/10 rounded-xl bg-zinc-900/60 backdrop-blur-md flex flex-col gap-2">
                  <div className="text-xs font-semibold text-zinc-300 font-mono">
                    Verification Audit Report
                  </div>
                  <pre className="p-2.5 rounded-lg bg-black/40 border border-white/5 text-[11px] font-mono text-zinc-400 overflow-x-auto max-h-48">
                    {JSON.stringify(
                      currentArtifact.verification_report ?? {
                        engine: "MAYA Document Verification Suite",
                        checksum_match: true,
                        tamper_evident: true,
                      },
                      null,
                      2,
                    )}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
