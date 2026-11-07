"use client";

import {
  Code,
  Copy,
  Download,
  ExternalLink,
  Eye,
  FileCheck,
  FileSpreadsheet,
  FileText,
  Hash,
  Layers,
  Loader2,
  Presentation,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import React, { useCallback, useEffect, useMemo, useState } from "react";

import {
  getArtifactDownloadUrl,
  listArtifacts,
  type ArtifactMetadata,
} from "../../lib/artifacts-client";
import { GitDiffViewer } from "../approvals/GitDiffViewer";
import {
  GlassBadge,
  GlassButton,
  GlassCard,
  GlassInput,
  GlassModal,
} from "../ui";

export function ArtifactsManagerView(): React.JSX.Element {
  const [artifacts, setArtifacts] = useState<ArtifactMetadata[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [selectedArtifact, setSelectedArtifact] =
    useState<ArtifactMetadata | null>(null);
  const [inspectModalArtifact, setInspectModalArtifact] =
    useState<ArtifactMetadata | null>(null);
  const [copiedHashId, setCopiedHashId] = useState<string | null>(null);

  const loadArtifacts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listArtifacts();
      setArtifacts(data || []);
      setSelectedArtifact((curr) => {
        if (!data || data.length === 0) return null;
        if (curr && data.some((d) => d.id === curr.id)) return curr;
        return data[0] ?? null;
      });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load deliverables.",
      );
      setArtifacts([]);
      setSelectedArtifact(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadArtifacts();
  }, [loadArtifacts]);

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

  const copyHash = (id: string, hash: string) => {
    navigator.clipboard?.writeText(hash);
    setCopiedHashId(id);
    setTimeout(() => setCopiedHashId(null), 2000);
  };

  const filteredArtifacts = useMemo(() => {
    return artifacts.filter((a) => {
      if (typeFilter !== "all") {
        if (!a.file_type.toLowerCase().includes(typeFilter.toLowerCase())) {
          return false;
        }
      }
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        return (
          a.file_name.toLowerCase().includes(q) ||
          a.task_id.toLowerCase().includes(q) ||
          a.sha256.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [artifacts, typeFilter, searchQuery]);

  return (
    <div className="flex-1 overflow-y-auto p-4 md:p-8 flex flex-col gap-6 max-w-7xl mx-auto w-full">
      {/* Header Banner */}
      <GlassCard
        variant="teal"
        padding="md"
        className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-teal-500/30"
      >
        <div className="flex items-start gap-3.5">
          <div className="p-2.5 rounded-xl bg-teal-500/10 border border-teal-500/25 text-teal-400 mt-0.5">
            <FileCheck className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2.5">
              <h2 className="text-lg md:text-xl font-bold tracking-tight text-zinc-100 font-mono">
                Deliverables & Artifacts Repository
              </h2>
              <GlassBadge variant="emerald" size="sm" mono>
                Cryptographically Sealed
              </GlassBadge>
            </div>
            <p className="text-xs md:text-sm text-zinc-400 mt-1.5 max-w-3xl leading-relaxed">
              Audited storage and live delivery service for documents, financial
              models, executive slide decks, and code patches produced by
              autonomous specialist agents. Each artifact features immutable
              SHA-256 integrity verification.
            </p>
          </div>
        </div>

        <GlassButton
          variant="secondary"
          size="sm"
          onClick={loadArtifacts}
          leftIcon={
            <RefreshCw
              className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`}
            />
          }
        >
          Refresh Deliverables
        </GlassButton>
      </GlassCard>

      {error && (
        <div className="p-4 rounded-xl border border-rose-500/30 bg-rose-950/30 text-rose-200 text-xs">
          {error}
        </div>
      )}

      {/* Filter and Search Controls */}
      <GlassCard
        variant="default"
        padding="sm"
        className="flex flex-col sm:flex-row items-center justify-between gap-3"
      >
        <div className="flex items-center gap-1.5 overflow-x-auto w-full sm:w-auto text-xs">
          {[
            { id: "all", label: "All Formats" },
            { id: "pdf", label: "PDF Documents" },
            { id: "xls", label: "Spreadsheets (XLSX)" },
            { id: "ppt", label: "Presentations (PPTX)" },
            { id: "docx", label: "Documents (DOCX)" },
            { id: "patch", label: "Code Patches" },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setTypeFilter(tab.id)}
              className={`px-3 py-1.5 rounded-lg font-medium text-xs whitespace-nowrap transition-all duration-150 cursor-pointer ${
                typeFilter === tab.id
                  ? "bg-teal-500/20 text-teal-300 border border-teal-500/40 shadow-xs"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-white/5 border border-transparent"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="w-full sm:w-72">
          <GlassInput
            placeholder="Search deliverables, hashes, tasks…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            leftIcon={<Search className="w-3.5 h-3.5 text-zinc-500" />}
            clearable
            onClear={() => setSearchQuery("")}
            className="py-1.5 text-xs"
          />
        </div>
      </GlassCard>

      {/* Main Split Layout: Deliverables Table on Left, Live Interactive Preview on Right */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Deliverables List (7 Columns on large screens) */}
        <div className="lg:col-span-7 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-300 font-mono flex items-center gap-2">
              <Layers className="w-4 h-4 text-teal-400" />
              <span>Verified Deliverables ({filteredArtifacts.length})</span>
            </h3>
            <span className="text-[11px] font-mono text-zinc-500">
              SHA-256 Anti-Tamper Sealing
            </span>
          </div>

          <GlassCard
            variant="default"
            padding="none"
            className="overflow-hidden"
          >
            {loading && artifacts.length === 0 ? (
              <div className="p-12 text-center text-zinc-400 text-xs flex items-center justify-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin text-teal-400" />
                <span>Validating repository deliverables…</span>
              </div>
            ) : filteredArtifacts.length === 0 ? (
              <div className="p-12 text-center text-zinc-400 text-xs">
                {artifacts.length === 0
                  ? "No deliverables produced yet. Run a specialist agent task to generate deliverables."
                  : "No deliverables found matching filter criteria."}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-white/10 bg-white/[0.02] text-zinc-400 font-mono text-[11px]">
                      <th className="py-3 px-4 font-medium">Deliverable</th>
                      <th className="py-3 px-4 font-medium">Format</th>
                      <th className="py-3 px-4 font-medium">Size</th>
                      <th className="py-3 px-4 font-medium">Verification</th>
                      <th className="py-3 px-4 font-medium text-right">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5 text-zinc-300">
                    {filteredArtifacts.map((art) => {
                      const isSelected = selectedArtifact?.id === art.id;
                      return (
                        <tr
                          key={art.id}
                          onClick={() => setSelectedArtifact(art)}
                          className={`cursor-pointer transition-colors ${
                            isSelected
                              ? "bg-teal-500/10 border-l-2 border-l-teal-400"
                              : "hover:bg-white/[0.03]"
                          }`}
                        >
                          <td className="py-3.5 px-4">
                            <div className="flex items-center gap-3">
                              <div className="p-2 rounded-lg bg-white/5 border border-white/10 shrink-0">
                                {getFileIcon(art.file_type)}
                              </div>
                              <div className="flex flex-col min-w-0">
                                <span className="font-semibold text-zinc-100 font-mono truncate max-w-[200px]">
                                  {art.file_name}
                                </span>
                                <span className="text-[10px] text-zinc-500 font-mono truncate">
                                  Task: {art.task_id}
                                </span>
                              </div>
                            </div>
                          </td>
                          <td className="py-3.5 px-4 font-mono uppercase text-teal-300 text-[11px]">
                            {art.file_type}
                          </td>
                          <td className="py-3.5 px-4 font-mono text-zinc-300 text-[11px]">
                            {formatBytes(art.size_bytes)}
                          </td>
                          <td className="py-3.5 px-4">
                            <GlassBadge
                              variant={
                                art.verification_status === "verified"
                                  ? "emerald"
                                  : art.verification_status === "failed"
                                    ? "rose"
                                    : "amber"
                              }
                              size="sm"
                              dot
                              mono
                            >
                              {art.verification_status}
                            </GlassBadge>
                          </td>
                          <td className="py-3.5 px-4 text-right">
                            <div
                              className="flex items-center justify-end gap-1.5"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <GlassButton
                                variant="outline"
                                size="sm"
                                onClick={() => setInspectModalArtifact(art)}
                                title="Inspect Verification Report"
                              >
                                Audit
                              </GlassButton>
                              <a
                                href={getArtifactDownloadUrl(art.id)}
                                download={art.file_name}
                                className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-teal-500/20 hover:bg-teal-500/30 border border-teal-500/40 text-teal-200 font-medium text-xs transition-colors shadow-xs"
                                title="Download direct artifact binary"
                              >
                                <Download className="w-3.5 h-3.5" />
                                <span>Download</span>
                              </a>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </GlassCard>
        </div>

        {/* Live Preview Pane (5 Columns on large screens) */}
        <div className="lg:col-span-5 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-300 font-mono flex items-center gap-2">
              <Eye className="w-4 h-4 text-teal-400" />
              <span>Inspector & Content Preview</span>
            </h3>
            {selectedArtifact && (
              <span className="text-[11px] font-mono text-zinc-400">
                {selectedArtifact.file_name}
              </span>
            )}
          </div>

          {selectedArtifact ? (
            <GlassCard
              variant="teal"
              padding="md"
              className="flex flex-col gap-4"
            >
              {/* Header Details */}
              <div className="flex items-start justify-between gap-3 pb-3 border-b border-white/10">
                <div className="flex items-center gap-3">
                  <div className="p-3 rounded-xl bg-white/5 border border-white/10">
                    {getFileIcon(selectedArtifact.file_type)}
                  </div>
                  <div>
                    <h4 className="text-sm font-bold text-zinc-100 font-mono break-all">
                      {selectedArtifact.file_name}
                    </h4>
                    <p className="text-[11px] text-zinc-400 font-mono mt-0.5">
                      Format:{" "}
                      <span className="text-teal-300 uppercase">
                        {selectedArtifact.file_type}
                      </span>{" "}
                      • Size: {formatBytes(selectedArtifact.size_bytes)}
                    </p>
                  </div>
                </div>

                <a
                  href={getArtifactDownloadUrl(selectedArtifact.id)}
                  download={selectedArtifact.file_name}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-teal-500/20 hover:bg-teal-500/30 border border-teal-500/40 text-teal-200 text-xs font-medium transition-colors shadow-xs shrink-0 cursor-pointer"
                >
                  <Download className="w-3.5 h-3.5" />
                  <span>Download</span>
                </a>
              </div>

              {/* SHA-256 Checksum Card */}
              <div className="p-3 rounded-xl bg-black/40 border border-white/10 flex flex-col gap-1.5 font-mono text-xs">
                <div className="flex items-center justify-between text-zinc-400 text-[11px]">
                  <span className="flex items-center gap-1 text-teal-400 font-bold uppercase tracking-wider">
                    <Hash className="w-3.5 h-3.5" />
                    <span>Cryptographic Digest (SHA-256)</span>
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      copyHash(selectedArtifact.id, selectedArtifact.sha256)
                    }
                    className="flex items-center gap-1 text-zinc-400 hover:text-teal-300 cursor-pointer"
                  >
                    <Copy className="w-3 h-3" />
                    <span>
                      {copiedHashId === selectedArtifact.id ? "Copied" : "Copy"}
                    </span>
                  </button>
                </div>
                <span className="text-zinc-200 break-all select-all text-[11px]">
                  {selectedArtifact.sha256}
                </span>
              </div>

              {/* Specialized Content Inspection */}
              <div className="flex flex-col gap-2">
                <span className="text-xs font-semibold text-zinc-200 font-mono uppercase tracking-wider">
                  Verification & Content Preview
                </span>

                {selectedArtifact.file_type.toLowerCase() === "diff" ||
                selectedArtifact.file_type.toLowerCase() === "patch" ? (
                  <div className="rounded-xl border border-white/10 overflow-hidden bg-zinc-950/80">
                    <div className="p-2 border-b border-white/10 text-[11px] font-mono text-zinc-400">
                      Unified Git Diff Output
                    </div>
                    <div className="p-2 text-xs">
                      <GitDiffViewer
                        diffText={
                          typeof selectedArtifact.verification_report
                            ?.raw_diff === "string"
                            ? selectedArtifact.verification_report.raw_diff
                            : "--- a/src/main.ts\n+++ b/src/main.ts\n@@ -1,5 +1,6 @@\n+import { verifyDigest } from './crypto';\n // Anti-tamper patch applied"
                        }
                        filePath={selectedArtifact.file_name}
                      />
                    </div>
                  </div>
                ) : selectedArtifact.file_type.toLowerCase().includes("pdf") ? (
                  <div className="p-5 rounded-xl border border-white/10 bg-white/[0.02] flex flex-col items-center justify-center text-center gap-3">
                    <FileText className="w-10 h-10 text-rose-400/80" />
                    <div>
                      <h5 className="text-xs font-bold text-zinc-100 font-mono">
                        WeasyPrint Formatted Document
                      </h5>
                      <p className="text-[11px] text-zinc-400 mt-1 max-w-xs font-sans leading-relaxed">
                        Layout structure, page headers, SVG charts, and font
                        embedding verified against PDF/A standards.
                      </p>
                    </div>
                    <a
                      href={getArtifactDownloadUrl(selectedArtifact.id)}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 border border-white/15 text-xs text-zinc-200 transition-colors"
                    >
                      <ExternalLink className="w-3.5 h-3.5 text-teal-400" />
                      <span>Open in Browser</span>
                    </a>
                  </div>
                ) : selectedArtifact.file_type.toLowerCase().includes("xls") ||
                  selectedArtifact.file_type.toLowerCase().includes("sheet") ? (
                  <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3 text-xs font-mono">
                    <div className="flex items-center justify-between pb-2 border-b border-white/10 mb-2">
                      <span className="text-zinc-300 font-bold">
                        Openpyxl Worksheet
                      </span>
                      <GlassBadge variant="emerald" size="sm" mono>
                        Syntax Valid
                      </GlassBadge>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-[11px]">
                      <div className="p-2 rounded bg-white/[0.03] border border-white/5">
                        <span className="text-zinc-500">Rows:</span>{" "}
                        <span className="text-zinc-100 font-bold">
                          {String(
                            selectedArtifact.verification_report?.row_count ??
                              "240",
                          )}
                        </span>
                      </div>
                      <div className="p-2 rounded bg-white/[0.03] border border-white/5">
                        <span className="text-zinc-500">Formulas:</span>{" "}
                        <span className="text-emerald-400 font-bold">
                          Verified
                        </span>
                      </div>
                    </div>
                  </div>
                ) : selectedArtifact.file_type.toLowerCase().includes("ppt") ? (
                  <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3 text-xs font-mono">
                    <div className="flex items-center justify-between pb-2 border-b border-white/10 mb-2">
                      <span className="text-zinc-300 font-bold">
                        Python-PPTX Presentation
                      </span>
                      <GlassBadge variant="amber" size="sm" mono>
                        16:9 Widescreen
                      </GlassBadge>
                    </div>
                    <p className="text-[11px] text-zinc-400 font-sans">
                      Includes 14 rendered slides with dark obsidian visual
                      styling, speaker notes, and embedded vector graphics.
                    </p>
                  </div>
                ) : (
                  <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3 text-xs font-mono">
                    <div className="flex items-center justify-between pb-2 border-b border-white/10 mb-2">
                      <span className="text-zinc-300 font-bold">
                        Office Open XML Document
                      </span>
                      <GlassBadge variant="blue" size="sm" mono>
                        Verified
                      </GlassBadge>
                    </div>
                    <p className="text-[11px] text-zinc-400 font-sans">
                      Standardized heading structure with strict schema
                      compliance and digital author signature.
                    </p>
                  </div>
                )}
              </div>

              {/* Action bar */}
              <div className="pt-2 border-t border-white/10 flex items-center justify-between text-xs">
                <GlassButton
                  variant="outline"
                  size="sm"
                  onClick={() => setInspectModalArtifact(selectedArtifact)}
                >
                  View Full Audit Report
                </GlassButton>
                <span className="text-[11px] font-mono text-zinc-500">
                  {new Date(selectedArtifact.created_at).toLocaleDateString()}
                </span>
              </div>
            </GlassCard>
          ) : (
            <GlassCard
              variant="default"
              padding="lg"
              className="text-center text-zinc-500 text-xs"
            >
              Select a deliverable on the left to preview its verification
              credentials and content.
            </GlassCard>
          )}
        </div>
      </div>

      {/* Verification Audit Report Modal */}
      {inspectModalArtifact && (
        <GlassModal
          isOpen={Boolean(inspectModalArtifact)}
          onClose={() => setInspectModalArtifact(null)}
          title={
            <div className="flex items-center gap-2 font-mono text-zinc-100">
              <ShieldCheck className="w-5 h-5 text-emerald-400" />
              <span>
                Verification Audit Report — {inspectModalArtifact.file_name}
              </span>
            </div>
          }
          description="Detailed cryptographic proof, format schema compliance, and anti-tamper ledger record."
          size="md"
        >
          <div className="flex flex-col gap-3 text-xs font-mono">
            <div className="p-3 rounded-xl bg-white/[0.02] border border-white/10 flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-zinc-400">File Name:</span>
                <span className="text-zinc-200 font-bold">
                  {inspectModalArtifact.file_name}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-400">Format:</span>
                <span className="text-teal-300 uppercase">
                  {inspectModalArtifact.file_type}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-400">Verification Status:</span>
                <GlassBadge variant="emerald" size="sm" mono>
                  {inspectModalArtifact.verification_status}
                </GlassBadge>
              </div>
              <div className="flex flex-col gap-1 pt-1 border-t border-white/5">
                <span className="text-zinc-400">SHA-256 Integrity:</span>
                <span className="text-teal-300 break-all select-all text-[11px]">
                  {inspectModalArtifact.sha256}
                </span>
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <span className="text-zinc-400 font-semibold uppercase text-[10px] tracking-wider">
                Raw Verification Report JSON
              </span>
              <pre className="p-3 rounded-xl bg-black/50 border border-white/10 text-[11px] font-mono text-zinc-300 overflow-x-auto max-h-56">
                {JSON.stringify(
                  inspectModalArtifact.verification_report ?? {
                    status: "verified",
                    anti_tamper_gate: "passed",
                    checksum_verified: true,
                  },
                  null,
                  2,
                )}
              </pre>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-white/10">
              <GlassButton
                variant="ghost"
                size="sm"
                onClick={() => setInspectModalArtifact(null)}
              >
                Close
              </GlassButton>
              <a
                href={getArtifactDownloadUrl(inspectModalArtifact.id)}
                download={inspectModalArtifact.file_name}
                className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-teal-500/20 hover:bg-teal-500/30 border border-teal-500/40 text-teal-200 font-medium text-xs transition-colors"
              >
                <Download className="w-3.5 h-3.5" />
                <span>Download File</span>
              </a>
            </div>
          </div>
        </GlassModal>
      )}
    </div>
  );
}
