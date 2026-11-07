"use client";

import {
  AlertCircle,
  Ban,
  Bot,
  CheckCircle2,
  Clock,
  DollarSign,
  Layers,
  ListTodo,
  Loader2,
  PlusCircle,
  Radio,
  RefreshCw,
  RotateCw,
  Search,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import React, { useCallback, useEffect, useState } from "react";

import {
  abortTask,
  getTask,
  listTasks,
  type TaskAbortResponse,
  type TaskDetailResponse,
  type TaskListItem,
} from "../../lib/tasks-client";
import {
  useTaskEvents,
  type TaskEventPayload,
} from "../../hooks/useTaskEvents";
import { DagVisualizer } from "../dag/DagVisualizer";
import { NewTaskModal } from "./NewTaskModal";

export interface TaskCenterViewProps {
  readonly initialTaskId?: string | null;
}

export function TaskCenterView({
  initialTaskId,
}: TaskCenterViewProps): React.JSX.Element {
  const [tasks, setTasks] = useState<readonly TaskListItem[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(
    initialTaskId ?? null,
  );
  const [selectedTaskDetail, setSelectedTaskDetail] =
    useState<TaskDetailResponse | null>(null);

  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [detailLoading, setDetailLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [isAborting, setIsAborting] = useState<boolean>(false);
  const [abortResult, setAbortResult] = useState<TaskAbortResponse | null>(
    null,
  );
  const [isNewTaskOpen, setIsNewTaskOpen] = useState<boolean>(false);

  // Load task list
  const loadTasks = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const statusParam = filterStatus === "all" ? undefined : filterStatus;
      const res = await listTasks(statusParam, 50, 0);
      setTasks(res.items);
      if (res.items.length > 0 && !selectedTaskId) {
        setSelectedTaskId(res.items[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load tasks");
    } finally {
      setLoading(false);
    }
  }, [filterStatus, selectedTaskId]);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  // Load selected task details
  const loadTaskDetail = useCallback(async (taskId: string) => {
    setDetailLoading(true);
    setAbortResult(null);
    try {
      const detail = await getTask(taskId);
      setSelectedTaskDetail(detail);
    } catch {
      // Failed to load details
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedTaskId) {
      loadTaskDetail(selectedTaskId);
    }
  }, [selectedTaskId, loadTaskDetail]);

  // Live SSE listener for real-time task progress
  const handleLiveEvent = useCallback((event: TaskEventPayload) => {
    if (!event.payload) return;
    const payload = event.payload;
    const subtaskId = payload.subtask_id as string | undefined;
    const newStatus = payload.status as string | undefined;

    setSelectedTaskDetail((prev) => {
      if (!prev) return prev;
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
    taskId: selectedTaskId,
    enabled: Boolean(selectedTaskId),
    onEvent: handleLiveEvent,
  });

  const handleAbort = async () => {
    if (!selectedTaskId) return;
    setIsAborting(true);
    try {
      const res = await abortTask(selectedTaskId);
      await loadTaskDetail(selectedTaskId);
      setAbortResult(res);
      await loadTasks();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to abort task");
    } finally {
      setIsAborting(false);
    }
  };

  const filteredTasks = tasks.filter((t) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      (t.title && t.title.toLowerCase().includes(q)) ||
      t.instruction.toLowerCase().includes(q) ||
      t.id.toLowerCase().includes(q)
    );
  });

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
            <span>Executing</span>
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
      case "cancelled":
        return (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-0.5 rounded-full bg-zinc-800/80 border border-zinc-700/60 text-zinc-400 shadow-sm">
            <Ban className="w-3 h-3 text-zinc-500" />
            <span>Cancelled</span>
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
      case "planning":
      default:
        return (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-0.5 rounded-full bg-zinc-800/80 border border-zinc-700/60 text-zinc-400 shadow-sm">
            <Clock className="w-3 h-3 text-zinc-500" />
            <span>Planning</span>
          </span>
        );
    }
  };

  return (
    <div className="flex h-full w-full flex-col lg:flex-row overflow-hidden bg-zinc-950 text-zinc-100 min-h-0">
      {/* Left Column: Task Master List (Glass Surface) */}
      <div className="w-full lg:w-96 flex flex-col h-full min-h-0 overflow-hidden border-r border-white/10 bg-zinc-900/30 backdrop-blur-xl shrink-0">
        {/* Search & Actions Bar */}
        <div className="p-4 border-b border-white/5 flex flex-col gap-3 bg-zinc-950/40">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-zinc-100 flex items-center gap-2 tracking-tight">
              <div className="p-1 rounded-md bg-teal-500/10 border border-teal-500/20 text-teal-400">
                <ListTodo className="w-3.5 h-3.5" />
              </div>
              <span>Task Ledger</span>
            </h2>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={loadTasks}
                className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 transition-colors"
                title="Refresh tasks"
              >
                <RefreshCw
                  className={`w-3.5 h-3.5 ${loading ? "animate-spin text-teal-400" : ""}`}
                />
              </button>
              <button
                type="button"
                onClick={() => setIsNewTaskOpen(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-gradient-to-r from-teal-500 to-emerald-500 hover:from-teal-400 hover:to-emerald-400 text-zinc-950 text-xs font-bold shadow-[0_0_15px_rgba(20,184,166,0.3)] transition-all cursor-pointer"
              >
                <PlusCircle className="w-3.5 h-3.5" />
                <span>New Task</span>
              </button>
            </div>
          </div>

          {/* Search box with glass styling */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-3 top-2.5 text-zinc-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search tasks, IDs, instructions…"
              className="w-full pl-9 pr-3 py-2 rounded-xl border border-white/10 bg-zinc-950/60 text-xs text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:border-teal-400/60 focus:ring-1 focus:ring-teal-400/20 transition-all font-medium"
            />
          </div>

          {/* Filter Pills with neon highlights */}
          <div className="flex items-center gap-1 overflow-x-auto text-[11px] pt-0.5">
            {[
              { id: "all", label: "All" },
              { id: "executing", label: "Running" },
              { id: "awaiting_approval", label: "Approval" },
              { id: "completed", label: "Done" },
              { id: "failed", label: "Failed" },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setFilterStatus(tab.id)}
                className={`px-2.5 py-1 rounded-lg whitespace-nowrap transition-all font-medium ${
                  filterStatus === tab.id
                    ? "bg-teal-500/20 text-teal-300 border border-teal-500/30 shadow-[0_0_10px_rgba(20,184,166,0.2)] font-semibold"
                    : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Task List Items */}
        <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2">
          {loading && tasks.length === 0 ? (
            <div className="p-10 text-center text-zinc-500 text-xs flex flex-col items-center justify-center gap-2">
              <Loader2 className="w-5 h-5 animate-spin text-teal-400" />
              <span>Loading ledger tasks…</span>
            </div>
          ) : filteredTasks.length === 0 ? (
            <div className="p-10 text-center text-zinc-500 text-xs">
              No tasks found.
            </div>
          ) : (
            filteredTasks.map((t) => {
              const isSelected = t.id === selectedTaskId;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setSelectedTaskId(t.id)}
                  className={`w-full text-left p-3.5 rounded-xl border transition-all duration-200 cursor-pointer relative overflow-hidden ${
                    isSelected
                      ? "border-teal-400/60 bg-gradient-to-r from-teal-950/40 via-zinc-900/80 to-zinc-900/80 shadow-[0_0_20px_rgba(20,184,166,0.15)] ring-1 ring-teal-400/30"
                      : "border-white/5 bg-zinc-900/30 hover:bg-zinc-900/60 hover:border-white/10"
                  }`}
                >
                  {/* Subtle top edge glow */}
                  {isSelected && (
                    <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-teal-400/60 to-transparent" />
                  )}

                  <div className="flex items-start justify-between gap-2">
                    <span className="text-xs font-bold text-zinc-200 line-clamp-1 tracking-tight">
                      {t.title || "Autonomous Task"}
                    </span>
                    {getStatusBadge(t.status)}
                  </div>
                  <p className="text-xs text-zinc-400 line-clamp-2 mt-1.5 leading-relaxed font-mono">
                    {t.instruction}
                  </p>
                  <div className="flex items-center justify-between text-[11px] font-mono text-zinc-500 mt-2.5 pt-2 border-t border-white/5">
                    <span className="flex items-center gap-1 text-teal-300 font-semibold">
                      <DollarSign className="w-3 h-3 text-teal-400" />
                      <span>{Number(t.spent_usd).toFixed(2)} USD</span>
                    </span>
                    <span>{new Date(t.created_at).toLocaleTimeString()}</span>
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>

      {/* Right Column: Detailed Task View & Interactive DAG */}
      <div className="flex-1 flex flex-col h-full min-h-0 overflow-y-auto bg-zinc-950 p-6 overscroll-contain">
        {detailLoading ? (
          <div className="flex-1 flex flex-col items-center justify-center text-zinc-500 text-xs gap-3">
            <Loader2 className="w-6 h-6 animate-spin text-teal-400" />
            <span className="font-medium">Loading task execution details…</span>
          </div>
        ) : selectedTaskDetail ? (
          <div className="max-w-5xl flex flex-col gap-6 mx-auto w-full">
            {error && (
              <div className="p-4 rounded-xl border border-rose-500/40 bg-rose-950/50 text-rose-200 text-xs flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0 text-rose-400" />
                <span>{error}</span>
              </div>
            )}

            {/* Task Header & Actions (Glass Surface) */}
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-6 rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-xl shadow-xl">
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-3">
                  <h2 className="text-lg font-bold text-zinc-100 tracking-tight">
                    {selectedTaskDetail.title || "Autonomous Task"}
                  </h2>
                  {getStatusBadge(selectedTaskDetail.status)}
                  {isConnected && (
                    <span
                      title="Live SSE connected"
                      className="inline-flex items-center gap-1 text-[10px] font-mono text-emerald-400 bg-emerald-950/60 border border-emerald-500/30 px-2 py-0.5 rounded-full"
                    >
                      <Radio className="w-2.5 h-2.5 animate-pulse text-emerald-400" />
                      <span>Live Stream</span>
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-3 text-xs font-mono text-zinc-400">
                  <span className="px-2 py-0.5 rounded-md bg-zinc-800/80 border border-white/5">
                    ID: {selectedTaskDetail.id.slice(0, 13)}…
                  </span>
                  <span>•</span>
                  <span>
                    Created:{" "}
                    {new Date(selectedTaskDetail.created_at).toLocaleString()}
                  </span>
                  <span>•</span>
                  <span className="text-teal-300 font-semibold flex items-center gap-1">
                    <DollarSign className="w-3.5 h-3.5 text-teal-400" />
                    Budget: $
                    {Number(selectedTaskDetail.budget_usd || 0).toFixed(2)} USD
                  </span>
                  <span>•</span>
                  <span className="text-zinc-300 font-semibold">
                    Spent: $
                    {Number(selectedTaskDetail.spent_usd || 0).toFixed(4)} USD
                  </span>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center gap-2.5">
                {selectedTaskDetail.status === "awaiting_approval" && (
                  <Link
                    href={`/approvals?task_id=${selectedTaskDetail.id}`}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-gradient-to-r from-amber-500 to-amber-600 hover:from-amber-400 hover:to-amber-500 text-zinc-950 font-bold text-xs transition-all shadow-[0_0_15px_rgba(245,158,11,0.3)] cursor-pointer"
                  >
                    <ShieldAlert className="w-4 h-4" />
                    <span>Review Approval</span>
                  </Link>
                )}

                {selectedTaskDetail.status !== "completed" &&
                  selectedTaskDetail.status !== "cancelled" &&
                  selectedTaskDetail.status !== "failed" && (
                    <button
                      type="button"
                      onClick={handleAbort}
                      disabled={isAborting}
                      className="flex items-center gap-1.5 px-4 py-2 rounded-xl border border-rose-500/40 bg-rose-950/40 hover:bg-rose-900/60 text-rose-200 font-semibold text-xs transition-all shadow-[0_0_12px_rgba(244,63,94,0.15)] disabled:opacity-50 cursor-pointer"
                    >
                      {isAborting ? (
                        <Loader2 className="w-4 h-4 animate-spin text-rose-400" />
                      ) : (
                        <Ban className="w-4 h-4 text-rose-400" />
                      )}
                      <span>Abort Task & Sagas</span>
                    </button>
                  )}
              </div>
            </div>

            {/* Saga Compensation Report Alert if aborted */}
            {abortResult && (
              <div className="p-5 rounded-2xl border border-rose-500/40 bg-rose-950/40 backdrop-blur-xl text-rose-100 flex flex-col gap-2.5 shadow-xl">
                <div className="flex items-center gap-2 font-bold text-sm text-rose-200">
                  <AlertCircle className="w-4 h-4 text-rose-400" />
                  <span>Saga Compensation Rollback Executed</span>
                </div>
                <div className="text-xs font-mono grid grid-cols-1 sm:grid-cols-3 gap-3 p-3 rounded-xl bg-zinc-950/60 border border-white/5">
                  <div>
                    <span className="text-zinc-500 block">Triggered:</span>
                    <span className="text-rose-300 font-bold">
                      {abortResult.compensations_triggered} compensations
                    </span>
                  </div>
                  <div>
                    <span className="text-zinc-500 block">
                      Executed Rollbacks:
                    </span>
                    <span className="text-emerald-300 font-bold">
                      {abortResult.executed} successful
                    </span>
                  </div>
                  <div>
                    <span className="text-zinc-500 block">Failed:</span>
                    <span className="text-rose-300 font-bold">
                      {abortResult.failed} failed
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Original Instruction Glass Card */}
            <div className="p-5 rounded-2xl border border-white/10 bg-zinc-900/30 backdrop-blur-xl flex flex-col gap-2 shadow-lg">
              <span className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5 text-teal-400" />
                <span>Original Natural Language Instruction</span>
              </span>
              <p className="text-xs text-zinc-200 leading-relaxed font-mono whitespace-pre-wrap">
                {selectedTaskDetail.instruction}
              </p>
            </div>

            {/* Interactive DAG Visualizer */}
            <div className="flex flex-col gap-3">
              <h3 className="text-sm font-bold text-zinc-200 flex items-center gap-2 tracking-tight">
                <Layers className="w-4 h-4 text-teal-400" />
                <span>Orchestrator LangGraph DAG Plan</span>
              </h3>
              <DagVisualizer subtasks={selectedTaskDetail.subtasks} />
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-zinc-500 text-center">
            <div className="p-4 rounded-2xl bg-zinc-900/40 border border-white/5 shadow-inner mb-3">
              <Bot className="w-12 h-12 text-teal-400 opacity-60" />
            </div>
            <p className="text-base font-bold text-zinc-200">
              Select a Task from the Ledger
            </p>
            <p className="text-xs max-w-sm mt-1.5 text-zinc-400 leading-relaxed">
              Select an execution task on the left or create a new task to view
              the real-time DAG graph and subtask verification proofs.
            </p>
          </div>
        )}
      </div>

      <NewTaskModal
        isOpen={isNewTaskOpen}
        onClose={() => setIsNewTaskOpen(false)}
        onTaskCreated={(task) => {
          loadTasks();
          setSelectedTaskId(task.task_id);
        }}
      />
    </div>
  );
}
