"use client";

import {
  AlertCircle,
  AlertTriangle,
  Ban,
  CheckCircle2,
  Clock,
  DollarSign,
  Layers,
  Loader2,
  Radio,
  ShieldAlert,
  Sparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  useTaskEvents,
  type TaskEventPayload,
} from "../../hooks/useTaskEvents";
import {
  abortTask,
  getTask,
  type SubtaskResponse,
  type TaskAbortResponse,
  type TaskDetailResponse,
} from "../../lib/tasks-client";
import { BrowserTakeoverModal } from "../browser/BrowserTakeoverModal";
import { resumeBrowserSession } from "../../lib/browser-client";

export interface TaskCenterDrawerProps {
  readonly taskId: string | null;
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly onTaskAborted?: (abortResult: TaskAbortResponse) => void;
}

export function TaskCenterDrawer({
  taskId,
  isOpen,
  onClose,
  onTaskAborted,
}: TaskCenterDrawerProps) {
  const [task, setTask] = useState<TaskDetailResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [isAborting, setIsAborting] = useState<boolean>(false);
  const [abortResult, setAbortResult] = useState<TaskAbortResponse | null>(
    null,
  );
  const [browserTakeoverSession, setBrowserTakeoverSession] = useState<{
    sessionId: string;
    url: string;
    botType?: string;
    screenshotBase64?: string;
  } | null>(null);

  // Live SSE stream handler
  const handleLiveEvent = useCallback(
    (event: TaskEventPayload) => {
      if (!event.payload) return;

      const payload = event.payload;
      const subtaskId = payload.subtask_id as string | undefined;
      const newStatus = payload.status as string | undefined;

      if (event.event_type === "browser_takeover_required") {
        setBrowserTakeoverSession({
          sessionId: String(payload.session_id || taskId || ""),
          url: String(payload.current_url || "about:blank"),
          botType: payload.challenge_type
            ? String(payload.challenge_type)
            : undefined,
          screenshotBase64: payload.screenshot_base64
            ? String(payload.screenshot_base64)
            : undefined,
        });
      }

      setTask((prev) => {
        if (!prev) return prev;

        // Update specific subtask if payload targets one
        let updatedSubtasks = prev.subtasks;
        if (subtaskId && newStatus) {
          updatedSubtasks = prev.subtasks.map((st) =>
            st.id === subtaskId || String(st.id) === subtaskId
              ? {
                  ...st,
                  status: newStatus,
                  output_result: payload.output ?? st.output_result,
                }
              : st,
          );
        }

        // Check task-level status changes
        let updatedStatus = prev.status;
        let updatedPlanStatus = prev.plan_status;
        if (event.event_type === "task_completed") {
          updatedStatus = "completed";
          updatedPlanStatus = "completed";
        } else if (event.event_type === "task_failed") {
          updatedStatus = "failed";
          updatedPlanStatus = "failed";
        } else if (event.event_type === "task_paused_approval") {
          updatedStatus = "awaiting_approval";
          updatedPlanStatus = "paused";
        }

        return {
          ...prev,
          status: updatedStatus,
          plan_status: updatedPlanStatus,
          subtasks: updatedSubtasks,
        };
      });
    },
    [taskId],
  );

  const { isConnected } = useTaskEvents({
    taskId,
    enabled: isOpen && Boolean(taskId),
    onEvent: handleLiveEvent,
  });

  // Fetch initial task state
  useEffect(() => {
    if (!isOpen || !taskId) {
      setTask(null);
      setError(null);
      setAbortResult(null);
      return;
    }

    let isCancelled = false;
    setLoading(true);
    setError(null);

    getTask(taskId)
      .then((data) => {
        if (!isCancelled) {
          setTask(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!isCancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        }
      });

    return () => {
      isCancelled = true;
    };
  }, [isOpen, taskId]);

  // Handle Abort action
  const handleAbort = async () => {
    if (!taskId || isAborting) return;
    setIsAborting(true);
    try {
      const res = await abortTask(taskId);
      setAbortResult(res);
      setTask((prev) =>
        prev
          ? {
              ...prev,
              status: "cancelled",
              plan_status: "failed",
            }
          : prev,
      );
      if (onTaskAborted) {
        onTaskAborted(res);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to abort task");
    } finally {
      setIsAborting(false);
    }
  };

  // Close on ESC
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  // Derive plain-language progress
  const progressSummary = useMemo(() => {
    if (!task) return "";
    const subtasks = task.subtasks || [];
    const total = subtasks.length;
    if (total === 0) return "Analyzing instruction...";

    const completed = subtasks.filter((st) => st.status === "completed").length;
    const inProgress = subtasks.find((st) => st.status === "in_progress");
    const awaitingApproval = subtasks.find(
      (st) => st.status === "awaiting_approval",
    );

    if (task.status === "completed") {
      return `All ${total} subtasks completed successfully`;
    }
    if (task.status === "cancelled") {
      return "Task was cancelled and rollbacks executed";
    }
    if (awaitingApproval) {
      return `Approval required before: "${awaitingApproval.description}"`;
    }
    if (inProgress) {
      const currentIdx = subtasks.indexOf(inProgress) + 1;
      return `Step ${currentIdx} of ${total} — ${inProgress.description}`;
    }
    return `Progress: ${completed} of ${total} completed`;
  }, [task]);

  if (!isOpen) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="task-center-title"
      className="fixed inset-0 z-50 overflow-hidden bg-black/60 backdrop-blur-md transition-opacity"
      data-testid="task-center-drawer-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="fixed inset-y-0 right-0 flex max-w-full pl-6 sm:pl-10">
        <div
          className="w-screen max-w-2xl bg-zinc-900/90 backdrop-blur-2xl text-zinc-100 shadow-2xl border-l border-white/10 flex flex-col"
          data-testid="task-center-drawer-panel"
        >
          {/* Header */}
          <div className="p-6 border-b border-white/5 flex items-start justify-between bg-zinc-950/40">
            <div>
              <div className="flex items-center gap-3">
                <h2
                  id="task-center-title"
                  className="text-lg font-bold tracking-tight text-zinc-100"
                >
                  {task?.title || "Task Center"}
                </h2>
                {task && (
                  <span
                    data-testid="task-status-badge"
                    className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-mono font-semibold uppercase tracking-wider ${
                      task.status === "completed"
                        ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 shadow-[0_0_12px_rgba(16,185,129,0.25)]"
                        : task.status === "awaiting_approval"
                          ? "bg-amber-500/15 text-amber-300 border border-amber-500/30 shadow-[0_0_12px_rgba(245,158,11,0.25)]"
                          : task.status === "cancelled" ||
                              task.status === "failed"
                            ? "bg-rose-500/15 text-rose-300 border border-rose-500/30 shadow-[0_0_12px_rgba(244,63,94,0.25)]"
                            : "bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 shadow-[0_0_12px_rgba(6,182,212,0.25)]"
                    }`}
                  >
                    {task.status}
                  </span>
                )}
                {isConnected && (
                  <span
                    title="Live SSE connected"
                    data-testid="sse-live-indicator"
                    className="inline-flex items-center gap-1 text-[10px] font-mono text-emerald-400 bg-emerald-950/60 border border-emerald-500/30 px-2 py-0.5 rounded-full"
                  >
                    <Radio className="w-2.5 h-2.5 animate-pulse text-emerald-400" />
                    <span>Live</span>
                  </span>
                )}
              </div>
              <p
                className="text-xs text-zinc-400 mt-1.5 font-mono"
                data-testid="task-progress-summary"
              >
                {progressSummary}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              data-testid="drawer-close-button"
              className="p-2 text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/60 rounded-xl transition-colors cursor-pointer"
              aria-label="Close task center"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Abort Notification Banner */}
          {abortResult && (
            <div
              data-testid="abort-result-banner"
              className="p-4 bg-amber-950/50 border-b border-amber-500/30 text-amber-200 text-xs flex items-start gap-3"
            >
              <AlertTriangle className="w-5 h-5 shrink-0 text-amber-400" />
              <div>
                <p className="font-semibold text-sm">Task cancelled</p>
                <p className="text-xs text-amber-300/90 mt-0.5 font-mono">
                  Triggered {abortResult.compensations_triggered} compensation
                  actions ({abortResult.executed} executed, {abortResult.failed}{" "}
                  failed).
                </p>
                {abortResult.notifications.length > 0 && (
                  <ul className="mt-2 text-xs list-disc list-inside space-y-1 font-mono">
                    {abortResult.notifications.map((note, idx) => (
                      <li key={idx}>{note}</li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}

          {/* Error Banner */}
          {error && (
            <div
              data-testid="task-error-banner"
              className="p-4 bg-rose-950/60 border-b border-rose-500/40 text-rose-200 text-xs flex items-center gap-3"
            >
              <AlertCircle className="w-5 h-5 shrink-0 text-rose-400" />
              <p>{error}</p>
            </div>
          )}

          {/* Content Body */}
          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            {loading ? (
              <div
                data-testid="task-center-loading"
                className="flex flex-col items-center justify-center py-24 text-zinc-500 gap-3"
              >
                <Loader2 className="w-8 h-8 animate-spin text-teal-400" />
                <p className="text-sm font-medium">
                  Loading task execution graph...
                </p>
              </div>
            ) : !task ? (
              <div
                data-testid="task-center-empty"
                className="text-center py-24 text-zinc-500"
              >
                <p className="text-sm">No task selected.</p>
              </div>
            ) : (
              <>
                {/* Instruction Glass Card */}
                <div className="bg-zinc-950/50 border border-white/10 rounded-2xl p-4 shadow-lg backdrop-blur-xl">
                  <h3 className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-1.5 flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-teal-400" />
                    <span>Instruction</span>
                  </h3>
                  <p className="text-xs text-zinc-200 leading-relaxed font-mono whitespace-pre-wrap">
                    {task.instruction}
                  </p>
                  <div className="flex flex-wrap items-center gap-4 mt-3 pt-3 border-t border-white/5 text-xs text-zinc-400 font-mono">
                    <span className="flex items-center gap-1">
                      <DollarSign className="w-3 h-3 text-zinc-500" />
                      Budget:{" "}
                      <strong className="text-zinc-200 font-semibold">
                        ${Number(task.budget_usd).toFixed(2)}
                      </strong>
                    </span>
                    <span>
                      Spent:{" "}
                      <strong className="text-teal-300 font-semibold">
                        ${Number(task.spent_usd).toFixed(4)}
                      </strong>
                    </span>
                    <span>
                      Subtasks:{" "}
                      <strong className="text-zinc-200 font-semibold">
                        {task.subtasks.length}
                      </strong>
                    </span>
                  </div>
                </div>

                {/* Subtask Execution Graph */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-2">
                      <Layers className="w-3.5 h-3.5 text-teal-400" />
                      <span>Subtask DAG Execution</span>
                    </h3>
                    <span className="text-[11px] font-mono text-zinc-500">
                      LangGraph Pipeline
                    </span>
                  </div>

                  {task.subtasks.length === 0 ? (
                    <p className="text-sm text-zinc-500 italic p-4 rounded-xl bg-zinc-950/30 border border-white/5">
                      No subtasks defined yet.
                    </p>
                  ) : (
                    <div className="space-y-3" data-testid="subtasks-list">
                      {task.subtasks.map((st, idx) => (
                        <SubtaskCard
                          key={st.id || idx}
                          subtask={st}
                          index={idx}
                          onTakeoverClick={(sub) => {
                            setBrowserTakeoverSession({
                              sessionId: sub.task_id || task.id,
                              url:
                                typeof sub.input_data?.url === "string"
                                  ? sub.input_data.url
                                  : "https://example.com",
                              botType: "Cloudflare Turnstile / CAPTCHA",
                            });
                          }}
                        />
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>

          {/* Footer Controls */}
          {task &&
            task.status !== "completed" &&
            task.status !== "cancelled" && (
              <div className="p-4 border-t border-white/10 bg-zinc-950/60 backdrop-blur-xl flex items-center justify-between">
                <span className="text-[11px] text-zinc-400 max-w-sm">
                  Action requires abort? Registered compensations will run in
                  reverse order.
                </span>
                <button
                  type="button"
                  onClick={handleAbort}
                  disabled={isAborting}
                  data-testid="task-abort-button"
                  className="inline-flex items-center gap-2 px-4 py-2 text-xs font-semibold text-rose-200 bg-rose-950/60 hover:bg-rose-900/80 border border-rose-500/40 rounded-xl transition-all shadow-[0_0_15px_rgba(244,63,94,0.15)] disabled:opacity-50 cursor-pointer"
                >
                  {isAborting ? (
                    <Loader2 className="w-4 h-4 animate-spin text-rose-400" />
                  ) : (
                    <Ban className="w-4 h-4 text-rose-400" />
                  )}
                  Abort Task
                </button>
              </div>
            )}
        </div>
      </div>

      {browserTakeoverSession && (
        <BrowserTakeoverModal
          isOpen={Boolean(browserTakeoverSession)}
          onClose={() => setBrowserTakeoverSession(null)}
          sessionId={browserTakeoverSession.sessionId}
          url={browserTakeoverSession.url}
          botType={browserTakeoverSession.botType}
          screenshotBase64={browserTakeoverSession.screenshotBase64}
          onResume={async (sId) => {
            await resumeBrowserSession(sId);
            setBrowserTakeoverSession(null);
          }}
        />
      )}
    </div>
  );
}

function SubtaskCard({
  subtask,
  index,
  onTakeoverClick,
}: {
  readonly subtask: SubtaskResponse;
  readonly index: number;
  readonly onTakeoverClick?: (subtask: SubtaskResponse) => void;
}) {
  const isCompleted = subtask.status === "completed";
  const isInProgress =
    subtask.status === "in_progress" || subtask.status === "executing";
  const isAwaitingApproval =
    subtask.status === "awaiting_approval" || subtask.status === "paused";
  const isFailed = subtask.status === "failed";

  return (
    <div
      data-testid={`subtask-card-${index}`}
      className={`p-4 rounded-xl border text-sm transition-all duration-200 backdrop-blur-md ${
        isInProgress
          ? "bg-cyan-950/20 border-cyan-500/40 shadow-[0_0_15px_rgba(6,182,212,0.15)] ring-1 ring-cyan-500/20"
          : isAwaitingApproval
            ? "bg-amber-950/20 border-amber-500/40 shadow-[0_0_15px_rgba(245,158,11,0.15)]"
            : isCompleted
              ? "bg-zinc-900/30 border-white/5 opacity-90"
              : isFailed
                ? "bg-rose-950/20 border-rose-500/30"
                : "bg-zinc-900/20 border-white/5 text-zinc-400"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5">
            {isInProgress && (
              <Loader2
                className="w-4 h-4 text-cyan-400 animate-spin"
                data-testid="status-icon-in-progress"
              />
            )}
            {isCompleted && (
              <CheckCircle2
                className="w-4 h-4 text-emerald-400"
                data-testid="status-icon-completed"
              />
            )}
            {isAwaitingApproval && (
              <ShieldAlert
                className="w-4 h-4 text-amber-400"
                data-testid="status-icon-awaiting-approval"
              />
            )}
            {isFailed && (
              <AlertCircle
                className="w-4 h-4 text-rose-400"
                data-testid="status-icon-failed"
              />
            )}
            {!isInProgress &&
              !isCompleted &&
              !isAwaitingApproval &&
              !isFailed && (
                <Clock
                  className="w-4 h-4 text-zinc-500"
                  data-testid="status-icon-pending"
                />
              )}
          </div>
          <div>
            <p className="text-zinc-200 font-semibold leading-snug">
              {subtask.description}
            </p>
            {subtask.dependencies.length > 0 && (
              <p className="text-xs text-zinc-500 mt-1 font-mono">
                Depends on: {subtask.dependencies.join(", ")}
              </p>
            )}
          </div>
        </div>
        <span
          className="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold uppercase bg-zinc-800/80 text-zinc-300 border border-white/10 shrink-0"
          data-testid="agent-pill"
        >
          {subtask.assigned_agent}
        </span>
      </div>

      {subtask.error_message && (
        <div className="mt-2.5 text-xs text-rose-300 bg-rose-950/50 p-2.5 rounded-lg border border-rose-500/30 font-mono">
          {subtask.error_message}
        </div>
      )}

      {subtask.output_result !== undefined &&
        subtask.output_result !== null && (
          <div className="mt-2.5 text-xs bg-zinc-950/70 p-2.5 rounded-lg border border-white/5 text-zinc-300 font-mono overflow-x-auto shadow-inner">
            {typeof subtask.output_result === "object"
              ? JSON.stringify(subtask.output_result, null, 2)
              : String(subtask.output_result)}
          </div>
        )}

      {subtask.verification_proof !== undefined &&
        subtask.verification_proof !== null && (
          <div className="mt-2.5 text-xs bg-emerald-950/20 p-2.5 rounded-lg border border-emerald-500/30 text-emerald-300 font-mono overflow-x-auto shadow-inner">
            <span className="font-bold text-emerald-400">
              Verification Proof:{" "}
            </span>
            {typeof subtask.verification_proof === "object"
              ? JSON.stringify(subtask.verification_proof, null, 2)
              : String(subtask.verification_proof)}
          </div>
        )}

      {subtask.assigned_agent === "browser" && (
        <div className="mt-3 pt-2 border-t border-white/5 flex items-center justify-end">
          <button
            type="button"
            onClick={() => onTakeoverClick?.(subtask)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40 transition-colors cursor-pointer"
          >
            <ShieldAlert className="w-3.5 h-3.5" />
            <span>Human Takeover (Anti-Bot Challenge)</span>
          </button>
        </div>
      )}
    </div>
  );
}
