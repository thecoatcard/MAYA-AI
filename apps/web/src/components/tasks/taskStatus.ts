import type { TaskDetailResponse } from "../../lib/tasks-client";

/**
 * Plain-language task state derived ONLY from real task data. No stages are
 * invented: with no subtasks the summary is "Planning" and nothing more.
 */

export type TaskPhase =
  | "loading"
  | "planning"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled";

export interface TaskSummary {
  readonly phase: TaskPhase;
  /** Short status label (text, shown with an icon, never colour only). */
  readonly label: string;
  /** One line from real subtask data, or null. */
  readonly detail: string | null;
  readonly active: boolean;
  /** True when the task finished but a step failed ("Completed with errors"). */
  readonly partial: boolean;
}

/** Label for a task that finished while a step failed (status stays "completed"). */
export const PARTIAL_LABEL = "Completed with errors";

const RUNNING = new Set(["in_progress", "running", "executing", "dispatched"]);
const DONE = new Set(["completed", "done", "succeeded"]);

/** The failed steps of a finished task, as short plain text lines. */
export function failedStepLines(
  task: Pick<TaskDetailResponse, "subtasks" | "failed_steps">,
): string[] {
  const reported = task.failed_steps ?? [];
  if (reported.length > 0) {
    return reported.map((f) => `${f.step}: ${f.error}`);
  }
  return task.subtasks
    .filter((s) => s.status.toLowerCase() === "failed")
    .map((s) => `${s.description}: ${s.error_message ?? "failed"}`);
}

export function describeTask(
  task: Pick<
    TaskDetailResponse,
    "status" | "subtasks" | "partial" | "failed_steps"
  > | null,
): TaskSummary {
  if (task === null) {
    return {
      phase: "loading",
      label: "Loading",
      detail: null,
      active: true,
      partial: false,
    };
  }
  const status = task.status.toLowerCase();
  const subtasks = task.subtasks;
  const total = subtasks.length;
  const done = subtasks.filter((s) => DONE.has(s.status.toLowerCase())).length;
  const current = subtasks.find((s) =>
    RUNNING.has(s.status.toLowerCase()),
  )?.description;
  const retrying = subtasks.find(
    (s) => s.status.toLowerCase() === "pending" && s.error_message,
  )?.error_message;
  const progress = total > 0 ? `${done} of ${total} steps done` : null;

  if (status === "cancelled" || status === "canceled" || status === "aborted") {
    return {
      phase: "cancelled",
      label: "Cancelled",
      detail: progress,
      active: false,
      partial: false,
    };
  }
  if (status === "failed") {
    const failed = subtasks.find((s) => s.status.toLowerCase() === "failed");
    return {
      phase: "failed",
      label: "Failed",
      detail: failed?.error_message ?? "The task failed.",
      active: false,
      partial: false,
    };
  }
  if (DONE.has(status)) {
    const failedLines = failedStepLines(task);
    if (task.partial === true || failedLines.length > 0) {
      const first = failedLines[0];
      const more =
        failedLines.length > 1 ? ` (+${failedLines.length - 1} more)` : "";
      return {
        phase: "completed",
        label: PARTIAL_LABEL,
        detail:
          first !== undefined
            ? `Failed: ${first}${more}`.slice(0, 280)
            : (progress ?? "A step failed."),
        active: false,
        partial: true,
      };
    }
    return {
      phase: "completed",
      label: "Completed",
      detail: progress,
      active: false,
      partial: false,
    };
  }
  const waiting =
    status === "awaiting_approval" ||
    subtasks.some((s) => s.status.toLowerCase() === "awaiting_approval");
  if (waiting) {
    return {
      phase: "awaiting_approval",
      label: "Waiting for your approval",
      detail: progress,
      active: true,
      partial: false,
    };
  }
  if (total === 0) {
    return {
      phase: "planning",
      label: "Planning",
      detail: null,
      active: true,
      partial: false,
    };
  }
  return {
    phase: "running",
    label: "In progress",
    detail: retrying
      ? `${progress ?? "Working"}. Retrying: ${retrying}`
      : current
        ? `${progress}. Now: ${current}`
        : progress,
    active: true,
    partial: false,
  };
}

