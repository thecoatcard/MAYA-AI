"use client";

import {
  AlertCircle,
  Bot,
  Check,
  Clock,
  Code,
  Copy,
  DollarSign,
  FileText,
  Key,
  Loader2,
  Mail,
  RefreshCw,
  Search,
  Sliders,
  Sparkles,
  X,
} from "lucide-react";
import React, { useCallback, useEffect, useState } from "react";

import {
  submitTask,
  type TaskSubmissionResponse,
} from "../../lib/tasks-client";

export interface NewTaskModalProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly onTaskCreated?: (task: TaskSubmissionResponse) => void;
  readonly sessionId?: string;
}

interface DelegationPreset {
  readonly id: "research" | "coding" | "document" | "email" | "pm";
  readonly label: string;
  readonly role: string;
  readonly defaultTitle: string;
  readonly defaultInstruction: string;
  readonly recommendedBudget: number;
  readonly icon: React.ReactNode;
  readonly colorClass: string;
  readonly borderClass: string;
  readonly glowClass: string;
}

const DELEGATION_PRESETS: readonly DelegationPreset[] = [
  {
    id: "research",
    label: "Research",
    role: "Deep Investigation & Citations",
    defaultTitle: "Competitive Market & Technical Research",
    defaultInstruction:
      "Investigate the latest architectural benchmarks and market alternatives for [Topic]. Query Brave Web Search, extract primary sources, and return structured citations with verbatim quote snippets and source URLs.",
    recommendedBudget: 1.5,
    icon: <Search className="w-3.5 h-3.5 text-cyan-400" />,
    colorClass: "text-cyan-300 bg-cyan-950/40",
    borderClass: "border-cyan-500/30",
    glowClass:
      "hover:border-cyan-400/60 hover:shadow-[0_0_15px_rgba(6,182,212,0.2)]",
  },
  {
    id: "coding",
    label: "Coding",
    role: "Autonomous Bug Fix & Engineering",
    defaultTitle: "Sandbox Test Reproduction & PR Development",
    defaultInstruction:
      "Reproduce failing test in Docker sandbox environment, develop code patch, verify exit code 0 under non-root UID 10001, commit locally, and prepare GitHub PR under approval token ceiling.",
    recommendedBudget: 4.0,
    icon: <Code className="w-3.5 h-3.5 text-emerald-400" />,
    colorClass: "text-emerald-300 bg-emerald-950/40",
    borderClass: "border-emerald-500/30",
    glowClass:
      "hover:border-emerald-400/60 hover:shadow-[0_0_15px_rgba(16,185,129,0.2)]",
  },
  {
    id: "document",
    label: "Document",
    role: "Artifact Generation",
    defaultTitle: "Executive PDF & Spreadsheet Generation",
    defaultInstruction:
      "Synthesize analyzed operational and financial data into an executive PDF report via WeasyPrint and companion OpenPyXL XLSX workbook. Record SHA-256 cryptographic hashes in artifact ledger.",
    recommendedBudget: 2.5,
    icon: <FileText className="w-3.5 h-3.5 text-purple-400" />,
    colorClass: "text-purple-300 bg-purple-950/40",
    borderClass: "border-purple-500/30",
    glowClass:
      "hover:border-purple-400/60 hover:shadow-[0_0_15px_rgba(168,85,247,0.2)]",
  },
  {
    id: "email",
    label: "Email",
    role: "Communications & Triage",
    defaultTitle: "Priority Inbox Triage & Draft Review",
    defaultInstruction:
      "Analyze recent unread email threads for priority action items, compose structured replies, and halt at draft preview state awaiting human cryptographic approval token.",
    recommendedBudget: 1.0,
    icon: <Mail className="w-3.5 h-3.5 text-blue-400" />,
    colorClass: "text-blue-300 bg-blue-950/40",
    borderClass: "border-blue-500/30",
    glowClass:
      "hover:border-blue-400/60 hover:shadow-[0_0_15px_rgba(59,130,246,0.2)]",
  },
  {
    id: "pm",
    label: "PM",
    role: "Daily Executive Briefing",
    defaultTitle: "Daily Morning Briefing & Sprint Sync",
    defaultInstruction:
      "Query Google Calendar for today's agenda and Notion sprint databases for in-flight blocker tickets. Produce concise executive briefing with time allocations and risk alerts.",
    recommendedBudget: 1.0,
    icon: <Clock className="w-3.5 h-3.5 text-amber-400" />,
    colorClass: "text-amber-300 bg-amber-950/40",
    borderClass: "border-amber-500/30",
    glowClass:
      "hover:border-amber-400/60 hover:shadow-[0_0_15px_rgba(245,158,11,0.2)]",
  },
];

