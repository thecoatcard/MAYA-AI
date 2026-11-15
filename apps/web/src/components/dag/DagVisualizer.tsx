"use client";

import { storedReviewLabel } from "../../lib/voice-task-updates";
import {
  Bot,
  Check,
  Clock,
  Code,
  Copy,
  ExternalLink,
  FileText,
  Layers,
  Mail,
  Search,
  type LucideIcon,
} from "lucide-react";
import React, { useMemo, useRef, useState } from "react";

import type { SubtaskResponse } from "../../lib/tasks-client";
import { FailureReason } from "../tasks/FailureReason";
import { TaskStatusBadge } from "../tasks/TaskStatusBadge";
import {
  Badge,
  CodeBlockShell,
  EmptyState,
  IconButton,
  ScrollArea,
  WrapText,
} from "../ui";

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

const AGENT_ICON: Readonly<Record<string, LucideIcon>> = {
  research: Search,
  coding: Code,
  document: FileText,
  email: Mail,
  pm: Clock,
};

function agentIcon(agent: string): LucideIcon {
  return AGENT_ICON[agent.toLowerCase()] ?? Bot;
}

function stepNumber(index: number): string {
  return String(index + 1).padStart(2, "0");
}

function prettyJson(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

function SectionTitle({
  children,
}: {
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return <h4 className="maya-label m-0 mb-1">{children}</h4>;
}

function CopyId({ id }: { readonly id: string }): React.JSX.Element {
  const [copied, setCopied] = useState(false);
  return (
    <IconButton
      size="sm"
      label={copied ? "Step id copied" : "Copy step id"}
      icon={copied ? <Check size={14} /> : <Copy size={14} />}
      onClick={() => {
        try {
          void navigator.clipboard?.writeText(id).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          });
        } catch {
          // Clipboard unavailable: the id is still selectable text.
        }
      }}
    />
  );
}

/**
 * The plan of a task as a list of steps in order, with an inspector for the
 * selected step. Layout: one column; side by side only when the container is
 * wide. The step list and the inspector each scroll inside their own region,
 * so up to 30 steps never overlap, widen the page or cut a card or pill.
 */
