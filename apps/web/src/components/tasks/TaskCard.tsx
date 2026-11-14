"use client";

import {
  ClipboardCopy,
  Hash,
  MoreHorizontal,
  OctagonX,
  RotateCcw,
  ScrollText,
  TextQuote,
  Trash2,
  X,
} from "lucide-react";
import React, { useEffect, useMemo, useRef, useState } from "react";

import { useTaskEvents } from "../../hooks/useTaskEvents";
import {
  isApprovalWait,
  settleProgress,
  taskProgressSteps,
} from "../../lib/progress-steps";
import { abortTask, deleteTask, submitTask } from "../../lib/tasks-client";
import { AutoApprovedNote } from "../approvals/AutoApprovedNote";
import { TaskApprovals } from "../approvals/TaskApprovals";
import { pendingTaskInput } from "../../lib/task-input";
import { TaskInputCard } from "./TaskInputCard";
import { AskUserCard } from "../chat/AskUserCard";
import { ProgressSteps } from "../chat/ProgressSteps";
import { needsDetailQuestion, taskDetailRequest } from "../../lib/ask-user";
import { Button, ConfirmDeleteDialog, Truncate } from "../ui";
import { MenuButton, useContextMenu, type ContextMenuEntry } from "../ui";
import { copyText } from "../../lib/menu-actions";
import { resultTextOf } from "./TaskResult";
import { FailureReason } from "./FailureReason";
import {
  CANCEL_FIRST,
  TASK_DELETE_KEEPS,
  TASK_DELETE_REMOVES,
} from "./taskDeleteCopy";
import { TaskFiles } from "./TaskFiles";
import { TaskResult } from "./TaskResult";
import { TaskStatusBadge } from "./TaskStatusBadge";
import {
  describeTask,
  failureTextOf,
  isTerminalPhase,
  phaseOfStatus,
  type TaskPhase,
} from "./taskStatus";

/** Event types that change what the card must show: refetch the task on each. */
const LIFECYCLE_EVENTS = new Set([
  "task_completed",
  "task_failed",
  "task_cancelled",
  "task_paused_approval",
  "task_resumed",
  "subtask_completed",
  "subtask_failed",
  "subtask_started",
  "plan_created",
  // The names the task engine publishes (backend TaskEventType).
  "task_plan_created",
  "subtask_dispatched",
  "subtask_status_changed",
]);

/** How often an unfinished card re-reads its task, as a net under the event stream. */
const CARD_POLL_MS = 5000;

const SETTLED_STEP_STATE = {
  completed: "done",
  failed: "failed",
  cancelled: "skipped",
} as const;
const STEP_LIST_STATE = {
  completed: "complete",
  failed: "interrupted",
  cancelled: "stopped",
} as const;

export interface TaskCardProps {
  readonly taskId: string;
  /** Title known when the card was created; the live task title wins. */
  readonly fallbackTitle?: string;
  /**
   * Status the caller already knows (for example from its own task list). It
   * is used until the live detail arrives, and a terminal value here always
   * wins over a stale non-terminal live detail, so a finished task never keeps
   * a spinner.
   */
  readonly liveStatus?: string;
  /** Called after the server accepted an abort. */
  readonly onAborted?: (taskId: string) => void;
  /** Failed task: called with the id of the new task created by "Try again" (add its card). */
  readonly onRetried?: (newTaskId: string) => void;
  /** When given, a "Dismiss" button hides the card of a finished task. */
  readonly onDismiss?: (taskId: string) => void;
  /** Called after the task was deleted from history (the card then reads "deleted"). */
  readonly onDeleted?: (taskId: string) => void;
  /** Called once when this card saw its task go from running to a final state. */
  readonly onFinished?: (taskId: string) => void;
  /** Called after an approval of this task was decided on the card (refresh counters). */
  readonly onApprovalDecided?: (taskId: string) => void;
  /**
   * A step stopped for a missing detail and the card shows the question. The answer goes back as
   * a chat message (the model starts the task again with it); it never approves anything.
   */
  readonly onDetailAnswered?: (
    taskId: string,
    title: string,
    question: string,
    answer: string,
  ) => void;
}

