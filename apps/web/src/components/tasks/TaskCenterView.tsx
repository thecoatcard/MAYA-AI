"use client";

import {
  ArrowLeft,
  Check,
  Copy,
  ClipboardCopy,
  FolderOpen,
  Hash,
  MessagesSquare,
  MoreHorizontal,
  OctagonX,
  RotateCcw,
  FileCheck,
  ListTodo,
  PlusCircle,
  Radio,
  RefreshCw,
  Search,
  Trash2,
  XCircle,
} from "lucide-react";
import { useOptionalRouter } from "../../hooks/useOptionalRouter";
import React, {
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { ConversationContext } from "../chat/ConversationContext";

import {
  abortTask,
  deleteTask,
  deleteTasks,
  getTask,
  listTasks,
  submitTask,
  type TaskAbortResponse,
  type TaskDetailResponse,
  type TaskListItem,
} from "../../lib/tasks-client";
import {
  useTaskEvents,
  type TaskEventPayload,
} from "../../hooks/useTaskEvents";
import { TaskApprovals } from "../approvals/TaskApprovals";
import { pendingTaskInput, type PendingTaskInput } from "../../lib/task-input";
import { TaskInputCard } from "./TaskInputCard";
import { useOptionalWorkspace } from "../layout/WorkspaceProvider";
import { DagVisualizer } from "../dag/DagVisualizer";
import {
  Badge,
  Button,
  ConfirmDeleteDialog,
  EmptyState,
  ErrorState,
  IconButton,
  Notice,
  Pane,
  SelectionBar,
  Skeleton,
  Truncate,
  WrapText,
} from "../ui";
import { MenuButton, useContextMenu, type ContextMenuEntry } from "../ui";
import { copyText } from "../../lib/menu-actions";
import { FailureReason } from "./FailureReason";
import { TaskResultSection } from "./TaskResultSection";
import {
  CANCEL_FIRST,
  TASK_DELETE_KEEPS,
  TASK_DELETE_REMOVES,
} from "./taskDeleteCopy";
import { NewTaskModal } from "./NewTaskModal";
import { TaskStatusBadge } from "./TaskStatusBadge";
import {
  describeTask,
  failureTextOf,
  formatCost,
  isTerminalPhase,
  phaseOfStatus,
} from "./taskStatus";

export interface TaskCenterViewProps {
  readonly initialTaskId?: string | null;
  /** Called after an approval was decided on a task here (refresh the pending counters). */
  readonly onApprovalDecided?: () => void;
}

const PAGE_SIZE = 50;
/** The API accepts at most 100 rows per request. */
const MAX_REFRESH = 100;
const REFRESH_MS = 10_000;

const FILTERS = [
  { id: "all", label: "All" },
  { id: "executing", label: "Running" },
  { id: "awaiting_approval", label: "Approval" },
  { id: "completed", label: "Done" },
  { id: "failed", label: "Failed" },
] as const;

const TERMINAL_EVENTS = new Set([
  "task_completed",
  "task_failed",
  "task_cancelled",
  "task_paused_approval",
]);

function when(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message !== "" ? err.message : fallback;
}

/** Long text clamps to a few lines with a "Show all" control; nothing is lost or cut. */
function ExpandableText({
  text,
}: {
  readonly text: string;
}): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const long = text.length > 400;
  return (
    <div className="min-w-0">
      <WrapText
        as="p"
        dir="auto"
        className={`m-0 block text-maya-body ${
          long && !open ? "line-clamp-6" : ""
        }`}
      >
        {text}
      </WrapText>
      {long && (
        <Button
          size="sm"
          variant="ghost"
          className="mt-1"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {open ? "Show less" : "Show all"}
        </Button>
      )}
    </div>
  );
}

/** One row of the task list: the same menu opens on right-click, Menu key or the "..." button. */
function TaskRowShell({
  label,
  getItems,
  children,
}: {
  readonly label: string;
  readonly getItems: () => readonly ContextMenuEntry[];
  readonly children: React.ReactNode;
}): React.JSX.Element {
  const menu = useContextMenu({ label, getItems });
  return (
    <li
      className="maya-reveal flex min-w-0 shrink-0 items-stretch gap-1"
      {...menu.triggerProps}
    >
      {children}
      <MenuButton
        label={`More actions for ${label}`}
        buttonProps={menu.buttonProps}
      >
        <MoreHorizontal size={14} aria-hidden="true" />
      </MenuButton>
      {menu.menu}
    </li>
  );
}