export function DagVisualizer({
  subtasks,
  selectedSubtaskId,
  onSelectSubtask,
  className = "",
}: DagVisualizerProps): React.JSX.Element {
  const [internalSelectedId, setInternalSelectedId] = useState<string | null>(
    subtasks[0]?.id ?? null,
  );
  const nodeRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const activeId =
    selectedSubtaskId !== undefined ? selectedSubtaskId : internalSelectedId;
  const activeIndex = Math.max(
    0,
    subtasks.findIndex(
      (st) => st.id === activeId || String(st.id) === activeId,
    ),
  );
  const activeSubtask = subtasks[activeIndex] ?? null;

  const numberById = useMemo(() => {
    const map = new Map<string, string>();
    subtasks.forEach((st, index) => map.set(st.id, stepNumber(index)));
    return map;
  }, [subtasks]);

  const dependencyLabel = (ids: readonly string[]): string =>
    ids.map((dep) => numberById.get(dep) ?? dep).join(", ");

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
    if (!Array.isArray(rawList)) return [];
    return rawList.map((item, idx) => {
      if (typeof item === "string") {
        return { id: idx + 1, title: item };
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
            `Source ${idx + 1}`,
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

  const citationCount = useMemo(() => {
    const proof = activeSubtask?.verification_proof as
      Record<string, unknown> | null | undefined;
    return typeof proof?.citations === "number" ? proof.citations : null;
  }, [activeSubtask]);

  if (subtasks.length === 0) {
    return (
      <EmptyState
        className={className}
        icon={Bot}
        title="No steps planned yet"
        description="The plan appears here once MAYA has broken the task into steps."
      />
    );
  }

  const select = (st: SubtaskResponse): void => {
    setInternalSelectedId(st.id);
    onSelectSubtask?.(st);
  };

  const onNodeKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    index: number,
  ): void => {
    let next = index;
    if (event.key === "ArrowDown")
      next = Math.min(index + 1, subtasks.length - 1);
    else if (event.key === "ArrowUp") next = Math.max(index - 1, 0);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = subtasks.length - 1;
    else return;
    event.preventDefault();
    nodeRefs.current[next]?.focus();
  };

  return (
    <div
      className={`@container min-w-0 max-w-full ${className}`.trim()}
      data-testid="dag"
    >
      <div className="grid min-w-0 grid-cols-1 gap-3 @[52rem]:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
        {/* Step list */}
        <section
          aria-label="Plan steps"
          className="min-w-0 rounded-maya-lg overflow-hidden border border-maya-border bg-maya-pane"
        >
          <header className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-maya-border px-3 py-2">
            <h3 className="m-0 flex min-w-0 items-center gap-2 text-maya-small font-medium text-maya-text">
              <Layers
                aria-hidden="true"
                size={16}
                className="shrink-0 text-maya-muted"
              />
              <span className="min-w-0">Plan</span>
            </h3>
            <Badge tone="neutral" icon={Layers}>
              {subtasks.length} {subtasks.length === 1 ? "step" : "steps"}
            </Badge>
          </header>
          <ScrollArea label="Plan steps list" className="max-h-[26rem] p-2">
            <ol className="m-0 flex list-none flex-col gap-2 p-0">
              {subtasks.map((st, index) => {
                const isSelected = index === activeIndex;
                const Icon = agentIcon(st.assigned_agent);
                const deps = st.dependencies ?? [];
                return (
                  <li key={st.id} className="flex min-w-0 shrink-0 flex-col">
                    {deps.length > 0 && (
                      <p
                        className="m-0 px-1 pb-1 text-maya-caption text-maya-muted [overflow-wrap:anywhere]"
                        title={`Runs after: ${deps.join(", ")}`}
                      >
                        depends on: {dependencyLabel(deps)}
                      </p>
                    )}
                    <button
                      type="button"
                      ref={(el) => {
                        nodeRefs.current[index] = el;
                      }}
                      tabIndex={isSelected ? 0 : -1}
                      aria-pressed={isSelected}
                      onClick={() => select(st)}
                      onKeyDown={(event) => onNodeKeyDown(event, index)}
                      className={`flex w-full min-w-0 flex-wrap items-start gap-x-3 gap-y-2 border p-3 text-left text-maya-text motion-interactive ${
                        isSelected
                          ? "border-maya-accent bg-maya-accent-soft"
                          : "border-maya-border bg-maya-raised hover:bg-maya-hover"
                      }`}
                    >
                      <span className="maya-tabular w-6 shrink-0 font-mono text-maya-mono text-maya-muted">
                        {stepNumber(index)}
                      </span>
                      <span className="flex min-w-0 flex-1 basis-40 flex-col gap-1">
                        <span className="flex min-w-0 items-center gap-2 text-maya-caption font-medium text-maya-secondary capitalize">
                          <Icon
                            aria-hidden="true"
                            size={14}
                            className="shrink-0"
                          />
                          <span className="min-w-0 [overflow-wrap:anywhere]">
                            {st.assigned_agent} step
                          </span>
                        </span>
                        <span
                          dir="auto"
                          title={st.description}
                          className="min-w-0 text-maya-small [overflow-wrap:anywhere]"
                        >
                          {st.description}
                        </span>
                        {storedReviewLabel(st.output_result) !== null && (
                          <span
                            className="min-w-0 text-maya-caption text-maya-muted [overflow-wrap:anywhere]"
                            data-testid="step-review"
                          >
                            {storedReviewLabel(st.output_result)}
                          </span>
                        )}
                      </span>
                      <TaskStatusBadge status={st.status} />
                    </button>
                  </li>
                );
              })}
            </ol>
          </ScrollArea>
        </section>

        {/* Inspector */}
        {activeSubtask && (
          <section
            aria-label="Step details"
            className="min-w-0 rounded-maya-lg overflow-hidden border border-maya-border bg-maya-pane"
          >
            <header className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-maya-border px-3 py-2">
              <h3 className="m-0 min-w-0 text-maya-small font-medium text-maya-text capitalize [overflow-wrap:anywhere]">
                Step {stepNumber(activeIndex)}: {activeSubtask.assigned_agent}
              </h3>
              <TaskStatusBadge status={activeSubtask.status} />
            </header>
            <ScrollArea
              label="Step details content"
              className="grid max-h-[26rem] content-start gap-4 p-3 text-maya-small"
            >
              <div className="min-w-0">
                <SectionTitle>Description</SectionTitle>
                <WrapText as="p" dir="auto" className="m-0 block">
                  {activeSubtask.description}
                </WrapText>
              </div>

              <div className="flex min-w-0 items-center gap-1">
                <span className="maya-label shrink-0">Step id</span>
                <WrapText
                  as="code"
                  className="min-w-0 flex-1 font-mono text-maya-mono text-maya-muted"
                >
                  {activeSubtask.id}
                </WrapText>
                <CopyId id={activeSubtask.id} />
              </div>

              {activeSubtask.dependencies &&
                activeSubtask.dependencies.length > 0 && (
                  <div className="min-w-0">
                    <SectionTitle>Runs after</SectionTitle>
                    <ul className="m-0 flex list-none flex-wrap gap-1 p-0">
                      {activeSubtask.dependencies.map((dep) => (
                        <li key={dep} className="min-w-0">
                          <Badge tone="neutral" title={dep}>
                            Step {numberById.get(dep) ?? dep}
                          </Badge>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

              {activeSubtask.error_message && (
                <div className="min-w-0">
                  <SectionTitle>What went wrong</SectionTitle>
                  <FailureReason
                    error={activeSubtask.error_message}
                    note={
                      activeSubtask.retry_count > 0
                        ? `Retried ${activeSubtask.retry_count} ${
                            activeSubtask.retry_count === 1 ? "time" : "times"
                          }.`
                        : undefined
                    }
                  />
                </div>
              )}

              {citationCount !== null && extractedCitations.length === 0 && (
                <p className="m-0 text-maya-caption text-maya-secondary">
                  {citationCount} {citationCount === 1 ? "source" : "sources"}{" "}
                  recorded.
                </p>
              )}

              {extractedCitations.length > 0 && (
                <div className="min-w-0">
                  <SectionTitle>
                    Sources ({extractedCitations.length})
                  </SectionTitle>
                  <ul className="m-0 flex list-none flex-col gap-2 p-0">
                    {extractedCitations.map((c) => (
                      <li
                        key={String(c.id)}
                        className="min-w-0 rounded-maya-md border border-maya-border bg-maya-raised p-2 text-maya-caption"
                      >
                        <div className="flex min-w-0 items-start justify-between gap-2">
                          <span
                            dir="auto"
                            className="min-w-0 font-medium text-maya-text [overflow-wrap:anywhere]"
                          >
                            {c.title}
                          </span>
                          {c.url && (
                            <a
                              href={c.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="shrink-0 text-maya-link"
                              title="Open source in a new window"
                              aria-label={`Open source: ${c.title ?? c.url}`}
                            >
                              <ExternalLink aria-hidden="true" size={14} />
                            </a>
                          )}
                        </div>
                        {c.snippet && (
                          <p
                            dir="auto"
                            className="m-0 mt-1 text-maya-secondary [overflow-wrap:anywhere]"
                          >
                            &ldquo;{c.snippet}&rdquo;
                          </p>
                        )}
                        {c.domain && (
                          <p className="m-0 mt-1 font-mono text-maya-muted [overflow-wrap:anywhere]">
                            {c.domain}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {Boolean(activeSubtask.verification_proof) && (
                <div className="min-w-0">
                  <SectionTitle>Verification Proof</SectionTitle>
                  <CodeBlockShell
                    label="Verification proof"
                    maxHeight="14rem"
                    className="whitespace-pre-wrap [overflow-wrap:anywhere]"
                  >
                    {prettyJson(activeSubtask.verification_proof)}
                  </CodeBlockShell>
                </div>
              )}

              {activeSubtask.output_result !== undefined &&
                activeSubtask.output_result !== null && (
                  <div className="min-w-0">
                    <SectionTitle>Output</SectionTitle>
                    <CodeBlockShell
                      label="Step output"
                      maxHeight="14rem"
                      className="whitespace-pre-wrap [overflow-wrap:anywhere]"
                    >
                      {prettyJson(activeSubtask.output_result)}
                    </CodeBlockShell>
                  </div>
                )}

              {activeSubtask.input_data &&
                Object.keys(activeSubtask.input_data).length > 0 && (
                  <div className="min-w-0">
                    <SectionTitle>Input</SectionTitle>
                    <CodeBlockShell
                      label="Step input"
                      maxHeight="12rem"
                      className="whitespace-pre-wrap [overflow-wrap:anywhere]"
                    >
                      {prettyJson(activeSubtask.input_data)}
                    </CodeBlockShell>
                  </div>
                )}
            </ScrollArea>
          </section>
        )}
      </div>
    </div>
  );
}
