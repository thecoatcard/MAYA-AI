"use client";

import {
  Bot,
  Check,
  Clock,
  Code,
  Copy,
  FileText,
  Mail,
  RefreshCw,
  Search,
  type LucideIcon,
} from "lucide-react";
import React, { useCallback, useEffect, useState } from "react";

import {
  submitTask,
  type TaskSubmissionResponse,
} from "../../lib/tasks-client";
import { Button, GlassModal, IconButton, Notice } from "../ui";

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
  readonly icon: LucideIcon;
}

/** Starting points for the form. The text is an editable template, never sent without review. */
const DELEGATION_PRESETS: readonly DelegationPreset[] = [
  {
    id: "research",
    label: "Research",
    role: "Investigation with sources",
    defaultTitle: "Competitive Market & Technical Research",
    defaultInstruction:
      "Investigate the latest architectural benchmarks and market alternatives for [Topic]. Search the web, extract primary sources, and return structured citations with quote snippets and source URLs.",
    recommendedBudget: 1.5,
    icon: Search,
  },
  {
    id: "coding",
    label: "Coding",
    role: "Bug fix and engineering",
    defaultTitle: "Test Reproduction & Fix",
    defaultInstruction:
      "Reproduce the failing test in the sandbox, develop a code patch, verify the tests pass, commit locally, and prepare a pull request for my approval.",
    recommendedBudget: 4.0,
    icon: Code,
  },
  {
    id: "document",
    label: "Document",
    role: "Reports and files",
    defaultTitle: "Executive Report & Spreadsheet",
    defaultInstruction:
      "Turn the analysed operational and financial data into an executive PDF report and a companion spreadsheet.",
    recommendedBudget: 2.5,
    icon: FileText,
  },
  {
    id: "email",
    label: "Email",
    role: "Inbox and drafts",
    defaultTitle: "Priority Inbox Triage & Draft Review",
    defaultInstruction:
      "Review recent unread email threads for action items, compose replies, and stop at the draft so I can approve before anything is sent.",
    recommendedBudget: 1.0,
    icon: Mail,
  },
  {
    id: "pm",
    label: "PM",
    role: "Daily briefing",
    defaultTitle: "Daily Morning Briefing & Sprint Sync",
    defaultInstruction:
      "Check today's calendar and the sprint board for blockers. Produce a concise briefing with time allocations and risks.",
    recommendedBudget: 1.0,
    icon: Clock,
  },
];

