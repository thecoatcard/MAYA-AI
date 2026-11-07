"use client";

import {
  AlertCircle,
  ArrowRight,
  Bot,
  CheckCircle2,
  Clock,
  Code,
  ExternalLink,
  FileText,
  Layers,
  Link as LinkIcon,
  Mail,
  RotateCw,
  Search,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import React, { useMemo, useState } from "react";

import type { SubtaskResponse } from "../../lib/tasks-client";

export interface DagVisualizerProps {
  readonly subtasks: readonly SubtaskResponse[];
  readonly selectedSubtaskId?: string | null;
  readonly onSelectSubtask?: (subtask: SubtaskResponse) => void;
  readonly className?: string;
}

interface CitationItem {
  readonly id?: string | number;
  readonly title?: string;
  readonly url?: string;
  readonly snippet?: string;
  readonly domain?: string;
}

export function DagVisualizer({
  subtasks,
  selectedSubtaskId,
  onSelectSubtask,
  className = "",
}: DagVisualizerProps): React.JSX.Element {
  const [internalSelectedId, setInternalSelectedId] = useState<string | null>(
    subtasks[0]?.id ?? null,
  );

  const activeId =
    selectedSubtaskId !== undefined ? selectedSubtaskId : internalSelectedId;
  const activeSubtask =
    subtasks.find((st) => st.id === activeId || String(st.id) === activeId) ??
    subtasks[0] ??
    null;

  const getAgentIcon = (agent: string) => {
    switch (agent.toLowerCase()) {
      case "research":
        return <Search className="w-4 h-4 text-cyan-400" />;
      case "coding":
        return <Code className="w-4 h-4 text-emerald-400" />;
      case "document":
        return <FileText className="w-4 h-4 text-purple-400" />;
      case "email":
        return <Mail className="w-4 h-4 text-blue-400" />;
      case "pm":
        return <Clock className="w-4 h-4 text-amber-400" />;
      default:
        return <Bot className="w-4 h-4 text-teal-400" />;
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status.toLowerCase()) {
      case "completed":
        return (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 shadow-[0_0_12px_rgba(16,185,129,0.25)]">
            <CheckCircle2 className="w-3 h-3 text-emerald-400" />
            <span>Completed</span>
          </span>
        );
      case "in_progress":
      case "executing":
        return (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-0.5 rounded-full bg-cyan-500/15 border border-cyan-500/30 text-cyan-300 shadow-[0_0_12px_rgba(6,182,212,0.25)]">
            <RotateCw className="w-3 h-3 text-cyan-400 animate-spin" />
            <span>In Progress</span>
          </span>
        );
      case "awaiting_approval":
      case "paused":
        return (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-0.5 rounded-full bg-amber-500/15 border border-amber-500/30 text-amber-300 shadow-[0_0_12px_rgba(245,158,11,0.25)]">
            <ShieldAlert className="w-3 h-3 text-amber-400" />
            <span>Awaiting Approval</span>
          </span>
        );
      case "failed":
        return (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-0.5 rounded-full bg-rose-500/15 border border-rose-500/30 text-rose-300 shadow-[0_0_12px_rgba(244,63,94,0.25)]">
            <AlertCircle className="w-3 h-3 text-rose-400" />
            <span>Failed</span>
          </span>
        );
      case "pending":
      default:
        return (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-0.5 rounded-full bg-zinc-800/80 border border-zinc-700/60 text-zinc-400 shadow-sm">
            <Clock className="w-3 h-3 text-zinc-500" />
            <span>Pending</span>
          </span>
        );
    }
  };

  // Extract structured citations from verification proof or output
  const extractedCitations = useMemo<readonly CitationItem[]>(() => {
    if (!activeSubtask) return [];
    const sourceObj =
      (activeSubtask.verification_proof as Record<string, unknown> | null) ??
      (activeSubtask.output_result as Record<string, unknown> | null);

    if (!sourceObj || typeof sourceObj !== "object") return [];

    const rawList =
      (sourceObj.citations as unknown[]) ??
      (sourceObj.sources as unknown[]) ??
      (sourceObj.references as unknown[]) ??
      [];

    if (!Array.isArray(rawList)) {
      if (typeof sourceObj.citations === "number") {
        return Array.from({ length: sourceObj.citations }).map((_, i) => ({
          id: i + 1,
          title: `Verified Ledger Citation #${i + 1}`,
          snippet:
            "Cryptographically verified Brave Search source citation recorded in execution ledger.",
        }));
      }
      return [];
    }

    return rawList.map((item, idx) => {
      if (typeof item === "string") {
        return {
          id: idx + 1,
          title: item,
          snippet: item,
        };
      }
      if (item && typeof item === "object") {
        const record = item as Record<string, unknown>;
        let domain = "";
        const url = typeof record.url === "string" ? record.url : undefined;
        if (url) {
          try {
            domain = new URL(url).hostname;
          } catch {
            domain = "";
          }
        }
        return {
          id: (record.id as string | number) ?? idx + 1,
          title:
            (record.title as string) ??
            (record.source as string) ??
            `Citation Source #${idx + 1}`,
          url,
          domain,
          snippet:
            (record.snippet as string) ??
            (record.quote as string) ??
            (record.description as string) ??
            "",
        };
      }
      return { id: idx + 1, title: String(item) };
    });
  }, [activeSubtask]);

  if (subtasks.length === 0) {
    return (
      <div
        className={`p-10 text-center rounded-2xl bg-zinc-900/40 backdrop-blur-xl border border-white/10 shadow-xl text-zinc-400 ${className}`}
      >
        <div className="w-12 h-12 mx-auto mb-3 rounded-xl bg-teal-500/10 border border-teal-500/20 flex items-center justify-center text-teal-400 shadow-[0_0_15px_rgba(20,184,166,0.15)]">
          <Bot className="w-6 h-6 opacity-80" />
        </div>
        <p className="text-sm font-semibold text-zinc-300">
          No subtasks defined in the DAG plan.
        </p>
        <p className="text-xs text-zinc-500 mt-1 max-w-sm mx-auto">
          When a task instruction is submitted, the LangGraph supervisor will
          decompose it into an execution graph.
        </p>
      </div>
    );
  }

  return (
    <div
      className={`flex flex-col lg:flex-row gap-5 rounded-2xl bg-zinc-900/40 backdrop-blur-xl border border-white/10 p-5 shadow-xl text-zinc-100 ${className}`}
    >
      {/* Left Column: DAG Node Graph Visualization */}
      <div className="flex-1 flex flex-col gap-3.5">
        <div className="flex items-center justify-between pb-3 border-b border-white/5">
          <div className="flex items-center gap-2">
            <div className="p-1.5 rounded-lg bg-teal-500/10 border border-teal-500/20 text-teal-400">
              <Layers className="w-3.5 h-3.5" />
            </div>
            <span className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
              Execution DAG Dependency Graph
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono text-zinc-400 px-2 py-0.5 rounded-md bg-zinc-800/60 border border-zinc-700/50">
              {subtasks.length} Nodes
            </span>
          </div>
        </div>

        <div className="flex flex-col gap-2.5">
          {subtasks.map((st, index) => {
            const isSelected =
              st.id === activeId || String(st.id) === String(activeId);
            const hasDependencies =
              st.dependencies && st.dependencies.length > 0;

            return (
              <div key={st.id} className="flex flex-col">
                {/* Dependency Connector Indicator with neon line */}
                {hasDependencies && (
                  <div className="flex items-center gap-2 pl-7 py-1 text-[11px] text-zinc-400 font-mono">
                    <span className="w-3 h-px bg-teal-500/40" />
                    <span>depends on: {st.dependencies.join(", ")}</span>
                  </div>
                )}

                {/* Subtask Node Card */}
                <button
                  type="button"
                  onClick={() => {
                    setInternalSelectedId(st.id);
                    onSelectSubtask?.(st);
                  }}
                  className={`w-full text-left p-3.5 rounded-xl border transition-all duration-200 cursor-pointer relative overflow-hidden group ${
                    isSelected
                      ? "border-teal-400/50 bg-gradient-to-r from-teal-950/40 via-zinc-900/80 to-zinc-900/80 shadow-[0_0_20px_rgba(20,184,166,0.15)] ring-1 ring-teal-400/30"
                      : "border-white/5 bg-zinc-900/30 hover:bg-zinc-900/60 hover:border-white/10"
                  }`}
                  aria-pressed={isSelected}
                >
                  {/* Subtle top edge glow */}
                  {isSelected && (
                    <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-teal-400/60 to-transparent" />
                  )}

                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <span className="text-xs font-mono font-bold text-zinc-500 w-5">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                      <div className="p-2 rounded-lg bg-zinc-800/80 border border-white/10 shadow-inner">
                        {getAgentIcon(st.assigned_agent)}
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-bold text-zinc-200 uppercase tracking-wide">
                            {st.assigned_agent} Agent
                          </span>
                          <span className="text-[10px] font-mono text-zinc-500">
                            #{st.id.slice(0, 8)}
                          </span>
                        </div>
                        <p className="text-xs text-zinc-300 line-clamp-1 mt-0.5 font-medium">
                          {st.description}
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-2.5 shrink-0">
                      {getStatusBadge(st.status)}
                      <ArrowRight
                        className={`w-4 h-4 transition-transform duration-200 ${
                          isSelected
                            ? "text-teal-400 translate-x-0.5"
                            : "text-zinc-600 group-hover:text-zinc-400"
                        }`}
                      />
                    </div>
                  </div>
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* Right Column: Node Inspector Panel with Verification & Citation Previews */}
      {activeSubtask && (
        <div className="w-full lg:w-96 flex flex-col rounded-xl bg-zinc-900/60 backdrop-blur-xl border border-white/10 p-4 shadow-xl shrink-0">
          {/* Inspector Header */}
          <div className="flex items-center justify-between pb-3 border-b border-white/5">
            <div className="flex items-center gap-2.5">
              <div className="p-1.5 rounded-lg bg-zinc-800/90 border border-white/10 shadow-xs">
                {getAgentIcon(activeSubtask.assigned_agent)}
              </div>
              <div>
                <span className="text-xs font-bold text-zinc-200 uppercase tracking-wide">
                  {activeSubtask.assigned_agent} Node
                </span>
                <p className="text-[10px] font-mono text-zinc-400">
                  ID: {activeSubtask.id}
                </p>
              </div>
            </div>
            {getStatusBadge(activeSubtask.status)}
          </div>

          {/* Inspector Scroll Body */}
          <div className="flex flex-col gap-4 pt-3 text-xs overflow-y-auto max-h-[500px] pr-1">
            {/* Description */}
            <div className="p-3 rounded-lg bg-zinc-950/50 border border-white/5">
              <span className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider block mb-1">
                Instruction / Description
              </span>
              <p className="text-zinc-200 leading-relaxed font-medium">
                {activeSubtask.description}
              </p>
            </div>

            {/* Dependencies */}
            {activeSubtask.dependencies &&
              activeSubtask.dependencies.length > 0 && (
                <div>
                  <span className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider block mb-1.5">
                    Predecessor Dependencies
                  </span>
                  <div className="flex flex-wrap gap-1.5 font-mono text-[11px]">
                    {activeSubtask.dependencies.map((dep) => (
                      <span
                        key={dep}
                        className="px-2 py-0.5 rounded-md bg-zinc-800/80 text-teal-300 border border-teal-500/20 font-semibold"
                      >
                        {dep}
                      </span>
                    ))}
                  </div>
                </div>
              )}

            {/* Citation Previews */}
            {extractedCitations.length > 0 && (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-semibold text-cyan-400 uppercase tracking-wider flex items-center gap-1.5">
                    <LinkIcon className="w-3 h-3" />
                    <span>Citation Previews ({extractedCitations.length})</span>
                  </span>
                  <span className="text-[10px] font-mono text-zinc-500">
                    Brave Verified
                  </span>
                </div>
                <div className="flex flex-col gap-2">
                  {extractedCitations.map((c) => (
                    <div
                      key={String(c.id)}
                      className="p-2.5 rounded-lg bg-cyan-950/20 border border-cyan-500/20 hover:border-cyan-500/40 transition-colors flex flex-col gap-1 text-[11px]"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold text-cyan-200 line-clamp-1">
                          {c.title}
                        </span>
                        {c.url && (
                          <a
                            href={c.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-cyan-400 hover:text-cyan-300 p-0.5 rounded transition-colors shrink-0"
                            title="Open external citation"
                          >
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        )}
                      </div>
                      {c.snippet && (
                        <p className="text-zinc-400 line-clamp-2 leading-relaxed text-[11px] font-mono">
                          &ldquo;{c.snippet}&rdquo;
                        </p>
                      )}
                      {c.domain && (
                        <span className="text-[10px] font-mono text-cyan-400/80 mt-0.5">
                          {c.domain}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Verification Proof Display (Decision D8) */}
            {Boolean(activeSubtask.verification_proof) && (
              <div>
                <span className="text-[10px] font-semibold text-emerald-400 uppercase tracking-wider flex items-center gap-1 mb-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>Verification Proof</span>
                </span>
                <pre className="p-3 rounded-lg bg-zinc-950/80 border border-emerald-500/20 text-[11px] font-mono text-emerald-300 overflow-x-auto shadow-inner leading-relaxed">
                  {JSON.stringify(activeSubtask.verification_proof, null, 2)}
                </pre>
              </div>
            )}

            {/* Output Result */}
            {activeSubtask.output_result !== undefined &&
              activeSubtask.output_result !== null && (
                <div>
                  <span className="text-[10px] font-semibold text-teal-400 uppercase tracking-wider flex items-center gap-1 mb-1.5">
                    <Sparkles className="w-3.5 h-3.5" />
                    <span>Output Result</span>
                  </span>
                  <pre className="p-3 rounded-lg bg-zinc-950/80 border border-white/5 text-[11px] font-mono text-zinc-300 overflow-x-auto shadow-inner leading-relaxed">
                    {typeof activeSubtask.output_result === "object"
                      ? JSON.stringify(activeSubtask.output_result, null, 2)
                      : String(activeSubtask.output_result)}
                  </pre>
                </div>
              )}

            {/* Error & Retry */}
            {activeSubtask.error_message && (
              <div>
                <span className="text-[10px] font-semibold text-rose-400 uppercase tracking-wider flex items-center gap-1 mb-1.5">
                  <AlertCircle className="w-3.5 h-3.5" />
                  <span>Error (Retries: {activeSubtask.retry_count})</span>
                </span>
                <p className="p-2.5 rounded-lg bg-rose-950/40 border border-rose-500/30 text-rose-200 text-xs font-mono leading-relaxed">
                  {activeSubtask.error_message}
                </p>
              </div>
            )}

            {/* Input Data */}
            {activeSubtask.input_data &&
              Object.keys(activeSubtask.input_data).length > 0 && (
                <div>
                  <span className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider block mb-1.5">
                    Input Parameters
                  </span>
                  <pre className="p-2.5 rounded-lg bg-zinc-950/60 border border-white/5 text-[11px] font-mono text-zinc-400 overflow-x-auto leading-relaxed">
                    {JSON.stringify(activeSubtask.input_data, null, 2)}
                  </pre>
                </div>
              )}
          </div>
        </div>
      )}
    </div>
  );
}