/**
 * Inline task card. Everything shown comes from the task API and its event
 * stream: title, status, step progress and the real failure text. It follows
 * the live state (the stream and a refetch on every lifecycle event), shows
 * terminal states as final (no spinner), and explains a failure in plain words
 * with the technical text one click away.
 */
export function TaskCard({
  taskId,
  fallbackTitle,
  liveStatus,
  onAborted,
  onRetried,
  onDismiss,
  onDeleted,
  onApprovalDecided,
  onDetailAnswered,
  onFinished,
}: TaskCardProps): React.JSX.Element {
  const [detailAnswer, setDetailAnswer] = useState<string | null>(null);
  const { taskDetail, error, lastEvent, events, refetchTask, taskGone } =
    useTaskEvents({
      taskId,
    });
  // The live step list of the task (task_progress events). The stream has no replay, so after a
  // reload the card shows the task detail only.
  const liveSteps = useMemo(() => taskProgressSteps(events), [events]);
  const [aborting, setAborting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleted, setDeleted] = useState(false);
  const articleRef = useRef<HTMLElement>(null);

  // The stream only delivers events; the card's data is the task detail. Refetch it whenever an
  // event says something changed, otherwise a card stays "In progress" after the task ends.
  useEffect(() => {
    const type = lastEvent?.event_type;
    if (type !== undefined && LIFECYCLE_EVENTS.has(type)) void refetchTask?.();
  }, [lastEvent, refetchTask]);

  const base = describeTask(taskDetail);
  const knownPhase: TaskPhase | null =
    liveStatus !== undefined ? phaseOfStatus(liveStatus) : null;
  const useKnown =
    knownPhase !== null &&
    (taskDetail === null ||
      (isTerminalPhase(knownPhase) && !isTerminalPhase(base.phase)));
  const described = useKnown
    ? describeTask({
        status: liveStatus as string,
        subtasks: taskDetail?.subtasks ?? [],
      })
    : base;
  // A step the engine paused for approval means the task waits for the user, whatever a stale
  // task status says: the card must not read "In progress" next to "Waiting for approval".
  const approvalPaused = liveSteps.some(
    (step) => step.state === "skipped" && isApprovalWait(step.detail),
  );
  const summary =
    approvalPaused &&
    !isTerminalPhase(described.phase) &&
    described.phase !== "awaiting_approval"
      ? {
          ...described,
          phase: "awaiting_approval" as const,
          label: "Waiting for your approval",
        }
      : described;
  const terminal = isTerminalPhase(summary.phase);
  // A step paused to ask for a detail waits for an ANSWER: no approval lookup for it.
  const needsInput = pendingTaskInput(taskDetail);
  // Tell the thread when the task ENDS while this card watches it (not for a card that opens on
  // an already finished task: its result message is part of the loaded history).
  const sawRunning = useRef(false);
  useEffect(() => {
    if (summary.phase === "loading") return;
    if (!terminal) {
      sawRunning.current = true;
    } else if (sawRunning.current) {
      sawRunning.current = false;
      onFinished?.(taskId);
    }
  }, [summary.phase, terminal, taskId, onFinished]);
  const endPhase =
    summary.phase === "completed" ||
    summary.phase === "failed" ||
    summary.phase === "cancelled"
      ? summary.phase
      : null;
  // A finished task never keeps a step spinning, whatever event was missed.
  const steps =
    endPhase === null
      ? liveSteps
      : (settleProgress(liveSteps, SETTLED_STEP_STATE[endPhase]) ?? []);
  // The event stream has no replay, so a missed event would leave the card on "0 of N steps" for
  // good. While the task is not finished, refetch it now and then; stop once it is terminal.
  useEffect(() => {
    if (terminal || refetchTask === undefined) return;
    const timer = setInterval(() => void refetchTask(), CARD_POLL_MS);
    return () => clearInterval(timer);
  }, [terminal, refetchTask]);

  const title =
    taskDetail?.title ?? taskDetail?.instruction ?? fallbackTitle ?? "Task";
  const failure =
    summary.phase === "failed" ? (failureTextOf(taskDetail) ?? null) : null;

  const cancel = async (): Promise<void> => {
    setAborting(true);
    setActionError(null);
    try {
      await abortTask(taskId);
      onAborted?.(taskId);
      void refetchTask?.();
    } catch {
      setActionError("The task could not be cancelled. Try again.");
    } finally {
      setAborting(false);
    }
  };

  const remove = async (): Promise<void> => {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteTask(taskId);
      setDeleted(true);
      setConfirming(false);
      onDeleted?.(taskId);
    } catch {
      setDeleteError("The task could not be deleted. Try again.");
    } finally {
      setDeleting(false);
    }
  };

  const retry = async (): Promise<void> => {
    if (taskDetail === null) return;
    setRetrying(true);
    setActionError(null);
    try {
      const created = await submitTask({
        instruction: taskDetail.instruction,
        title: taskDetail.title,
      });
      onRetried?.(created.task_id);
    } catch {
      setActionError("The task could not be started again. Try again.");
    } finally {
      setRetrying(false);
    }
  };

  const menu = useContextMenu({
    label: "Task actions",
    getItems: () => {
      const details = [
        `Task: ${title}`,
        `Status: ${summary.label}`,
        ...(taskDetail !== null ? [`Request: ${taskDetail.instruction}`] : []),
      ].join("\n");
      const result = resultTextOf(taskDetail);
      const items: ContextMenuEntry[] = [
        {
          id: "copy-details",
          label: "Copy details",
          icon: <ClipboardCopy size={14} />,
          onSelect: () => void copyText(details),
        },
      ];
      if (summary.phase === "failed") {
        items.push(
          {
            id: "copy-error",
            label: "Copy error",
            icon: <TextQuote size={14} />,
            onSelect: () => void copyText(failureTextOf(taskDetail) ?? ""),
          },
          {
            id: "technical",
            label: "Show technical details",
            icon: <ScrollText size={14} />,
            onSelect: () => {
              const d = articleRef.current?.querySelector("details");
              if (d instanceof HTMLDetailsElement) d.open = true;
            },
          },
        );
      }
      if (result !== null) {
        items.push({
          id: "copy-result",
          label: "Copy result",
          icon: <ClipboardCopy size={14} />,
          onSelect: () => void copyText(result),
        });
      }
      items.push({
        id: "copy-id",
        label: "Copy task ID",
        icon: <Hash size={14} />,
        onSelect: () => void copyText(taskId),
      });
      if (!terminal && summary.active && summary.phase !== "loading") {
        items.push({
          id: "cancel",
          label: "Cancel task",
          icon: <OctagonX size={14} />,
          onSelect: () => void cancel(),
        });
      }
      if (summary.phase === "failed" && taskDetail !== null) {
        items.push({
          id: "retry",
          label: "Try again",
          icon: <RotateCcw size={14} />,
          onSelect: () => void retry(),
        });
      }
      items.push(
        { kind: "separator" },
        {
          id: "delete",
          label: "Delete task",
          icon: <Trash2 size={14} />,
          danger: true,
          disabled: !terminal,
          disabledReason: CANCEL_FIRST,
          onSelect: () => {
            setDeleteError(null);
            setConfirming(true);
          },
        },
      );
      return items;
    },
  });

  if (deleted || taskGone === true) {
    return (
      <article
        aria-label="Deleted task"
        data-testid="task-card-deleted"
        className="my-3 min-w-0 max-w-full rounded-maya-lg border border-maya-border bg-maya-pane p-3"
      >
        <p className="m-0 text-maya-small text-maya-muted">
          This task was deleted.
        </p>
      </article>
    );
  }

  return (
    <article
      ref={articleRef}
      {...menu.triggerProps}
      aria-label={`Task: ${title}`}
      data-testid="task-card"
      data-phase={summary.phase}
      className={`my-3 flex min-w-0 max-w-full flex-col gap-2 rounded-maya-lg border border-maya-border bg-maya-pane p-3 text-maya-text shadow-maya-1 transition-[border-color,background-color,box-shadow] duration-200 ease-out ${
        summary.phase === "failed" ? "border-l-4 border-l-maya-danger" : ""
      }`}
    >
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <Truncate
          as="p"
          lines={2}
          title={title}
          dir="auto"
          className="m-0 min-w-0 flex-1 basis-40 text-maya-small font-medium"
        >
          {title}
        </Truncate>
        <span className="relative flex items-center gap-1">
          <TaskStatusBadge
            phase={summary.phase}
            label={summary.label}
            partial={summary.partial}
            data-testid="task-status"
          />
          <MenuButton label="More task actions" buttonProps={menu.buttonProps}>
            <MoreHorizontal size={14} aria-hidden="true" />
          </MenuButton>
          {menu.menu}
        </span>
      </div>
      {summary.phase !== "failed" && summary.detail !== null && (
        <p
          className="m-0 min-w-0 text-maya-small text-maya-muted [overflow-wrap:anywhere]"
          data-testid="task-detail"
        >
          {summary.detail}
        </p>
      )}
      {summary.phase === "failed" &&
        onDetailAnswered !== undefined &&
        needsDetailQuestion(failure) !== null && (
          <AskUserCard
            request={taskDetailRequest(
              taskId,
              needsDetailQuestion(failure) ?? "",
            )}
            state={detailAnswer === null ? "awaiting_input" : "answered"}
            answer={
              detailAnswer === null
                ? null
                : {
                    askId: `task-${taskId}`,
                    answers: [
                      { question: "", selected: [], text: detailAnswer },
                    ],
                  }
            }
            autoFocus={false}
            onSkip={() => undefined}
            onSubmit={(answers) => {
              const text = answers[0]?.text ?? "";
              setDetailAnswer(text);
              onDetailAnswered(
                taskId,
                title,
                needsDetailQuestion(failure) ?? "",
                text,
              );
            }}
          />
        )}
      {summary.phase === "failed" && (
        <FailureReason
          error={failure}
          summaryTestId="task-detail"
          technicalTestId="task-technical"
        />
      )}
      <ProgressSteps
        steps={steps}
        replyState={endPhase === null ? "live" : STEP_LIST_STATE[endPhase]}
      />
      {error !== null && taskDetail === null && (
        <p className="m-0 text-maya-small text-maya-danger">
          Task details could not be loaded.
        </p>
      )}
      {actionError !== null && (
        <p role="alert" className="m-0 text-maya-small text-maya-danger">
          {actionError}
        </p>
      )}
      {summary.phase !== "awaiting_approval" && (
        <AutoApprovedNote taskId={taskId} phase={summary.phase} />
      )}
      {summary.phase === "awaiting_approval" && needsInput !== null && (
        <TaskInputCard
          taskId={taskId}
          pending={needsInput}
          onAnswered={() => void refetchTask?.()}
        />
      )}
      {summary.phase === "awaiting_approval" && needsInput === null && (
        <TaskApprovals
          taskId={taskId}
          waiting
          refreshKey={taskDetail}
          onDecided={() => {
            void refetchTask?.();
            onApprovalDecided?.(taskId);
          }}
        />
      )}
      {summary.phase === "completed" && <TaskResult task={taskDetail} />}
      {terminal && (
        <TaskFiles taskId={taskId} task={taskDetail} phase={summary.phase} />
      )}
      <div className="flex flex-wrap gap-2">
        {summary.active && summary.phase !== "loading" && !terminal && (
          <Button size="sm" loading={aborting} onClick={() => void cancel()}>
            Cancel task
          </Button>
        )}
        {summary.phase === "failed" && taskDetail !== null && (
          <Button
            size="sm"
            loading={retrying}
            leftIcon={<RotateCcw size={14} />}
            onClick={() => void retry()}
          >
            Try again
          </Button>
        )}
        {terminal && onDismiss !== undefined && (
          <Button
            size="sm"
            variant="ghost"
            leftIcon={<X size={14} />}
            onClick={() => onDismiss(taskId)}
          >
            Dismiss
          </Button>
        )}
      </div>
      {confirming && (
        <ConfirmDeleteDialog
          title="Delete this task?"
          removes={TASK_DELETE_REMOVES}
          keeps={TASK_DELETE_KEEPS}
          confirmLabel="Delete task"
          busy={deleting}
          error={deleteError}
          onClose={() => setConfirming(false)}
          onConfirm={() => void remove()}
        />
      )}
    </article>
  );
}
