/**
 * Typed client for Tamper-Evident Audit Trail REST API (Phase 07/12).
 */

import { ApiError } from "./api-client";
import { getAccessToken, refreshSession } from "./auth-session";

export interface AuditEvent {
  readonly id: string;
  readonly timestamp: string;
  readonly eventType: string;
  readonly category: "auth" | "task" | "tool" | "approval" | "security";
  readonly actor: string;
  readonly details: string;
  readonly status: "success" | "pending" | "rejected" | "warning";
  readonly taskId?: string;
  readonly subtaskId?: string;
  readonly approvalId?: string;
  readonly serverName?: string;
  readonly toolName?: string;
  readonly permissionLevel?: "READ" | "WRITE" | "SENSITIVE" | "RESTRICTED";
  readonly prevHash: string;
  readonly rowHash: string;
  readonly durationMs?: number;
  readonly inputPayload?: Record<string, unknown>;
  readonly outputPayload?: Record<string, unknown>;
}

export interface AuditListResponse {
  readonly items: readonly AuditEvent[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
  readonly chain_valid: boolean;
}

interface BackendAuditEventItem {
  readonly id: string;
  readonly timestamp: string;
  readonly event_type: string;
  readonly category: string;
  readonly actor: string;
  readonly details: string;
  readonly status: string;
  readonly task_id?: string | null;
  readonly subtask_id?: string | null;
  readonly approval_id?: string | null;
  readonly server_name?: string | null;
  readonly tool_name?: string | null;
  readonly permission_level?: string | null;
  readonly prev_hash: string;
  readonly row_hash: string;
  readonly duration_ms?: number | null;
  readonly input_payload?: Record<string, unknown> | null;
  readonly output_payload?: Record<string, unknown> | null;
}

interface BackendAuditListResponse {
  readonly items: readonly BackendAuditEventItem[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
  readonly chain_valid: boolean;
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

function normalizeCategory(
  cat: string,
): "auth" | "task" | "tool" | "approval" | "security" {
  const lower = cat.toLowerCase();
  if (
    lower === "auth" ||
    lower === "task" ||
    lower === "tool" ||
    lower === "approval" ||
    lower === "security"
  ) {
    return lower;
  }
  return "security";
}

function normalizeStatus(
  stat: string,
): "success" | "pending" | "rejected" | "warning" {
  const lower = stat.toLowerCase();
  if (
    lower === "success" ||
    lower === "pending" ||
    lower === "rejected" ||
    lower === "warning"
  ) {
    return lower;
  }
  if (lower === "failed" || lower === "error") return "rejected";
  return "success";
}

export async function listAuditEvents(params?: {
  readonly category?: string;
  readonly taskId?: string;
  readonly limit?: number;
  readonly offset?: number;
}): Promise<AuditListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.category && params.category !== "all") {
    searchParams.set("category", params.category);
  }
  if (params?.taskId) {
    searchParams.set("task_id", params.taskId);
  }
  if (params?.limit !== undefined) {
    searchParams.set("limit", String(params.limit));
  }
  if (params?.offset !== undefined) {
    searchParams.set("offset", String(params.offset));
  }

  const query = searchParams.toString();
  const url = `/api/v1/audit${query ? `?${query}` : ""}`;
  const response = await authenticatedFetch(url);
  const data = (await response.json()) as BackendAuditListResponse;

  const normalizedItems: AuditEvent[] = data.items.map((item) => ({
    id: item.id,
    timestamp: item.timestamp,
    eventType: item.event_type,
    category: normalizeCategory(item.category),
    actor: item.actor,
    details: item.details,
    status: normalizeStatus(item.status),
    taskId: item.task_id ?? undefined,
    subtaskId: item.subtask_id ?? undefined,
    approvalId: item.approval_id ?? undefined,
    serverName: item.server_name ?? undefined,
    toolName: item.tool_name ?? undefined,
    permissionLevel:
      (item.permission_level as
        "READ" | "WRITE" | "SENSITIVE" | "RESTRICTED") ?? undefined,
    prevHash: item.prev_hash,
    rowHash: item.row_hash,
    durationMs: item.duration_ms ?? undefined,
    inputPayload: item.input_payload ?? undefined,
    outputPayload: item.output_payload ?? undefined,
  }));

  return {
    items: normalizedItems,
    total: data.total,
    limit: data.limit,
    offset: data.offset,
    chain_valid: data.chain_valid,
  };
}