/* ------------------------------------------------------------ status mapping */

/** Phase for any raw task OR subtask status string (they share one vocabulary). */
export function phaseOfStatus(raw: string): TaskPhase {
  const status = raw.toLowerCase();
  if (status === "cancelled" || status === "canceled" || status === "aborted") {
    return "cancelled";
  }
  if (status === "failed") return "failed";
  if (DONE.has(status)) return "completed";
  if (status === "awaiting_approval" || status === "paused") {
    return "awaiting_approval";
  }
  if (RUNNING.has(status)) return "running";
  return "planning";
}

export const PHASE_LABEL: Readonly<Record<TaskPhase, string>> = {
  loading: "Loading",
  planning: "Pending",
  running: "In progress",
  awaiting_approval: "Awaiting approval",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** Terminal phases never show a spinner and never offer cancel. */
export function isTerminalPhase(phase: TaskPhase): boolean {
  return phase === "completed" || phase === "failed" || phase === "cancelled";
}

/** The real error text of a failed task: the first failed step's message. */
export function failureTextOf(
  task: Pick<TaskDetailResponse, "subtasks"> | null,
): string | null {
  if (task === null) return null;
  const failed = task.subtasks.find(
    (s) => s.status.toLowerCase() === "failed" && s.error_message,
  );
  return failed?.error_message ?? null;
}

/* ------------------------------------------------------------------- cost */

/**
 * Spend as text. The API returns a number even when nothing was measured, so a
 * zero (or missing) value is "Cost not tracked", never a fact like "$0.0000".
 */
export function formatCost(spent: number | null | undefined): string {
  const value = Number(spent);
  if (!Number.isFinite(value) || value <= 0) return "Cost not tracked";
  return `$${value.toFixed(value < 0.01 ? 4 : 2)}`;
}

/* ---------------------------------------------------------------- failures */

export interface FailureExplanation {
  /** One plain sentence about what went wrong. */
  readonly summary: string;
  /** The exact technical text from the server, or null when there is none. */
  readonly technical: string | null;
}

/**
 * Plain-language reason first, raw text second. Patterns are matched on the
 * real error text; anything unknown gets a neutral sentence (nothing invented)
 * and the full original text stays available.
 */
export function explainFailure(
  raw: string | null | undefined,
): FailureExplanation {
  const text = (raw ?? "").trim();
  if (text === "") {
    return {
      summary: "The task failed. No reason was recorded.",
      technical: null,
    };
  }
  const tool = /tool\s+'([^']+)'/i.exec(text)?.[1];
  const missing = /missing required arguments?:?\s*\[([^\]]*)\]/i.exec(
    text,
  )?.[1];
  const subject = tool ? `The "${tool}" step` : "A step";
  if (missing !== undefined) {
    const names = missing
      .split(",")
      .map((part) => part.replace(/['"\s]/g, ""))
      .filter((part) => part !== "");
    return {
      summary: `${subject} was missing a required input${
        names.length > 0 ? ` (${names.join(", ")})` : ""
      }.`,
      technical: text,
    };
  }
  if (/denied/i.test(text)) {
    return { summary: `${subject} was not allowed to run.`, technical: text };
  }
  if (/timed? ?out|timeout/i.test(text)) {
    return {
      summary: `${subject} took too long and timed out.`,
      technical: text,
    };
  }
  if (/quota|rate.?limit|\b429\b/i.test(text)) {
    return { summary: "A service limit was reached.", technical: text };
  }
  if (/unauthori[sz]ed|forbidden|\b40[13]\b|permission/i.test(text)) {
    return {
      summary: `${subject} did not have permission to continue.`,
      technical: text,
    };
  }
  if (/budget/i.test(text)) {
    return { summary: "The task reached its budget limit.", technical: text };
  }
  return { summary: `${subject} failed.`, technical: text };
}
