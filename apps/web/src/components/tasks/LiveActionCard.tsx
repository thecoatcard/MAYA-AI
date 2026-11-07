"use client";

import {
  Activity,
  ArrowRight,
  CheckCircle2,
  Clock,
  Layers,
  ListTodo,
  Loader2,
  Sparkles,
} from "lucide-react";
import React, { useMemo } from "react";

import { useTaskEvents } from "../../hooks/useTaskEvents";

export interface LiveActionCardProps {
  readonly toolName: string;
  readonly taskId?: string | null;
  readonly status?: string;
  readonly intent?: string;
  readonly args?: Record<string, unknown>;
  readonly result?: Record<string, unknown>;
  readonly onViewDeliverables?: () => void;
  readonly onOpenTaskDetails?: (taskId: string) => void;
  readonly className?: string;
}

export function LiveActionCard({
  toolName,
  taskId = null,
  status = "running",
  intent,
  args,
  result,
  onViewDeliverables,
  onOpenTaskDetails,
  className = "",
}: LiveActionCardProps): React.JSX.Element {
  // Connect to SSE stream for live subtask DAG progress if taskId exists
  const { events } = useTaskEvents({
    taskId,
    enabled: Boolean(taskId && status !== "completed" && status !== "failed"),
  });

  const summaryResult =
    typeof result?.summary === "string" ? result.summary : undefined;

  // Calculate human-friendly tool label
  const friendlyToolName = useMemo(() => {
    switch (toolName) {
      case "submit_task":
        return "Autonomous Task Orchestrator";
      case "get_task_status":
        return "Task Telemetry Check";
      case "document_agent":
        return "Document Synthesis Agent";
      case "web_search":
        return "Web Research Agent";
      case "execute_python":
        return "Python Code Sandbox";
      default:
        return toolName.replace(/_/g, " ");
    }
  }, [toolName]);

  // Extract human-friendly prompt or target
  const displayTarget = useMemo(() => {
    if (intent) return intent;
    if (args?.intent && typeof args.intent === "string") return args.intent;
    if (args?.query && typeof args.query === "string") return args.query;
    if (args?.prompt && typeof args.prompt === "string") return args.prompt;
    return "Executing autonomous multi-step workflow";
  }, [intent, args]);

  // Compute live subtasks either from SSE events or synthesized default stages
  const subtasks = useMemo(() => {
    // If we have live SSE subtask updates, build dynamic list
    const stepMap = new Map<
      string,
      { title: string; state: "pending" | "running" | "completed" }
    >();

    // Initial 3 standard LangGraph execution stages
    stepMap.set("1", {
      title: "Task Registered & Idempotency Verified",
      state: "completed",
    });
    stepMap.set("2", {
      title: "Supervisor Decomposing Subtasks DAG",
      state: status === "completed" ? "completed" : "running",
    });
    stepMap.set("3", {
      title: "Compiling Artifacts & Updating Deliverable Canvas",
      state: status === "completed" ? "completed" : "pending",
    });

    // Check if SSE events reported step transitions
    events.forEach((ev) => {
      const payload = ev.payload;
      if (payload?.subtask_id && payload?.title) {
        const sId = String(payload.subtask_id);
        const sStatus = String(payload.status || "running");
        stepMap.set(sId, {
          title: String(payload.title),
          state:
            sStatus === "completed"
              ? "completed"
              : sStatus === "failed"
                ? "pending"
                : "running",
        });
      }
    });

    return Array.from(stepMap.values());
  }, [events, status]);

  const completedCount = subtasks.filter((s) => s.state === "completed").length;
  const progressPercent = Math.round((completedCount / subtasks.length) * 100);

  const isCompleted = status === "completed";
  const isFailed = status === "failed";

  return (
    <div
      className={`rounded-xl border border-teal-500/30 bg-zinc-950/80 p-4 shadow-lg backdrop-blur-md transition-all duration-200 ${className}`}
      data-testid="live-action-card"
    >
      {/* Header: Tool execution badge + Status pill */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800/80 pb-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-teal-500/10 border border-teal-500/30 text-teal-400">
            {isCompleted ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-400" />
            ) : isFailed ? (
              <Activity className="h-4 w-4 text-rose-400" />
            ) : (
              <Sparkles className="h-4 w-4 text-teal-400 animate-pulse" />
            )}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-mono font-bold uppercase tracking-wider text-teal-400">
                Maya Called Tool
              </span>
              <span className="rounded bg-teal-950 px-1.5 py-0.5 text-[10px] font-mono text-teal-300 border border-teal-800/50">
                {friendlyToolName}
              </span>
            </div>
            {taskId && (
              <span className="text-[10px] font-mono text-zinc-500">
                ID: {taskId}
              </span>
            )}
          </div>
        </div>

        {/* Live Status Badge */}
        <div className="flex items-center gap-1.5">
          {isCompleted ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2.5 py-0.5 text-xs font-semibold text-emerald-400 border border-emerald-500/30">
              <CheckCircle2 className="h-3 w-3" />
              Completed
            </span>
          ) : isFailed ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/10 px-2.5 py-0.5 text-xs font-semibold text-rose-400 border border-rose-500/30">
              Failed
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-teal-500/10 px-2.5 py-0.5 text-xs font-semibold text-teal-300 border border-teal-500/30 animate-pulse">
              <Loader2 className="h-3 w-3 animate-spin text-teal-400" />
              Processing Live
            </span>
          )}
        </div>
      </div>

      {/* Target Intent / Description */}
      <div className="mt-3">
        <p className="text-xs text-zinc-300 font-medium leading-relaxed">
          <span className="text-zinc-500 font-mono text-[11px] mr-1.5">
            Action:
          </span>
          {displayTarget}
        </p>
        {summaryResult && (
          <p className="mt-1 text-[11px] text-teal-300 font-mono bg-teal-950/40 px-2 py-1 rounded border border-teal-800/40">
            {summaryResult}
          </p>
        )}
      </div>

      {/* Real-time Subtask DAG Pipeline */}
      <div className="mt-3.5 rounded-lg border border-zinc-800/60 bg-zinc-900/40 p-2.5">
        <div className="flex items-center justify-between text-[11px] font-mono text-zinc-400 mb-2">
          <span>Execution Pipeline</span>
          <span>
            {progressPercent}% Complete ({completedCount}/{subtasks.length})
          </span>
        </div>

        {/* Progress bar */}
        <div className="h-1.5 w-full rounded-full bg-zinc-800 overflow-hidden mb-2.5">
          <div
            className="h-full bg-gradient-to-r from-teal-500 to-emerald-400 transition-all duration-300 rounded-full"
            style={{ width: `${Math.max(progressPercent, 10)}%` }}
          />
        </div>

        {/* Step list */}
        <div className="space-y-1.5">
          {subtasks.map((step, idx) => (
            <div
              key={idx}
              className={`flex items-center gap-2 text-xs font-mono transition-colors ${
                step.state === "completed"
                  ? "text-emerald-300"
                  : step.state === "running"
                    ? "text-teal-300 font-semibold"
                    : "text-zinc-500"
              }`}
            >
              {step.state === "completed" ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
              ) : step.state === "running" ? (
                <Loader2 className="h-3.5 w-3.5 text-teal-400 animate-spin shrink-0" />
              ) : (
                <Clock className="h-3.5 w-3.5 text-zinc-600 shrink-0" />
              )}
              <span className="truncate">{step.title}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Direct Action Buttons */}
      <div className="mt-3.5 flex flex-wrap items-center gap-2 pt-2 border-t border-zinc-800/40">
        {onViewDeliverables && (
          <button
            type="button"
            onClick={onViewDeliverables}
            className="inline-flex items-center gap-1.5 rounded-lg bg-teal-500/10 px-3 py-1.5 text-xs font-medium text-teal-300 border border-teal-500/30 hover:bg-teal-500/20 transition-colors"
          >
            <Layers className="h-3.5 w-3.5" />
            <span>View Deliverable Canvas</span>
            <ArrowRight className="h-3 w-3" />
          </button>
        )}
        {taskId && onOpenTaskDetails && (
          <button
            type="button"
            onClick={() => onOpenTaskDetails(taskId)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-zinc-800/60 px-3 py-1.5 text-xs font-medium text-zinc-300 border border-zinc-700/60 hover:bg-zinc-800 transition-colors"
          >
            <ListTodo className="h-3.5 w-3.5 text-zinc-400" />
            <span>Task Center Telemetry</span>
          </button>
        )}
      </div>
    </div>
  );
}