export function TaskCenterView({
  initialTaskId,
  onApprovalDecided,
}: TaskCenterViewProps): React.JSX.Element {
  const [tasks, setTasks] = useState<readonly TaskListItem[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(
    initialTaskId ?? null,
  );
  // Narrow containers show either the list or the detail; wide ones show both.
  const [view, setView] = useState<"list" | "detail">(
    initialTaskId ? "detail" : "list",
  );
  const [selectedTaskDetail, setSelectedTaskDetail] =
    useState<TaskDetailResponse | null>(null);

  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [loadingMore, setLoadingMore] = useState<boolean>(false);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [listError, setListError] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<boolean>(false);
  const [detailLoading, setDetailLoading] = useState<boolean>(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isAborting, setIsAborting] = useState<boolean>(false);
  const [retrying, setRetrying] = useState<boolean>(false);
  const [abortResult, setAbortResult] = useState<TaskAbortResponse | null>(
    null,
  );
  const [isNewTaskOpen, setIsNewTaskOpen] = useState<boolean>(false);
  const [copiedId, setCopiedId] = useState<boolean>(false);

  const notify = useOptionalWorkspace()?.notify;
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const [confirmIds, setConfirmIds] = useState<readonly string[] | null>(null);
  const [deleting, setDeleting] = useState<boolean>(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const tasksRef = useRef<readonly TaskListItem[]>([]);
  tasksRef.current = tasks;
  const selectedRef = useRef<string | null>(selectedTaskId);
  selectedRef.current = selectedTaskId;
  const filterRef = useRef<string>(filterStatus);
  filterRef.current = filterStatus;
  const requestRef = useRef<number>(0);

  /**
   * reset: first page for the current filter (shows the loading state).
   * silent: refresh everything already loaded without a spinner or a scroll jump.
   * more: append the next page.
   */
  const loadTasks = useCallback(
    async (mode: "reset" | "silent" | "more" = "reset"): Promise<void> => {
      const request = ++requestRef.current;
      const statusParam =
        filterRef.current === "all" ? undefined : filterRef.current;
      if (mode === "reset") {
        setLoading(true);
        setListError(null);
      } else if (mode === "more") {
        setLoadingMore(true);
      } else {
        setRefreshing(true);
      }
      try {
        const loaded = tasksRef.current.length;
        const res =
          mode === "more"
            ? await listTasks(statusParam, PAGE_SIZE, loaded)
            : await listTasks(
                statusParam,
                mode === "silent"
                  ? Math.min(MAX_REFRESH, Math.max(PAGE_SIZE, loaded))
                  : PAGE_SIZE,
                0,
              );
        if (request !== requestRef.current) return;
        setTotal(res.total);
        setRefreshError(false);
        if (mode === "more") {
          const known = new Set(tasksRef.current.map((t) => t.id));
          setTasks([
            ...tasksRef.current,
            ...res.items.filter((t) => !known.has(t.id)),
          ]);
        } else if (mode === "silent" && loaded > MAX_REFRESH) {
          // Keep the rows beyond the refreshed window instead of dropping them.
          setTasks([...res.items, ...tasksRef.current.slice(res.items.length)]);
        } else {
          setTasks(res.items);
        }
        if (res.items.length > 0 && !selectedRef.current) {
          setSelectedTaskId(res.items[0].id);
        }
      } catch (err) {
        if (request !== requestRef.current) return;
        if (mode === "reset") {
          setTasks([]);
          setTotal(0);
          setListError(errorText(err, "Failed to load tasks"));
        } else {
          // Keep the list we have; say that it may be out of date.
          setRefreshError(true);
        }
      } finally {
        if (request === requestRef.current) {
          setLoading(false);
          setLoadingMore(false);
          setRefreshing(false);
        }
      }
    },
    [],
  );

  useEffect(() => {
    void loadTasks("reset");
  }, [filterStatus, loadTasks]);

  // The list refreshes on its own so statuses and new tasks appear without pressing a button.
  useEffect(() => {
    const timer = setInterval(() => {
      if (typeof document === "undefined" || !document.hidden) {
        void loadTasks("silent");
      }
    }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [loadTasks]);

  const loadTaskDetail = useCallback(
    async (taskId: string, silent: boolean = false): Promise<void> => {
      if (!silent) {
        setDetailLoading(true);
        setAbortResult(null);
        setDetailError(null);
      }
      try {
        const detail = await getTask(taskId);
        if (selectedRef.current === taskId) setSelectedTaskDetail(detail);
      } catch (err) {
        if (selectedRef.current === taskId && !silent) {
          setSelectedTaskDetail(null);
          setDetailError(errorText(err, "Failed to load the task"));
        }
      } finally {
        if (!silent && selectedRef.current === taskId) setDetailLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (selectedTaskId) {
      void loadTaskDetail(selectedTaskId);
    } else {
      setSelectedTaskDetail(null);
    }
  }, [selectedTaskId, loadTaskDetail]);

  // Live stream for the selected task: step status changes are applied at once, and the
  // events that end or pause a task refetch the real detail and the list.
  const handleLiveEvent = useCallback(
    (event: TaskEventPayload) => {
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
        } else if (event.event_type === "task_cancelled") {
          updatedStatus = "cancelled";
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

      if (TERMINAL_EVENTS.has(event.event_type)) {
        const id = selectedRef.current;
        if (id) void loadTaskDetail(id, true);
        void loadTasks("silent");
      }
    },
    [loadTaskDetail, loadTasks],
  );

  const { isConnected } = useTaskEvents({
    taskId: selectedTaskId,
    enabled: Boolean(selectedTaskId),
    onEvent: handleLiveEvent,
  });

  const handleAbort = async (): Promise<void> => {
    if (!selectedTaskId) return;
    setIsAborting(true);
    setActionError(null);
    try {
      const res = await abortTask(selectedTaskId);
      await loadTaskDetail(selectedTaskId, true);
      setAbortResult(res);
      await loadTasks("silent");
    } catch (err) {
      setActionError(errorText(err, "Failed to cancel the task"));
    } finally {
      setIsAborting(false);
    }
  };

  const toggleChecked = (id: string): void =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const askDelete = (ids: readonly string[]): void => {
    setDeleteError(null);
    setConfirmIds(ids);
  };

  const performDelete = async (): Promise<void> => {
    if (confirmIds === null) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      const gone = new Set<string>();
      if (confirmIds.length === 1) {
        await deleteTask(confirmIds[0] as string);
        gone.add(confirmIds[0] as string);
      } else {
        const result = await deleteTasks(confirmIds);
        result.deleted.forEach((id) => gone.add(id));
        result.notFound.forEach((id) => gone.add(id));
        if (result.notFinished.length > 0) {
          setDeleteError(
            `${result.notFinished.length} ${result.notFinished.length === 1 ? "task is" : "tasks are"} not finished. ${CANCEL_FIRST}.`,
          );
        }
      }
      const remaining = tasksRef.current.filter((t) => !gone.has(t.id));
      setTasks(remaining);
      setTotal((n) => Math.max(0, n - gone.size));
      setChecked((prev) => new Set([...prev].filter((id) => !gone.has(id))));
      if (selectedRef.current !== null && gone.has(selectedRef.current)) {
        setSelectedTaskId(null);
        setSelectedTaskDetail(null);
        setView("list");
      }
      if (gone.size > 0) {
        notify?.({
          tone: "success",
          message:
            gone.size === 1 ? "Task deleted." : `${gone.size} tasks deleted.`,
        });
      }
      if (gone.size === confirmIds.length) setConfirmIds(null);
      else setConfirmIds(confirmIds.filter((id) => !gone.has(id)));
    } catch (err) {
      setDeleteError(
        errorText(err, "The task could not be deleted. Try again."),
      );
    } finally {
      setDeleting(false);
    }
  };

  const copyId = (id: string): void => {
    try {
      void navigator.clipboard?.writeText(id).then(() => {
        setCopiedId(true);
        setTimeout(() => setCopiedId(false), 2000);
      });
    } catch {
      // Clipboard unavailable: the id stays selectable.
    }
  };

  const selectTask = (id: string): void => {
    setSelectedTaskId(id);
    setView("detail");
  };

  const workspaceRouter = useOptionalRouter();
  const runtimeCtx = useContext(ConversationContext);
  const retryTasks = async (list: readonly TaskListItem[]): Promise<void> => {
    if (retrying) return;
    setRetrying(true);
    try {
      for (const t of list) {
        try {
          const idempotencyKey = `task-${Date.now()}-${Math.random().toString(36).substring(2, 10)}`;
          const sid = (t as { session_id?: string }).session_id;
          await submitTask({
            instruction: t.instruction,
            title: t.title,
            idempotency_key: idempotencyKey,
            ...(sid ? { session_id: sid } : {}),
          });
        } catch {
          setActionError("A task could not be started again. Try again.");
        }
      }
      await loadTasks("silent");
    } finally {
      setRetrying(false);
    }
  };
  const rowItems = (
    t: TaskListItem,
    title: string,
    finished: boolean,
  ): ContextMenuEntry[] => {
    const failed = t.status.toLowerCase() === "failed";
    const inSelection = checked.has(t.id) && checked.size > 1;
    const items: ContextMenuEntry[] = [
      {
        id: "open",
        label: "Open",
        icon: <FolderOpen size={14} />,
        onSelect: () => selectTask(t.id),
      },
    ];
    if (!finished) {
      items.push({
        id: "cancel",
        label: "Cancel",
        icon: <OctagonX size={14} />,
        onSelect: () =>
          void abortTask(t.id)
            .then(() => loadTasks("silent"))
            .catch(() => setActionError("Failed to cancel the task")),
      });
    }
    if (failed) {
      items.push({
        id: "retry",
        label: "Retry",
        icon: <RotateCcw size={14} />,
        disabled: retrying,
        disabledReason: retrying ? "Retrying task..." : undefined,
        onSelect: () => void retryTasks([t]),
      });
    }
    items.push(
      { kind: "separator" },
      {
        id: "copy-id",
        label: "Copy task ID",
        icon: <Hash size={14} />,
        onSelect: () => void copyText(t.id),
      },
      {
        id: "copy-details",
        label: "Copy details",
        icon: <ClipboardCopy size={14} />,
        onSelect: () =>
          void copyText(
            `Task: ${title}\nStatus: ${t.status}\nRequest: ${t.instruction}`,
          ),
      },
      {
        id: "artifacts",
        label: "Open in Artifacts",
        icon: <FileCheck size={14} />,
        onSelect: () => workspaceRouter.push("/artifacts"),
      },
    );
    const sid = (t as { session_id?: string }).session_id;
    if (sid !== undefined && runtimeCtx !== null) {
      items.push({
        id: "conversation",
        label: "Show in conversation",
        icon: <MessagesSquare size={14} />,
        onSelect: () => {
          void runtimeCtx.openConversation(sid);
          workspaceRouter.push("/");
        },
      });
    }
    if (inSelection) {
      const failedSelected = tasks.filter(
        (x) => checked.has(x.id) && x.status.toLowerCase() === "failed",
      );
      items.push(
        { kind: "separator" },
        {
          id: "retry-failed",
          label: "Retry failed",
          icon: <RotateCcw size={14} />,
          disabled: retrying || failedSelected.length === 0,
          disabledReason: retrying
            ? "Retrying tasks..."
            : failedSelected.length === 0
              ? "None of the selected tasks failed."
              : undefined,
          onSelect: () => void retryTasks(failedSelected),
        },
        {
          id: "delete-selected",
          label: `Delete ${checked.size} selected`,
          icon: <Trash2 size={14} />,
          danger: true,
          onSelect: () => askDelete([...checked]),
        },
      );
    } else {
      items.push(
        { kind: "separator" },
        {
          id: "delete",
          label: "Delete",
          icon: <Trash2 size={14} />,
          danger: true,
          disabled: !finished,
          disabledReason: CANCEL_FIRST,
          onSelect: () => askDelete([t.id]),
        },
      );
    }
    return items;
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

  const detailSummary = describeTask(selectedTaskDetail);
  const showWide = "@[69rem]:block";

  /* ------------------------------------------------------------------ list */
  const listHeader = (
    <div className="flex min-w-0 flex-col gap-3 py-1">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <h2 className="m-0 flex min-w-0 items-center gap-2 text-maya-heading font-medium">
          <ListTodo
            aria-hidden="true"
            size={20}
            className="shrink-0 text-maya-muted"
          />
          <span className="min-w-0">Tasks</span>
        </h2>
        <div className="flex shrink-0 items-center gap-1">
          <IconButton
            size="sm"
            label={refreshing || loading ? "Refreshing tasks" : "Refresh tasks"}
            icon={<RefreshCw size={14} />}
            onClick={() => void loadTasks("reset")}
          />
          <Button
            size="sm"
            variant="primary"
            leftIcon={<PlusCircle size={14} />}
            onClick={() => setIsNewTaskOpen(true)}
          >
            New Task
          </Button>
        </div>
      </div>
      <div className="relative min-w-0">
        <Search
          aria-hidden="true"
          size={14}
          className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-maya-muted"
        />
        <input
          type="search"
          aria-label="Search loaded tasks"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search tasks, IDs, instructions"
          dir="auto"
          className="w-full min-w-0 rounded-maya-md border border-maya-control-border bg-maya-pane py-2 pr-3 pl-9 text-maya-small text-maya-text placeholder:text-maya-muted hover:border-maya-border-strong focus:border-maya-focus"
        />
      </div>
      <div
        role="group"
        aria-label="Filter by status"
        className="flex min-w-0 flex-wrap gap-1"
      >
        {FILTERS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            aria-pressed={filterStatus === tab.id}
            onClick={() => setFilterStatus(tab.id)}
            className={`min-h-8 border px-3 text-maya-caption pointer-coarse:min-h-11 ${
              filterStatus === tab.id
                ? "border-maya-accent bg-maya-accent-soft font-medium text-maya-text"
                : "border-maya-border text-maya-secondary hover:bg-maya-hover"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
    </div>
  );

  const listFooter = (
    <div className="flex min-w-0 flex-col gap-2 text-maya-caption text-maya-muted">
      <SelectionBar
        count={checked.size}
        noun="tasks"
        onClear={() => setChecked(new Set())}
        onDelete={() => askDelete([...checked])}
      />
      {refreshError && tasks.length > 0 && (
        <p role="status" className="m-0 text-maya-warning">
          Could not refresh. This list may be out of date.
        </p>
      )}
      {listError === null && !loading && (
        <p className="m-0" data-testid="task-count">
          {total > tasks.length
            ? `Showing ${tasks.length} of ${total} tasks`
            : `${total} ${total === 1 ? "task" : "tasks"}`}
          {searchQuery !== "" && " (search covers loaded tasks only)"}
        </p>
      )}
      {total > tasks.length && listError === null && (
        <Button
          size="sm"
          loading={loadingMore}
          onClick={() => void loadTasks("more")}
        >
          Load more
        </Button>
      )}
    </div>
  );

  let listBody: React.ReactNode;
  if (loading && tasks.length === 0) {
    listBody = (
      <div className="grid gap-2 p-2" aria-busy="true">
        <p className="m-0 px-1 text-maya-small text-maya-muted">
          Loading tasks
        </p>
        {[0, 1, 2].map((n) => (
          <Skeleton
            key={n}
            lines={3}
            className="rounded-maya-lg border border-maya-border p-3"
          />
        ))}
      </div>
    );
  } else if (listError !== null) {
    listBody = (
      <div className="p-2">
        <ErrorState
          title="Tasks could not be loaded"
          message={listError}
          onRetry={() => void loadTasks("reset")}
        />
      </div>
    );
  } else if (filteredTasks.length === 0) {
    listBody = (
      <div className="p-2">
        <EmptyState
          title={
            searchQuery !== "" ? "No loaded task matches" : "No tasks found."
          }
          description={
            searchQuery !== ""
              ? "Try a different search, or load more tasks."
              : "Tasks you start appear here."
          }
          action={
            <Button size="sm" onClick={() => setIsNewTaskOpen(true)}>
              New Task
            </Button>
          }
        />
      </div>
    );
  } else {
    listBody = (
      <ul className="m-0 flex list-none flex-col gap-2 p-2">
        {filteredTasks.map((t) => {
          const isSelected = t.id === selectedTaskId;
          const failed = t.status.toLowerCase() === "failed";
          const title = t.title || "Untitled task";
          const finished = isTerminalPhase(phaseOfStatus(t.status));
          return (
            <TaskRowShell
              key={t.id}
              label={title}
              getItems={() => rowItems(t, title, finished)}
            >
              <input
                type="checkbox"
                className="mt-4 size-4 shrink-0"
                aria-label={`Select ${title}`}
                checked={checked.has(t.id)}
                disabled={!finished}
                title={finished ? undefined : CANCEL_FIRST}
                onChange={() => toggleChecked(t.id)}
              />
              <button
                type="button"
                aria-pressed={isSelected}
                onClick={() => selectTask(t.id)}
                className={`flex min-w-0 flex-1 flex-col gap-1.5 border p-3 text-left motion-interactive ${
                  isSelected
                    ? "border-maya-accent bg-maya-accent-soft"
                    : "border-maya-border bg-maya-raised hover:bg-maya-hover"
                } ${failed ? "border-l-4 border-l-maya-danger" : ""}`}
              >
                <span className="flex min-w-0 flex-wrap items-start justify-between gap-x-2 gap-y-1">
                  <Truncate
                    lines={2}
                    title={title}
                    dir="auto"
                    className="min-w-0 flex-1 basis-32 text-maya-small font-medium text-maya-text"
                  >
                    {title}
                  </Truncate>
                  <TaskStatusBadge
                    status={t.status}
                    partial={t.partial === true}
                  />
                </span>
                <Truncate
                  lines={2}
                  title={t.instruction}
                  dir="auto"
                  className="text-maya-caption text-maya-secondary"
                >
                  {t.instruction}
                </Truncate>
                <span className="flex min-w-0 flex-wrap items-center justify-between gap-x-2 text-maya-caption text-maya-muted">
                  <span>{when(t.created_at)}</span>
                  <span className="maya-tabular">
                    {formatCost(t.spent_usd)}
                  </span>
                </span>
              </button>
              {finished && (
                <IconButton
                  size="sm"
                  label={`Delete ${title}`}
                  icon={<Trash2 size={14} />}
                  onClick={() => askDelete([t.id])}
                />
              )}
            </TaskRowShell>
          );
        })}
      </ul>
    );
  }

  /* ---------------------------------------------------------------- detail */
  let detailBody: React.ReactNode;
  if (detailLoading) {
    detailBody = (
      <div className="grid gap-3 p-4" aria-busy="true">
        <p className="m-0 text-maya-small text-maya-muted">
          Loading task details
        </p>
        <Skeleton lines={4} />
      </div>
    );
  } else if (detailError !== null) {
    detailBody = (
      <div className="p-4">
        <ErrorState
          title="The task could not be loaded"
          message={detailError}
          onRetry={() => selectedTaskId && void loadTaskDetail(selectedTaskId)}
        />
      </div>
    );
  } else if (selectedTaskDetail) {
    const d = selectedTaskDetail;
    const terminal = isTerminalPhase(detailSummary.phase);
    detailBody = (
      <div className="mx-auto flex w-full max-w-4xl min-w-0 flex-col gap-4 p-4">
        {actionError && (
          <Notice tone="danger" onDismiss={() => setActionError(null)}>
            {actionError}
          </Notice>
        )}

        <section
          aria-label="Task summary"
          className="flex min-w-0 flex-col gap-3 rounded-maya-lg border border-maya-border bg-maya-raised p-4"
        >
          <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-3 gap-y-2">
            <h2
              dir="auto"
              className="m-0 min-w-0 flex-1 basis-60 text-maya-title font-medium [overflow-wrap:anywhere]"
            >
              {d.title || "Untitled task"}
            </h2>
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              <TaskStatusBadge
                phase={detailSummary.phase}
                label={detailSummary.label}
                partial={detailSummary.partial}
              />
              {isConnected && (
                <Badge
                  tone="success"
                  icon={Radio}
                  title="Live updates connected"
                >
                  Live
                </Badge>
              )}
            </div>
          </div>

          <dl className="m-0 grid min-w-0 grid-cols-[repeat(auto-fit,minmax(10rem,1fr))] gap-x-4 gap-y-2 text-maya-small">
            <div className="min-w-0">
              <dt className="maya-label">Created</dt>
              <dd className="m-0">{when(d.created_at)}</dd>
            </div>
            <div className="min-w-0">
              <dt className="maya-label">Budget cap</dt>
              <dd className="maya-tabular m-0">
                ${Number(d.budget_usd || 0).toFixed(2)}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="maya-label">Cost</dt>
              <dd className="maya-tabular m-0">{formatCost(d.spent_usd)}</dd>
            </div>
            <div className="min-w-0">
              <dt className="maya-label">Task id</dt>
              <dd className="m-0 flex min-w-0 items-center gap-1">
                <WrapText
                  as="code"
                  className="min-w-0 font-mono text-maya-mono"
                >
                  {d.id}
                </WrapText>
                <IconButton
                  size="sm"
                  label={copiedId ? "Task id copied" : "Copy task id"}
                  icon={copiedId ? <Check size={14} /> : <Copy size={14} />}
                  onClick={() => copyId(d.id)}
                />
              </dd>
            </div>
          </dl>

          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {!terminal && (
              <Button
                variant="danger"
                loading={isAborting}
                leftIcon={<XCircle size={16} />}
                onClick={() => void handleAbort()}
              >
                Cancel task
              </Button>
            )}
            {terminal && (
              <Button
                variant="secondary"
                leftIcon={<Trash2 size={16} />}
                onClick={() => askDelete([d.id])}
              >
                Delete task
              </Button>
            )}
          </div>
        </section>

        {detailSummary.phase === "awaiting_approval" &&
          pendingTaskInput(d) !== null && (
            <TaskInputCard
              taskId={d.id}
              pending={pendingTaskInput(d) as PendingTaskInput}
              onAnswered={() => {
                void loadTaskDetail(d.id, true);
                void loadTasks("silent");
              }}
            />
          )}

        {detailSummary.phase === "awaiting_approval" &&
          pendingTaskInput(d) === null && (
            <TaskApprovals
              taskId={d.id}
              waiting
              refreshKey={d}
              onDecided={() => {
                void loadTaskDetail(d.id, true);
                void loadTasks("silent");
                onApprovalDecided?.();
              }}
            />
          )}

        {detailSummary.phase === "failed" && (
          <FailureReason error={failureTextOf(d)} />
        )}

        {abortResult && (
          <Notice tone="warning" title="Cleanup after cancelling">
            <p className="m-0">
              {abortResult.compensations_triggered} cleanup actions started,{" "}
              {abortResult.executed} finished, {abortResult.failed} failed.
            </p>
          </Notice>
        )}

        <TaskResultSection task={d} finished={terminal} />

        <section aria-label="Instruction" className="min-w-0">
          <h3 className="maya-label m-0 mb-1">Instruction</h3>
          <ExpandableText text={d.instruction} />
        </section>

        <section aria-label="Plan" className="flex min-w-0 flex-col gap-2">
          <details open className="min-w-0">
            <summary className="maya-label mb-2 cursor-pointer">
              Technical details: steps and proofs
            </summary>
            <DagVisualizer subtasks={d.subtasks} />
          </details>
        </section>
      </div>
    );
  } else {
    detailBody = (
      <div className="p-4">
        <EmptyState
          title="Select a task"
          description="Pick a task from the list, or start a new one, to see its plan and result."
        />
      </div>
    );
  }

  return (
    <div className="@container h-full min-h-0 w-full min-w-0 bg-maya-bg text-maya-text">
      <div className="grid h-full min-h-0 min-w-0 grid-cols-1 grid-rows-[minmax(0,1fr)] @[69rem]:grid-cols-[minmax(18rem,24rem)_minmax(0,1fr)]">
        <div
          className={`min-h-0 min-w-0 ${view === "detail" ? `hidden ${showWide}` : "block"}`}
        >
          <Pane
            aria-label="Task list"
            header={listHeader}
            footer={listFooter}
            bodyLabel="Task list"
          >
            {listBody}
          </Pane>
        </div>
        <div
          className={`min-h-0 min-w-0 ${view === "list" ? `hidden ${showWide}` : "block"}`}
        >
          <Pane aria-label="Task details" bodyLabel="Task details">
            <div className="p-2 @[69rem]:hidden">
              <Button
                size="sm"
                variant="ghost"
                leftIcon={<ArrowLeft size={14} />}
                onClick={() => setView("list")}
              >
                Back to tasks
              </Button>
            </div>
            {detailBody}
          </Pane>
        </div>
      </div>

      <NewTaskModal
        isOpen={isNewTaskOpen}
        onClose={() => setIsNewTaskOpen(false)}
        onTaskCreated={(task) => {
          void loadTasks("reset");
          setSelectedTaskId(task.task_id);
          setView("detail");
        }}
      />
      {confirmIds !== null && (
        <ConfirmDeleteDialog
          title={
            confirmIds.length === 1
              ? "Delete this task?"
              : `Delete ${confirmIds.length} tasks?`
          }
          removes={TASK_DELETE_REMOVES}
          keeps={TASK_DELETE_KEEPS}
          confirmLabel={
            confirmIds.length === 1
              ? "Delete task"
              : `Delete ${confirmIds.length} tasks`
          }
          busy={deleting}
          error={deleteError}
          onClose={() => setConfirmIds(null)}
          onConfirm={() => void performDelete()}
        />
      )}
    </div>
  );
}
