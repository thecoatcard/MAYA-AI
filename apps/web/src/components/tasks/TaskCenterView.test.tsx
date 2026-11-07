import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TaskCenterView } from "./TaskCenterView";
import * as tasksClient from "../../lib/tasks-client";
import * as authSession from "../../lib/auth-session";

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
      expect(screen.getByText("Task Ledger")).toBeInTheDocument();
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

    expect(screen.getByText("Abort Task & Sagas")).toBeInTheDocument();
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
      expect(screen.getByText("Abort Task & Sagas")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Abort Task & Sagas"));

    await waitFor(() => {
      expect(tasksClient.abortTask).toHaveBeenCalledWith("t-1001");
      expect(
        screen.getByText("Saga Compensation Rollback Executed"),
      ).toBeInTheDocument();
      expect(screen.getByText("3 compensations")).toBeInTheDocument();
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
});
