import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TaskCenterView } from "./TaskCenterView";
import * as tasksClient from "../../lib/tasks-client";
import * as authSession from "../../lib/auth-session";
import * as approvalsClient from "../../lib/approvals-client";

describe("TaskCenterView", () => {
  const mockTasksList: tasksClient.TaskListItem[] = [
    {
      id: "t-1001",
      workspace_id: "ws-1",
      title: "Quarterly Audit",
      instruction: "Audit Q3 revenue accounts and detect anomalies",
      status: "executing",
      plan_status: "executing",
      budget_usd: 5.0,
      spent_usd: 1.25,
      created_at: "2026-11-07T10:00:00Z",
      subtask_count: 2,
    },
    {
      id: "t-1002",
      workspace_id: "ws-1",
      title: "Bug Fix & PR",
      instruction: "Fix race condition in session checkout",
      status: "completed",
      plan_status: "completed",
      budget_usd: 3.0,
      spent_usd: 2.1,
      created_at: "2026-11-07T11:00:00Z",
      subtask_count: 1,
    },
  ];

  const mockDetail: tasksClient.TaskDetailResponse = {
    id: "t-1001",
    workspace_id: "ws-1",
    user_id: "u-1",
    session_id: "s-1",
    title: "Quarterly Audit",
    instruction: "Audit Q3 revenue accounts and detect anomalies",
    status: "executing",
    plan_status: "executing",
    budget_usd: 5.0,
    spent_usd: 1.25,
    tool_turns: 4,
    created_at: "2026-11-07T10:00:00Z",
    updated_at: "2026-11-07T10:05:00Z",
    active_plan: [],
    subtasks: [
      {
        id: "st-1",
        task_id: "t-1001",
        assigned_agent: "research",
        description: "Fetch bank statements",
        dependencies: [],
        status: "completed",
        input_data: {},
        verification_proof: { verified: true },
        retry_count: 0,
        created_at: "2026-11-07T10:00:00Z",
        updated_at: "2026-11-07T10:02:00Z",
      },
      {
        id: "st-2",
        task_id: "t-1001",
        assigned_agent: "coding",
        description: "Run anomaly algorithm",
        dependencies: ["st-1"],
        status: "in_progress",
        input_data: {},
        retry_count: 0,
        created_at: "2026-11-07T10:02:00Z",
        updated_at: "2026-11-07T10:04:00Z",
      },
    ],
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(authSession, "getAccessToken").mockReturnValue("test-auth-token");
    vi.spyOn(tasksClient, "listTasks").mockResolvedValue({
      items: mockTasksList,
      total: 2,
      limit: 50,
      offset: 0,
    });
    vi.spyOn(tasksClient, "getTask").mockResolvedValue(mockDetail);
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(new ReadableStream(), {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      ),
    );
  });

  it("renders Task Ledger list and loads selected task detail", async () => {
    render(<TaskCenterView />);

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Tasks" }),
      ).toBeInTheDocument();
      expect(
        screen.getAllByText("Quarterly Audit").length,
      ).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("Bug Fix & PR")).toBeInTheDocument();
    });

    // Subtasks DAG should be displayed
    await waitFor(() => {
      expect(
        screen.getAllByText("Fetch bank statements").length,
      ).toBeGreaterThanOrEqual(1);
      expect(
        screen.getAllByText("Run anomaly algorithm").length,
      ).toBeGreaterThanOrEqual(1);
    });

    expect(screen.getByText("Cancel task")).toBeInTheDocument();
  });

  it("triggers abort action and displays saga rollback metrics", async () => {
    vi.spyOn(tasksClient, "abortTask").mockResolvedValueOnce({
      task_id: "t-1001",
      status: "cancelled",
      compensations_triggered: 3,
      executed: 3,
      failed: 0,
      notifications: ["Cleaned up sandbox environment"],
    });

    render(<TaskCenterView />);

    await waitFor(() => {
      expect(screen.getByText("Cancel task")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Cancel task"));

    await waitFor(() => {
      expect(tasksClient.abortTask).toHaveBeenCalledWith("t-1001");
      expect(screen.getByText("Cleanup after cancelling")).toBeInTheDocument();
      expect(
        screen.getByText(/3 cleanup actions started, 3 finished, 0 failed/),
      ).toBeInTheDocument();
    });
  });

  it("opens NewTaskModal on clicking New Task button", async () => {
    render(<TaskCenterView />);

    await waitFor(() => {
      expect(screen.getByText("New Task")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("New Task"));

    await waitFor(() => {
      expect(screen.getByText("Assign Autonomous Task")).toBeInTheDocument();
    });
  });

  it("shows a failed list request as an error with retry, never as an empty list", async () => {
    vi.spyOn(tasksClient, "listTasks").mockRejectedValue(
      new Error("Service unavailable"),
    );
    render(<TaskCenterView />);

    expect(
      await screen.findByText("Tasks could not be loaded"),
    ).toBeInTheDocument();
    expect(screen.getByText("Service unavailable")).toBeInTheDocument();
    expect(screen.queryByText("No tasks found.")).toBeNull();

    vi.spyOn(tasksClient, "listTasks").mockResolvedValue({
      items: mockTasksList,
      total: 2,
      limit: 50,
      offset: 0,
    });
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText("Bug Fix & PR")).toBeInTheDocument();
  });

  it("states untracked cost honestly and never as $0.0000", async () => {
    vi.spyOn(tasksClient, "listTasks").mockResolvedValue({
      items: [{ ...mockTasksList[0], spent_usd: 0 }],
      total: 1,
      limit: 50,
      offset: 0,
    });
    vi.spyOn(tasksClient, "getTask").mockResolvedValue({
      ...mockDetail,
      spent_usd: 0,
    });
    render(<TaskCenterView />);

    await waitFor(() => {
      expect(screen.getAllByText("Cost not tracked").length).toBeGreaterThan(1);
    });
    expect(screen.queryByText(/\$0\.0000/)).toBeNull();
  });

  it("shows the real total and loads the next page", async () => {
    const page = (start: number, n: number): tasksClient.TaskListItem[] =>
      Array.from({ length: n }, (_, i) => ({
        ...mockTasksList[0],
        id: `t-${start + i}`,
        title: `Task number ${start + i}`,
      }));
    const listTasks = vi
      .spyOn(tasksClient, "listTasks")
      .mockResolvedValueOnce({
        items: page(0, 50),
        total: 120,
        limit: 50,
        offset: 0,
      })
      .mockResolvedValueOnce({
        items: page(50, 50),
        total: 120,
        limit: 50,
        offset: 50,
      });
    render(<TaskCenterView />);

    expect(
      await screen.findByText("Showing 50 of 120 tasks"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(
      await screen.findByText("Showing 100 of 120 tasks"),
    ).toBeInTheDocument();
    expect(listTasks).toHaveBeenLastCalledWith(undefined, 50, 50);
  });

  it("explains a failed task in plain words with the technical text in details", async () => {
    vi.spyOn(tasksClient, "getTask").mockResolvedValue({
      ...mockDetail,
      status: "failed",
      subtasks: [
        {
          ...mockDetail.subtasks[1],
          status: "failed",
          error_message:
            "Tool 'web_search' denied: Tool 'web_search' missing required arguments: ['query']",
          retry_count: 1,
        },
      ],
    });
    render(<TaskCenterView />);

    expect(
      (
        await screen.findAllByText(
          'The "web_search" step was missing a required input (query).',
        )
      ).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Technical details").length).toBeGreaterThan(0);
  });

  it("approves a waiting task right on the Tasks page, with no link to an Approvals page", async () => {
    vi.spyOn(tasksClient, "getTask").mockResolvedValue({
      ...mockDetail,
      status: "awaiting_approval",
      plan_status: "paused",
    });
    vi.spyOn(approvalsClient, "listApprovals").mockResolvedValue([
      {
        id: "ap-1",
        task_id: "t-1001",
        tool_name: "notion_create_page",
        tier: "APPROVAL_REQUIRED",
        status: "pending",
        action_description: "Notion: create the page 'Java Notes'",
        tool_args: {},
        payload_hash: "h",
        requested_at: "2026-11-07T10:00:00Z",
        expires_at: "2099-11-07T10:30:00Z",
      } as unknown as approvalsClient.ApprovalRequest,
    ]);
    const decide = vi
      .spyOn(approvalsClient, "decideApproval")
      .mockResolvedValue({ id: "ap-1", status: "approved" });
    const onApprovalDecided = vi.fn();
    render(<TaskCenterView onApprovalDecided={onApprovalDecided} />);

    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(onApprovalDecided).toHaveBeenCalledTimes(1));
    expect(decide).toHaveBeenCalledTimes(1);
    expect(decide).toHaveBeenCalledWith("ap-1", { action: "approve" });
    expect(screen.queryByRole("link", { name: /Review Approval/ })).toBeNull();
    // the task is read again after the decision
    await waitFor(() =>
      expect(vi.mocked(tasksClient.getTask).mock.calls.length).toBeGreaterThan(
        1,
      ),
    );
  });

  it("retries a failed task with an idempotency key and disables retry while running", async () => {
    const failedTask: tasksClient.TaskListItem = {
      ...mockTasksList[0],
      id: "t-failed-1",
      title: "Failed Data Job",
      status: "failed",
      instruction: "Process failed data",
    };
    vi.spyOn(tasksClient, "listTasks").mockResolvedValue({
      items: [failedTask],
      total: 1,
      limit: 50,
      offset: 0,
    });
    const submitSpy = vi.spyOn(tasksClient, "submitTask").mockResolvedValue({
      task_id: "t-new",
      status: "pending",
      idempotency_key: "task-key",
      created_at: "2026-11-07T12:00:00Z",
      is_new: true,
    });

    render(<TaskCenterView />);

    await waitFor(() => {
      expect(screen.getByText("Failed Data Job")).toBeInTheDocument();
    });

    const menuButton = screen.getByRole("button", {
      name: "More actions for Failed Data Job",
    });
    fireEvent.click(menuButton);

    const retryItem = await screen.findByRole("menuitem", { name: "Retry" });
    expect(retryItem).toBeInTheDocument();
    fireEvent.click(retryItem);

    await waitFor(() => {
      expect(submitSpy).toHaveBeenCalledTimes(1);
    });
    expect(submitSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        instruction: "Process failed data",
        title: "Failed Data Job",
        idempotency_key: expect.stringMatching(/^task-\d+-[a-z0-9]+$/),
      }),
    );
  });
});
