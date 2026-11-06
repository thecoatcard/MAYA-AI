/**
 * Typed client for the background tasks API (Phase 06).
 */

import { getAccessToken, refreshSession } from "./auth-session";
import { ApiError } from "./api-client";

export interface TaskSubmissionRequest {
  readonly instruction: string;
  readonly title?: string;
  readonly idempotency_key?: string;
  readonly budget_usd?: number;
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
}

export interface TaskListResponse {
  readonly items: readonly TaskListItem[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
}

export interface TaskAbortResponse {
  readonly task_id: string;
  readonly status: string;
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
    let errorCode = "unknown_error";
    try {
      const body = (await response.json()) as { error?: string };
      if (body?.error) {
        errorCode = body.error;
      }
    } catch {
      // not JSON
    }
    throw new ApiError(response.status, errorCode);
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
