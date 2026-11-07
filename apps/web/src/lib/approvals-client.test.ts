import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApprovalApiError,
  decideApproval,
  getApproval,
  listApprovals,
  revokeApproval,
  type ApprovalDecisionRequest,
  type ApprovalRequest,
} from "./approvals-client";
import * as authSession from "./auth-session";

describe("approvals-client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(authSession, "getAccessToken").mockReturnValue(
      "test-access-token",
    );
  });

  const mockApproval: ApprovalRequest = {
    id: "appr-001",
    workspace_id: "ws-001",
    user_id: "u-001",
    task_id: "task-001",
    subtask_id: "st-001",
    tool_name: "deploy_production",
    tool_args: { image: "registry.internal/app:v1.2.0" },
    payload_hash: "abcd1234efgh5678",
    tier: "RESTRICTED",
    action_description: "Deploy to production environment",
    payload_diff: null,
    status: "pending",
    approval_token: null,
    requested_at: "2026-11-06T12:00:00Z",
    expires_at: "2026-11-06T12:05:00Z",
    decided_at: null,
    consumed_at: null,
    decision_channel: null,
  };

  it("lists pending approvals with X-Maya-Channel and bearer auth", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify([mockApproval]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await listApprovals("pending", 10);

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/approvals?limit=10&status=pending",
      expect.objectContaining({
        method: "GET",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Maya-Channel": "web",
          Authorization: "Bearer test-access-token",
        },
      }),
    );
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("appr-001");
  });

  it("handles empty token gracefully in headers", async () => {
    vi.spyOn(authSession, "getAccessToken").mockReturnValue(null);

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await listApprovals();
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/approvals?limit=20",
      expect.objectContaining({
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Maya-Channel": "web",
        },
      }),
    );
    expect(result).toEqual([]);
  });

  it("throws ApprovalApiError on list failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: "forbidden",
          message: "Voice channel not allowed",
        }),
        {
          status: 403,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await expect(listApprovals()).rejects.toThrow(ApprovalApiError);
  });

  it("gets approval detail by id", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockApproval), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await getApproval("appr-001");
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/approvals/appr-001",
      expect.objectContaining({ method: "GET" }),
    );
    expect(result.tool_name).toBe("deploy_production");
  });

  it("throws ApprovalApiError when approval is not found", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({ error: "not_found", message: "Request not found" }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(getApproval("missing-id")).rejects.toThrow(ApprovalApiError);
  });

  it("decides approval with action and totp code", async () => {
    const mockDecisionRes = {
      id: "appr-001",
      status: "approved",
      approval_token: "example-jwt-token-string",
      expires_at: "2026-11-06T12:05:00Z",
    };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockDecisionRes), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const decision: ApprovalDecisionRequest = {
      action: "approve",
      totp_code: "123456",
    };

    const result = await decideApproval("appr-001", decision);

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/approvals/appr-001/decide",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(decision),
      }),
    );
    expect(result.status).toBe("approved");
    expect(result.approval_token).toBe("example-jwt-token-string");
  });

  it("revokes an approval request", async () => {
    const mockRevokeRes = {
      id: "appr-001",
      status: "revoked",
    };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockRevokeRes), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await revokeApproval("appr-001", "Security review");
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/approvals/appr-001/decide",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          action: "revoke",
          rejection_reason: "Security review",
        }),
      }),
    );
    expect(result.status).toBe("revoked");
  });
});
