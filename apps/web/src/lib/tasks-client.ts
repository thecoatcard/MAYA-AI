/**
 * Typed client for the background tasks API (Phase 06).
 */

import { getAccessToken, refreshSession } from "./auth-session";
import { ApiError, apiErrorFromResponse } from "./api-client";

export interface TaskSubmissionRequest {
  readonly instruction: string;
  readonly title?: string;
  readonly idempotency_key?: string;
  readonly budget_usd?: number;
  readonly session_id?: string;
}

export interface TaskSubmissionResponse {
  readonly task_id: string;
  readonly status: string;
  readonly idempotency_key: string;
  readonly created_at: string;
  readonly is_new: boolean;
}

export interface SubtaskResponse {
  readonly id: string;
  readonly task_id: string;
  readonly assigned_agent: string;
  readonly description: string;
  readonly dependencies: readonly string[];
  readonly status: string;
  readonly input_data: Record<string, unknown>;
  readonly output_result?: unknown;
  readonly verification_proof?: unknown;
  readonly error_message?: string;
  readonly retry_count: number;
  readonly created_at: string;
  readonly updated_at: string;
  readonly completed_at?: string;
}

export interface TaskDetailResponse {
  readonly id: string;
  readonly workspace_id: string;
  readonly user_id: string;
  readonly session_id: string;
  readonly title?: string;
  readonly instruction: string;
  readonly idempotency_key?: string;
  readonly status: string;
  readonly plan_status: string;
  readonly budget_usd: number;
  readonly spent_usd: number;
  readonly tool_turns: number;
  readonly created_at: string;
  readonly updated_at: string;
  readonly completed_at?: string;
  readonly active_plan: readonly Record<string, unknown>[];
  readonly subtasks: readonly SubtaskResponse[];
  /** True when the task finished ("completed") but a step failed. */
  readonly partial?: boolean;
  readonly failed_steps?: readonly {
    readonly step: string;
    readonly error: string;
    readonly deliverable_incomplete?: boolean;
  }[];
}

export interface TaskListItem {
  readonly id: string;
  readonly workspace_id: string;
  readonly title?: string;
  readonly instruction: string;
  readonly status: string;
  readonly plan_status: string;
  readonly budget_usd: number;
  readonly spent_usd: number;
  readonly created_at: string;
  readonly completed_at?: string;
  readonly subtask_count: number;
  readonly partial?: boolean;
}

export interface TaskListResponse {
  readonly items: readonly TaskListItem[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
}

export interface TaskAbortResponse {
  readonly task_id: string;
  readonly status: "cancelled" | string;
  readonly compensations_triggered: number;
  readonly executed: number;
  readonly failed: number;
  readonly notifications: readonly string[];
}

async function authenticatedFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  let token = getAccessToken();
  if (!token) {
    const refreshed = await refreshSession();
    token = refreshed ? getAccessToken() : null;
  }

  const headers = new Headers(init?.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  let response = await fetch(path, { ...init, headers });

  if (response.status === 401) {
    const refreshed = await refreshSession();
    if (refreshed) {
      const retryToken = getAccessToken();
      if (retryToken) {
        headers.set("Authorization", `Bearer ${retryToken}`);
        response = await fetch(path, { ...init, headers });
      }
    }
  }

  if (!response.ok) {
    throw await apiErrorFromResponse(response);
  }

  return response;
}

export async function submitTask(
  request: TaskSubmissionRequest,
): Promise<TaskSubmissionResponse> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (request.idempotency_key) {
    headers["Idempotency-Key"] = request.idempotency_key;
  }

  const response = await authenticatedFetch("/api/v1/tasks", {
    method: "POST",
    headers,
    body: JSON.stringify(request),
  });

  return (await response.json()) as TaskSubmissionResponse;
}

/** Alias for submitTask for backend API compatibility */
export const createTask = submitTask;

export async function listTasks(
  taskStatus?: string,
  limit: number = 20,
  offset: number = 0,
): Promise<TaskListResponse> {
  const params = new URLSearchParams();
  if (taskStatus) {
    params.set("status", taskStatus);
  }
  params.set("limit", limit.toString());
  params.set("offset", offset.toString());

  const response = await authenticatedFetch(
    `/api/v1/tasks?${params.toString()}`,
    {
      method: "GET",
    },
  );

  return (await response.json()) as TaskListResponse;
}

export async function getTask(taskId: string): Promise<TaskDetailResponse> {
  const response = await authenticatedFetch(`/api/v1/tasks/${taskId}`, {
    method: "GET",
  });

  return (await response.json()) as TaskDetailResponse;
}

export async function abortTask(taskId: string): Promise<TaskAbortResponse> {
  const response = await authenticatedFetch(`/api/v1/tasks/${taskId}/abort`, {
    method: "POST",
  });

  return (await response.json()) as TaskAbortResponse;
}

/** Answer the question of a step that paused for a detail (the task card's inline question). */
export async function answerTaskInput(
  taskId: string,
  subtaskId: string,
  answers: readonly {
    readonly selected: readonly string[];
    readonly text: string;
  }[],
): Promise<{ readonly message: string }> {
  const response = await authenticatedFetch(`/api/v1/tasks/${taskId}/input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      subtask_id: subtaskId,
      answers: answers.map((a) => ({
        selected: [...a.selected],
        text: a.text,
      })),
    }),
  });
  return (await response.json()) as { message: string };
}

/** Most ids the server accepts in one bulk delete. */
export const MAX_BULK_DELETE = 100;

export interface BulkDeleteTasksResult {
  readonly deleted: readonly string[];
  readonly notFound: readonly string[];
  readonly notFinished: readonly string[];
}

/**
 * Delete one finished task from history. A task that is still running or waiting is refused
 * (409, "Cancel the task first"). A task that is already gone (404) is treated as deleted so a
 * second click never shows an error.
 */
export async function deleteTask(taskId: string): Promise<void> {
  try {
    await authenticatedFetch(`/api/v1/tasks/${encodeURIComponent(taskId)}`, {
      method: "DELETE",
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return;
    throw error;
  }
}

/** Delete several finished tasks (batches of 100). Unfinished ones come back as `notFinished`. */
export async function deleteTasks(
  ids: readonly string[],
): Promise<BulkDeleteTasksResult> {
  const deleted: string[] = [];
  const notFound: string[] = [];
  const notFinished: string[] = [];
  for (let start = 0; start < ids.length; start += MAX_BULK_DELETE) {
    const response = await authenticatedFetch("/api/v1/tasks/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: ids.slice(start, start + MAX_BULK_DELETE) }),
    });
    const body = (await response.json()) as {
      deleted?: string[];
      not_found?: string[];
      not_finished?: string[];
    };
    deleted.push(...(body.deleted ?? []));
    notFound.push(...(body.not_found ?? []));
    notFinished.push(...(body.not_finished ?? []));
  }
  return { deleted, notFound, notFinished };
}
