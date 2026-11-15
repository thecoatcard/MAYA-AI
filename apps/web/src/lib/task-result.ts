import type { StoredMessage } from "./api-client";

/** The final state a task result message reports (matches the server's TaskResultOut). */
export type TaskResultStatus =
  "done" | "partial" | "failed" | "cancelled" | "needs_attention";

export interface TaskResultMark {
  readonly taskId: string;
  readonly status: TaskResultStatus;
  readonly artifactIds: readonly string[];
}

const STATUSES: ReadonlySet<string> = new Set([
  "done",
  "partial",
  "failed",
  "cancelled",
  "needs_attention",
]);

/** The task-result marker of a stored assistant message, or null (read defensively). */
export function readTaskResult(message: StoredMessage): TaskResultMark | null {
  if (message.role !== "assistant") return null;
  const raw = message.metadata?.task_result;
  if (raw === null || typeof raw !== "object") return null;
  const mark = raw as {
    task_id?: unknown;
    status?: unknown;
    artifact_ids?: unknown;
  };
  if (typeof mark.task_id !== "string" || mark.task_id === "") return null;
  if (typeof mark.status !== "string" || !STATUSES.has(mark.status)) {
    return null;
  }
  const ids = Array.isArray(mark.artifact_ids)
    ? mark.artifact_ids.filter((id): id is string => typeof id === "string")
    : [];
  return {
    taskId: mark.task_id,
    status: mark.status as TaskResultStatus,
    artifactIds: ids,
  };
}

/** The result messages that report on `taskId`, oldest first. */
export function resultsForTask(
  messages: readonly StoredMessage[],
  taskId: string,
): StoredMessage[] {
  return messages.filter((m) => readTaskResult(m)?.taskId === taskId);
}

/** Delays between checks for a task's result message after the task ended (ms). */
export const RESULT_POLL_DELAYS_MS: readonly number[] = [
  1500, 4000, 8000, 15000, 30000, 60000,
];
