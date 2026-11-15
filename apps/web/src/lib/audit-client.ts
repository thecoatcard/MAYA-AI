/**
 * Typed client for Tamper-Evident Audit Trail REST API (Phase 07/12).
 */

import { ApiError } from "./api-client";
import { getAccessToken, refreshSession } from "./auth-session";

/** Status vocabulary written by the backend audit writer. */
export const AUDIT_STATUSES = [
  "success",
  "failed",
  "denied",
  "pending",
  "rejected",
  "warning",
  "aborted",
  "timeout",
  "expired",
] as const;
export type AuditStatus = (typeof AUDIT_STATUSES)[number];

export interface AuditEvent {
  readonly id: string;
  readonly timestamp: string;
  readonly eventType: string;
  readonly category: "auth" | "task" | "tool" | "approval" | "security";
  readonly actor: string;
  readonly details: string;
  readonly status: AuditStatus;
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
  /** Id of the first row that failed hash verification, or null. */
  readonly chain_first_bad_id: string | null;
  /** Number of rows verified. */
  readonly chain_length: number;
  /** Status vocabulary the server accepts as filter values. */
  readonly statuses: readonly string[];
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
  readonly chain_first_bad_id?: string | null;
  readonly chain_length?: number;
  readonly statuses?: readonly string[];
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
      const body = (await response.json()) as {
        error?: string;
        message?: string;
      };
      if (body?.error) {
        errorCode = body.error;
      }
      if (body?.message) {
        throw new ApiError(response.status, errorCode, {
          message: body.message,
        });
      }
    } catch (err) {
      if (err instanceof ApiError) throw err;
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

function normalizeStatus(stat: string): AuditStatus {
  const lower = stat.toLowerCase();
  if ((AUDIT_STATUSES as readonly string[]).includes(lower)) {
    return lower as AuditStatus;
  }
  if (lower === "error") return "failed";
  // Unknown vocabulary must never read as success.
  return "warning";
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
    chain_first_bad_id: data.chain_first_bad_id ?? null,
    chain_length: data.chain_length ?? 0,
    statuses: data.statuses ?? AUDIT_STATUSES,
  };
}
