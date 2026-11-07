import { beforeEach, describe, expect, it, vi } from "vitest";

import { abortTask, getTask, listTasks, submitTask } from "./tasks-client";
import * as authSession from "./auth-session";

describe("tasks-client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(authSession, "getAccessToken").mockReturnValue(
      "example-test-access-token",
    );
    vi.spyOn(authSession, "refreshSession").mockResolvedValue({
      accessToken: "example-test-access-token",
      expiresIn: 3600,
    });
  });

  it("submits task with idempotency key and returns submission response", async () => {
    const mockResponse = {
      task_id: "task-123",
      status: "executing",
      idempotency_key: "key-123",
      created_at: "2026-11-06T12:00:00Z",
      is_new: true,
    };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockResponse), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await submitTask({
      instruction: "Process financial invoices",
      idempotency_key: "key-123",
      budget_usd: 2.0,
    });

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/tasks",
      expect.objectContaining({
        method: "POST",
        headers: expect.any(Headers),
      }),
    );
    expect(result).toEqual(mockResponse);
  });

  it("lists tasks with pagination and status filters", async () => {
    const mockList = {
      items: [
        {
          id: "task-1",
          workspace_id: "ws-1",
          instruction: "Run tests",
          status: "completed",
          plan_status: "completed",
          budget_usd: 1.5,
          spent_usd: 0.1,
          created_at: "2026-11-06T12:00:00Z",
          subtask_count: 3,
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockList), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await listTasks("completed", 20, 0);
    expect(result.items).toHaveLength(1);
    expect(result.total).toBe(1);
  });

  it("fetches task details by id", async () => {
    const mockDetail = {
      id: "task-456",
      workspace_id: "ws-1",
      user_id: "u-1",
      session_id: "s-1",
      instruction: "Analyze competitor docs",
      status: "executing",
      plan_status: "executing",
      budget_usd: 2.0,
      spent_usd: 0.05,
      tool_turns: 2,
      created_at: "2026-11-06T12:00:00Z",
      updated_at: "2026-11-06T12:05:00Z",
      active_plan: [],
      subtasks: [
        {
          id: "st-1",
          task_id: "task-456",
          assigned_agent: "research",
          description: "Search web",
          dependencies: [],
          status: "completed",
          input_data: {},
          retry_count: 0,
          created_at: "2026-11-06T12:00:00Z",
          updated_at: "2026-11-06T12:01:00Z",
        },
      ],
    };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockDetail), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await getTask("task-456");
    expect(result.id).toBe("task-456");
    expect(result.subtasks).toHaveLength(1);
  });

  it("aborts active task and triggers compensations", async () => {
    const mockAbort = {
      task_id: "task-789",
      status: "cancelled",
      compensations_triggered: 2,
      executed: 2,
      failed: 0,
      notifications: [],
    };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockAbort), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await abortTask("task-789");
    expect(result.status).toBe("cancelled");
    expect(result.compensations_triggered).toBe(2);
  });

  it("submits task with session_id included in body", async () => {
    const mockResponse = {
      task_id: "task-sess-1",
      status: "executing",
      idempotency_key: "key-sess-1",
      created_at: "2026-11-06T12:00:00Z",
      is_new: true,
    };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockResponse), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await submitTask({
      instruction: "Analyze voice task",
      session_id: "session-uuid-123",
    });

    const calledInit = fetchSpy.mock.calls[0][1] as RequestInit;
    const parsedBody = JSON.parse(calledInit.body as string);
    expect(parsedBody.session_id).toBe("session-uuid-123");
  });

  it("extracts rich detail message from API error response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Database constraint violated" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(submitTask({ instruction: "Will fail" })).rejects.toThrow(
      "The API answered 500 (Database constraint violated).",
    );
  });
});