const FIELD =
  "w-full min-w-0 rounded-maya-md border border-maya-control-border bg-maya-pane px-3 py-2 text-maya-body text-maya-text placeholder:text-maya-muted hover:border-maya-border-strong focus:border-maya-focus";

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

  // A new request key per dialog opening, so a double submit never creates two tasks.
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

  const handleSelectPreset = (preset: DelegationPreset): void => {
    setActivePreset(preset.id);
    setTitle(preset.defaultTitle);
    setInstruction(preset.defaultInstruction);
    setBudgetUsd(preset.recommendedBudget);
  };

  const handleCopyKey = (): void => {
    navigator.clipboard.writeText(idempotencyKey).then(() => {
      setCopiedKey(true);
      setTimeout(() => setCopiedKey(false), 2000);
    });
  };

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
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
    <GlassModal
      isOpen={isOpen}
      onClose={onClose}
      size="lg"
      title="Assign Autonomous Task"
      description="MAYA plans the steps, works on them in the background and asks you before anything that needs approval."
    >
      <div className="flex min-w-0 flex-col gap-5">
        {error && (
          <Notice tone="danger" onDismiss={() => setError(null)}>
            {error}
          </Notice>
        )}

        <div className="flex min-w-0 flex-col gap-2">
          <h3 className="maya-label m-0">Start from a template</h3>
          <div className="grid min-w-0 grid-cols-2 gap-2 sm:grid-cols-3">
            {DELEGATION_PRESETS.map((preset) => {
              const isActive = activePreset === preset.id;
              const Icon = preset.icon;
              return (
                <button
                  key={preset.id}
                  type="button"
                  aria-pressed={isActive}
                  onClick={() => handleSelectPreset(preset)}
                  className={`flex min-w-0 flex-col gap-1 border p-2 text-left motion-interactive ${
                    isActive
                      ? "border-maya-accent bg-maya-accent-soft"
                      : "border-maya-border bg-maya-raised hover:bg-maya-hover"
                  }`}
                >
                  <span className="flex min-w-0 items-center justify-between gap-2">
                    <Icon
                      aria-hidden="true"
                      size={16}
                      className="shrink-0 text-maya-secondary"
                    />
                    <span className="maya-tabular font-mono text-maya-caption text-maya-muted">
                      ${preset.recommendedBudget.toFixed(2)}
                    </span>
                  </span>
                  <span className="min-w-0 text-maya-small font-medium text-maya-text">
                    {preset.label}
                  </span>
                  <span className="min-w-0 text-maya-caption text-maya-muted [overflow-wrap:anywhere]">
                    {preset.role}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        <form
          onSubmit={handleSubmit}
          className="flex min-w-0 flex-col gap-4"
          noValidate
        >
          <div className="flex min-w-0 flex-col gap-1.5">
            <label
              htmlFor="task-title"
              className="text-maya-caption font-medium text-maya-text"
            >
              Task Title (Optional)
            </label>
            <input
              id="task-title"
              type="text"
              dir="auto"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Competitive Pricing Analysis Report"
              className={FIELD}
            />
          </div>

          <div className="flex min-w-0 flex-col gap-1.5">
            <div className="flex min-w-0 items-center justify-between gap-2">
              <label
                htmlFor="task-instruction"
                className="text-maya-caption font-medium text-maya-text"
              >
                What should MAYA do? *
              </label>
              <span className="maya-tabular shrink-0 font-mono text-maya-caption text-maya-muted">
                {instruction.length} chars
              </span>
            </div>
            <textarea
              id="task-instruction"
              rows={5}
              required
              dir="auto"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="Describe what MAYA should accomplish. MAYA splits it into steps and shows the plan."
              className={`${FIELD} resize-y leading-relaxed`}
            />
          </div>

          <div className="flex min-w-0 flex-col gap-3 rounded-maya-lg border border-maya-border p-3">
            <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
              <label
                htmlFor="task-budget-slider"
                className="text-maya-caption font-medium text-maya-text"
              >
                Execution Budget Cap (USD)
              </label>
              <span className="maya-tabular font-mono text-maya-small font-medium text-maya-text">
                ${Number(budgetUsd).toFixed(2)} USD
              </span>
            </div>
            <div className="flex min-w-0 flex-wrap items-center gap-3">
              <input
                id="task-budget-slider"
                type="range"
                min="0.50"
                max="20.00"
                step="0.50"
                value={budgetUsd}
                onChange={(e) => setBudgetUsd(parseFloat(e.target.value))}
                className="min-h-8 min-w-32 flex-1 accent-maya-accent pointer-coarse:min-h-11"
              />
              <input
                id="task-budget"
                aria-label="Budget cap in USD"
                type="number"
                step="0.50"
                min="0.50"
                max="50.00"
                value={budgetUsd}
                onChange={(e) => setBudgetUsd(parseFloat(e.target.value) || 0)}
                className={`${FIELD} maya-tabular w-28 text-right font-mono`}
              />
            </div>
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="text-maya-caption text-maya-muted">Quick</span>
              {[1.0, 2.0, 5.0, 10.0, 20.0].map((val) => (
                <button
                  key={val}
                  type="button"
                  aria-pressed={budgetUsd === val}
                  onClick={() => setBudgetUsd(val)}
                  className={`min-h-8 border px-2 font-mono text-maya-caption pointer-coarse:min-h-11 ${
                    budgetUsd === val
                      ? "border-maya-accent bg-maya-accent-soft text-maya-text"
                      : "border-maya-border-strong bg-maya-raised text-maya-secondary hover:bg-maya-hover"
                  }`}
                >
                  ${val.toFixed(0)}
                </button>
              ))}
            </div>
          </div>

          <details className="min-w-0 rounded-maya-lg border border-maya-border p-3">
            <summary className="cursor-pointer text-maya-caption text-maya-secondary">
              Request key (advanced)
            </summary>
            <div className="mt-2 flex min-w-0 items-center gap-2">
              <code className="min-w-0 flex-1 font-mono text-maya-mono text-maya-muted [overflow-wrap:anywhere]">
                {idempotencyKey}
              </code>
              <IconButton
                size="sm"
                label="Regenerate idempotency key"
                icon={<RefreshCw size={14} />}
                onClick={generateKey}
              />
              <IconButton
                size="sm"
                label="Copy idempotency key"
                icon={copiedKey ? <Check size={14} /> : <Copy size={14} />}
                onClick={handleCopyKey}
              />
            </div>
            <p className="m-0 mt-2 text-maya-caption text-maya-muted">
              Sending the same key twice creates the task once.
            </p>
          </details>

          <div className="flex flex-wrap items-center justify-end gap-3 border-t border-maya-border pt-4">
            <Button onClick={onClose} disabled={submitting}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              loading={submitting}
              disabled={!instruction.trim()}
              leftIcon={<Bot size={16} />}
            >
              Execute Task
            </Button>
          </div>
        </form>
      </div>
    </GlassModal>
  );
}