export function NewTaskModal({
  isOpen,
  onClose,
  onTaskCreated,
  sessionId,
}: NewTaskModalProps): React.JSX.Element | null {
  const [instruction, setInstruction] = useState<string>("");
  const [title, setTitle] = useState<string>("");
  const [budgetUsd, setBudgetUsd] = useState<number>(2.0);
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState<string>("");
  const [copiedKey, setCopiedKey] = useState<boolean>(false);

  // Generate unique RFC idempotency key
  const generateKey = useCallback(() => {
    const key = `task-${Date.now()}-${Math.random().toString(36).substring(2, 10)}`;
    setIdempotencyKey(key);
  }, []);

  useEffect(() => {
    if (isOpen) {
      generateKey();
      setError(null);
    }
  }, [isOpen, generateKey]);

  if (!isOpen) return null;

  const handleSelectPreset = (preset: DelegationPreset) => {
    setActivePreset(preset.id);
    setTitle(preset.defaultTitle);
    setInstruction(preset.defaultInstruction);
    setBudgetUsd(preset.recommendedBudget);
  };

  const handleCopyKey = () => {
    navigator.clipboard.writeText(idempotencyKey).then(() => {
      setCopiedKey(true);
      setTimeout(() => setCopiedKey(false), 2000);
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!instruction.trim()) {
      setError("Please provide a task instruction.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const budgetNum =
        typeof budgetUsd === "number"
          ? budgetUsd
          : parseFloat(String(budgetUsd));
      const res = await submitTask({
        instruction: instruction.trim(),
        title: title.trim() || undefined,
        idempotency_key: idempotencyKey.trim() || undefined,
        budget_usd: !isNaN(budgetNum) && budgetNum > 0 ? budgetNum : 2.0,
        session_id: sessionId || undefined,
      });

      onTaskCreated?.(res);
      setInstruction("");
      setTitle("");
      setActivePreset(null);
      onClose();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to submit background task",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-md transition-opacity"
      role="dialog"
      aria-modal="true"
      aria-labelledby="new-task-title"
    >
      <div className="w-full max-w-2xl rounded-2xl border border-white/10 bg-zinc-900/90 backdrop-blur-2xl p-6 shadow-2xl text-zinc-100 flex flex-col gap-5 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/5 pb-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl bg-teal-500/15 border border-teal-500/30 text-teal-400 shadow-[0_0_15px_rgba(20,184,166,0.25)]">
              <Sparkles className="w-5 h-5" />
            </div>
            <div>
              <h2
                id="new-task-title"
                className="text-base font-bold text-zinc-100 tracking-tight"
              >
                Assign Autonomous Task
              </h2>
              <p className="text-xs text-zinc-400">
                LangGraph supervisor decomposes instructions into specialized
                agent DAGs
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 transition-colors"
            aria-label="Close modal"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {error && (
          <div className="p-3.5 rounded-xl border border-rose-500/40 bg-rose-950/50 text-rose-200 text-xs flex items-center gap-2.5">
            <AlertCircle className="w-4 h-4 shrink-0 text-rose-400" />
            <span>{error}</span>
          </div>
        )}

        {/* Agent Delegation Presets */}
        <div className="flex flex-col gap-2">
          <span className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
            <Bot className="w-3.5 h-3.5 text-teal-400" />
            <span>Agent Delegation Presets</span>
          </span>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
            {DELEGATION_PRESETS.map((preset) => {
              const isActive = activePreset === preset.id;
              return (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => handleSelectPreset(preset)}
                  className={`p-2.5 rounded-xl border text-left transition-all flex flex-col gap-1.5 cursor-pointer ${
                    isActive
                      ? "border-teal-400 bg-teal-950/40 shadow-[0_0_15px_rgba(20,184,166,0.2)] ring-1 ring-teal-400/40"
                      : "border-white/5 bg-zinc-950/40 hover:bg-zinc-800/40 hover:border-white/10"
                  } ${preset.glowClass}`}
                >
                  <div className="flex items-center justify-between">
                    <div className="p-1.5 rounded-lg bg-zinc-800/80 border border-white/5">
                      {preset.icon}
                    </div>
                    <span className="text-[10px] font-mono text-zinc-400">
                      ${preset.recommendedBudget.toFixed(2)}
                    </span>
                  </div>
                  <div>
                    <div className="text-xs font-bold text-zinc-200 leading-tight">
                      {preset.label}
                    </div>
                    <div className="text-[10px] text-zinc-400 truncate mt-0.5">
                      {preset.role}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {/* Title input */}
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="task-title"
              className="text-xs font-semibold text-zinc-300"
            >
              Task Title (Optional)
            </label>
            <input
              id="task-title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Competitive Pricing Analysis Report"
              className="px-3.5 py-2.5 rounded-xl border border-white/10 bg-zinc-950/60 text-xs text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:border-teal-400/60 focus:ring-1 focus:ring-teal-400/30 transition-all font-medium"
            />
          </div>

          {/* Instruction textarea */}
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between">
              <label
                htmlFor="task-instruction"
                className="text-xs font-semibold text-zinc-300"
              >
                Natural Language Instruction *
              </label>
              <span className="text-[10px] font-mono text-zinc-500">
                {instruction.length} chars
              </span>
            </div>
            <textarea
              id="task-instruction"
              rows={4}
              required
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="Describe what MAYA should accomplish. The LangGraph supervisor decomposes this into a validated DAG of specialist agents."
              className="px-3.5 py-2.5 rounded-xl border border-white/10 bg-zinc-950/60 text-xs text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:border-teal-400/60 focus:ring-1 focus:ring-teal-400/30 transition-all resize-none leading-relaxed font-mono"
            />
          </div>

          {/* Budget Quota Slider & Input */}
          <div className="p-3.5 rounded-xl bg-zinc-950/40 border border-white/5 flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <label
                htmlFor="task-budget-slider"
                className="text-xs font-semibold text-zinc-300 flex items-center gap-1.5"
              >
                <Sliders className="w-3.5 h-3.5 text-teal-400" />
                <span>Execution Budget Cap ($USD)</span>
              </label>
              <span className="text-xs font-mono font-bold text-teal-300 px-2 py-0.5 rounded-md bg-teal-950/60 border border-teal-500/30 shadow-[0_0_10px_rgba(20,184,166,0.15)]">
                ${Number(budgetUsd).toFixed(2)} USD
              </span>
            </div>

            <div className="flex items-center gap-4">
              <input
                id="task-budget-slider"
                type="range"
                min="0.50"
                max="20.00"
                step="0.50"
                value={budgetUsd}
                onChange={(e) => setBudgetUsd(parseFloat(e.target.value))}
                className="flex-1 accent-teal-400 cursor-pointer h-1.5 bg-zinc-800 rounded-lg"
              />
              <div className="relative">
                <DollarSign className="w-3 h-3 absolute left-2.5 top-2.5 text-zinc-500" />
                <input
                  id="task-budget"
                  type="number"
                  step="0.50"
                  min="0.50"
                  max="50.00"
                  value={budgetUsd}
                  onChange={(e) =>
                    setBudgetUsd(parseFloat(e.target.value) || 0)
                  }
                  className="w-24 pl-6 pr-2 py-1.5 rounded-lg border border-white/10 bg-zinc-900 text-xs text-zinc-100 focus:outline-none focus:border-teal-400 font-mono text-right"
                />
              </div>
            </div>

            {/* Quick Budget Presets */}
            <div className="flex items-center gap-2 pt-1">
              <span className="text-[10px] text-zinc-500 font-mono">
                Quick:
              </span>
              {[1.0, 2.0, 5.0, 10.0, 20.0].map((val) => (
                <button
                  key={val}
                  type="button"
                  onClick={() => setBudgetUsd(val)}
                  className={`px-2 py-0.5 rounded text-[11px] font-mono transition-colors ${
                    budgetUsd === val
                      ? "bg-teal-500/20 text-teal-300 border border-teal-500/40"
                      : "bg-zinc-800/60 text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  ${val.toFixed(0)}
                </button>
              ))}
            </div>
          </div>

          {/* RFC Idempotency Key Handling */}
          <div className="flex items-center justify-between p-3 rounded-xl bg-zinc-950/40 border border-white/5 text-xs font-mono">
            <div className="flex items-center gap-2 overflow-hidden mr-2">
              <Key className="w-3.5 h-3.5 text-zinc-400 shrink-0" />
              <span className="text-zinc-500 text-[11px] shrink-0">
                Idempotency Key:
              </span>
              <span className="text-zinc-300 truncate text-[11px]">
                {idempotencyKey}
              </span>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                type="button"
                onClick={generateKey}
                className="p-1 rounded-md text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
                title="Regenerate idempotency key"
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                onClick={handleCopyKey}
                className="p-1 rounded-md text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
                title="Copy idempotency key"
              >
                {copiedKey ? (
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                ) : (
                  <Copy className="w-3.5 h-3.5" />
                )}
              </button>
            </div>
          </div>

          {/* Action Buttons */}
          <div className="flex items-center justify-end gap-3 pt-4 border-t border-white/5">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="px-4 py-2 rounded-xl border border-white/10 text-xs font-semibold text-zinc-300 hover:bg-zinc-800/60 transition-colors cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !instruction.trim()}
              className="flex items-center gap-2 px-5 py-2 rounded-xl bg-gradient-to-r from-teal-500 to-emerald-500 hover:from-teal-400 hover:to-emerald-400 disabled:opacity-50 text-zinc-950 font-bold text-xs shadow-[0_0_20px_rgba(20,184,166,0.3)] transition-all cursor-pointer"
            >
              {submitting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Planning Task…</span>
                </>
              ) : (
                <>
                  <Bot className="w-4 h-4" />
                  <span>Execute Task</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
