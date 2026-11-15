/**
 * Typed API client for the approvals endpoints (Decision D4, Decision D5).
 */

import { getAccessToken, refreshSession } from "./auth-session";

export type ApprovalLifecycleStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "expired"
  | "revoked"
  | "claimed"
  | "executing"
  | "completed"
  | "failed"
  | "denied"
  | "consumed";

export interface ApprovalRequest {
  id: string;
  workspace_id: string;
  user_id: string;
  task_id: string;
  subtask_id: string | null;
  tool_name: string;
  tool_args: Record<string, unknown>;
  payload_hash: string;
  tier: "APPROVAL_REQUIRED" | "RESTRICTED";
  action_description: string;
  payload_diff?: Record<string, unknown> | unknown[] | string | null;
  status: ApprovalLifecycleStatus;
  approval_token?: string | null;
  requested_at: string;
  expires_at: string;
  decided_at?: string | null;
  consumed_at?: string | null;
  decision_channel?: "web" | "api" | null;
  /** True when the owner's Notion auto-approve setting, not a click, recorded the approval. */
  auto_approved?: boolean;

  // Execution tracking fields
  claimed_by?: string | null;
  claimed_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  failed_at?: string | null;
  failure_reason?: string | null;
  idempotency_key?: string | null;
  execution_output?: Record<string, unknown> | unknown[] | null;
}

export interface ApprovalDecisionRequest {
  action: "approve" | "reject" | "revoke";
  totp_code?: string;
  rejection_reason?: string;
}

export interface ApprovalDecisionResponse {
  id: string;
  status: "approved" | "rejected" | "revoked" | "denied";
  approval_token?: string | null;
  expires_at?: string | null;
}

export class ApprovalApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApprovalApiError";
    this.status = status;
    this.code = code;
  }
}

function getAuthHeaders(): HeadersInit {
  const token = getAccessToken();
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Maya-Channel": "web",
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

/** fetch with Bearer auth; on 401 refresh the session once and retry. */
async function authFetch(path: string, init: RequestInit): Promise<Response> {
  let res = await fetch(path, { ...init, headers: getAuthHeaders() });
  if (res.status === 401 && (await refreshSession())) {
    res = await fetch(path, { ...init, headers: getAuthHeaders() });
  }
  return res;
}

export async function listApprovals(
  status?: string,
  limit: number = 20,
): Promise<ApprovalRequest[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (status) {
    params.set("status", status);
  }

  const res = await authFetch(`/api/v1/approvals?${params.toString()}`, {
    method: "GET",
    cache: "no-store",
  });

  if (!res.ok) {
    const errorBody = (await res.json().catch(() => ({}))) as {
      error?: string;
      message?: string;
    };
    throw new ApprovalApiError(
      res.status,
      errorBody.error || "failed_to_list_approvals",
      errorBody.message || "Failed to list approval requests",
    );
  }

  return (await res.json()) as ApprovalRequest[];
}

export async function getApproval(id: string): Promise<ApprovalRequest> {
  const res = await authFetch(`/api/v1/approvals/${encodeURIComponent(id)}`, {
    method: "GET",
    cache: "no-store",
  });

  if (!res.ok) {
    const errorBody = (await res.json().catch(() => ({}))) as {
      error?: string;
      message?: string;
    };
    throw new ApprovalApiError(
      res.status,
      errorBody.error || "failed_to_get_approval",
      errorBody.message || "Failed to get approval request details",
    );
  }

  return (await res.json()) as ApprovalRequest;
}

export async function decideApproval(
  id: string,
  decision: ApprovalDecisionRequest,
): Promise<ApprovalDecisionResponse> {
  const res = await authFetch(
    `/api/v1/approvals/${encodeURIComponent(id)}/decide`,
    {
      method: "POST",
      body: JSON.stringify(decision),
      cache: "no-store",
    },
  );

  if (!res.ok) {
    const errorBody = (await res.json().catch(() => ({}))) as {
      error?: string;
      message?: string;
    };
    throw new ApprovalApiError(
      res.status,
      errorBody.error || "decision_failed",
      errorBody.message || "Failed to submit approval decision",
    );
  }

  return (await res.json()) as ApprovalDecisionResponse;
}

export async function revokeApproval(
  id: string,
  reason: string = "Revoked by user",
): Promise<ApprovalDecisionResponse> {
  return decideApproval(id, { action: "revoke", rejection_reason: reason });
}
