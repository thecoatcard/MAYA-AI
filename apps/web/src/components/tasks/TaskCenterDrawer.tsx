"use client";

import {
  AlertCircle,
  AlertTriangle,
  Ban,
  CheckCircle2,
  Clock,
  Loader2,
  ShieldAlert,
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

  // Live SSE stream handler
  const handleLiveEvent = useCallback((event: TaskEventPayload) => {
    if (!event.payload) return;

    const payload = event.payload;
    const subtaskId = payload.subtask_id as string | undefined;
    const newStatus = payload.status as string | undefined;

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
  }, []);

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
      className="fixed inset-0 z-50 overflow-hidden bg-black/50 backdrop-blur-sm transition-opacity"
      data-testid="task-center-drawer-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="fixed inset-y-0 right-0 flex max-w-full pl-10">
        <div
          className="w-screen max-w-xl bg-zinc-950 text-zinc-100 shadow-2xl border-l border-zinc-800 flex flex-col"
          data-testid="task-center-drawer-panel"
        >
          {/* Header */}
          <div className="p-6 border-b border-zinc-800 flex items-center justify-between">
            <div>
              <div className="flex items-center gap-3">
                <h2
                  id="task-center-title"
                  className="text-lg font-semibold tracking-tight text-zinc-100"
                >
                  {task?.title || "Task Center"}
                </h2>
                {task && (
                  <span
                    data-testid="task-status-badge"
                    className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium uppercase tracking-wider ${
                      task.status === "completed"
                        ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                        : task.status === "awaiting_approval"
                          ? "bg-amber-950 text-amber-300 border border-amber-800"
                          : task.status === "cancelled" ||
                              task.status === "failed"
                            ? "bg-rose-950 text-rose-400 border border-rose-800"
                            : "bg-sky-950 text-sky-400 border border-sky-800"
                    }`}
                  >
                    {task.status}
                  </span>
                )}
                {isConnected && (
                  <span
                    title="Live SSE connected"
                    data-testid="sse-live-indicator"
                    className="inline-block w-2 h-2 rounded-full bg-emerald-500 animate-pulse"
                  />
                )}
              </div>
              <p
                className="text-sm text-zinc-400 mt-1 font-mono"
                data-testid="task-progress-summary"
              >
                {progressSummary}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              data-testid="drawer-close-button"
              className="p-2 text-zinc-400 hover:text-zinc-100 hover:bg-zinc-900 rounded-lg transition-colors"
              aria-label="Close task center"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Abort Notification Banner */}
          {abortResult && (
            <div
              data-testid="abort-result-banner"
              className="p-4 bg-amber-950/70 border-b border-amber-900 text-amber-200 text-sm flex items-start gap-3"
            >
              <AlertTriangle className="w-5 h-5 shrink-0 text-amber-400" />
              <div>
                <p className="font-medium">Task cancelled</p>
                <p className="text-xs text-amber-300/80 mt-0.5">
                  Triggered {abortResult.compensations_triggered} compensation
                  actions ({abortResult.executed} executed, {abortResult.failed}{" "}
                  failed).
                </p>
                {abortResult.notifications.length > 0 && (
                  <ul className="mt-2 text-xs list-disc list-inside space-y-1">
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
              className="p-4 bg-rose-950/70 border-b border-rose-900 text-rose-200 text-sm flex items-center gap-3"
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
                className="flex flex-col items-center justify-center py-20 text-zinc-500 gap-3"
              >
                <Loader2 className="w-8 h-8 animate-spin text-zinc-400" />
                <p className="text-sm">Loading task execution graph...</p>
              </div>
            ) : !task ? (
              <div
                data-testid="task-center-empty"
                className="text-center py-20 text-zinc-500"
              >
                <p className="text-sm">No task selected.</p>
              </div>
            ) : (
              <>
                {/* Instruction Card */}
                <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg p-4">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-1">
                    Instruction
                  </h3>
                  <p className="text-sm text-zinc-200">{task.instruction}</p>
                  <div className="flex items-center gap-4 mt-3 text-xs text-zinc-400">
                    <span>
                      Budget:{" "}
                      <strong className="text-zinc-200">
                        ${Number(task.budget_usd).toFixed(2)}
                      </strong>
                    </span>
                    <span>
                      Spent:{" "}
                      <strong className="text-zinc-200">
                        ${Number(task.spent_usd).toFixed(4)}
                      </strong>
                    </span>
                    <span>
                      Subtasks:{" "}
                      <strong className="text-zinc-200">
                        {task.subtasks.length}
                      </strong>
                    </span>
                  </div>
                </div>

                {/* Subtask Execution Graph */}
                <div className="space-y-3">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
                    Subtask DAG Execution
                  </h3>
                  {task.subtasks.length === 0 ? (
                    <p className="text-sm text-zinc-500 italic">
                      No subtasks defined yet.
                    </p>
                  ) : (
                    <div className="space-y-3" data-testid="subtasks-list">
                      {task.subtasks.map((st, idx) => (
                        <SubtaskCard
                          key={st.id || idx}
                          subtask={st}
                          index={idx}
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
              <div className="p-4 border-t border-zinc-800 bg-zinc-900/60 flex items-center justify-between">
                <span className="text-xs text-zinc-400">
                  Action requires abort? Registered compensations will run in
                  reverse order.
                </span>
                <button
                  type="button"
                  onClick={handleAbort}
                  disabled={isAborting}
                  data-testid="task-abort-button"
                  className="inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-rose-300 bg-rose-950/80 hover:bg-rose-900/80 border border-rose-800 rounded-md transition-colors disabled:opacity-50"
                >
                  {isAborting ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Ban className="w-4 h-4" />
                  )}
                  Abort Task
                </button>
              </div>
            )}
        </div>
      </div>
    </div>
  );
}

function SubtaskCard({
  subtask,
  index,
}: {
  readonly subtask: SubtaskResponse;
  readonly index: number;
}) {
  const isCompleted = subtask.status === "completed";
  const isInProgress = subtask.status === "in_progress";
  const isAwaitingApproval = subtask.status === "awaiting_approval";
  const isFailed = subtask.status === "failed";

  return (
    <div
      data-testid={`subtask-card-${index}`}
      className={`p-3.5 rounded-lg border text-sm transition-all ${
        isInProgress
          ? "bg-zinc-900/80 border-sky-800/80 ring-1 ring-sky-900"
          : isAwaitingApproval
            ? "bg-amber-950/40 border-amber-800/70"
            : isCompleted
              ? "bg-zinc-900/30 border-zinc-850 opacity-90"
              : isFailed
                ? "bg-rose-950/30 border-rose-900/60"
                : "bg-zinc-900/30 border-zinc-800 text-zinc-400"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <div className="mt-0.5">
            {isInProgress && (
              <Loader2
                className="w-4 h-4 text-sky-400 animate-spin"
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
            <p className="text-zinc-200 font-medium leading-snug">
              {subtask.description}
            </p>
            {subtask.dependencies.length > 0 && (
              <p className="text-xs text-zinc-500 mt-0.5 font-mono">
                Depends on: {subtask.dependencies.join(", ")}
              </p>
            )}
          </div>
        </div>
        <span
          className="px-2 py-0.5 rounded text-[10px] font-mono uppercase bg-zinc-800 text-zinc-300 border border-zinc-700/60 shrink-0"
          data-testid="agent-pill"
        >
          {subtask.assigned_agent}
        </span>
      </div>

      {subtask.error_message && (
        <div className="mt-2 text-xs text-rose-400 bg-rose-950/50 p-2 rounded border border-rose-900/60">
          {subtask.error_message}
        </div>
      )}

      {subtask.output_result !== undefined &&
        subtask.output_result !== null && (
          <div className="mt-2 text-xs bg-zinc-950/60 p-2 rounded border border-zinc-800 text-zinc-300 font-mono overflow-x-auto">
            {typeof subtask.output_result === "object"
              ? JSON.stringify(subtask.output_result, null, 2)
              : String(subtask.output_result)}
          </div>
        )}
    </div>
  );
}
