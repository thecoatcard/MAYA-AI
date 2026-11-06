import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TaskCenterDrawer } from "./TaskCenterDrawer";
import * as tasksClient from "../../lib/tasks-client";
import * as authSession from "../../lib/auth-session";

describe("TaskCenterDrawer component", () => {
  const mockTask: tasksClient.TaskDetailResponse = {
    id: "task-001",
    workspace_id: "ws-001",
    user_id: "u-001",
    session_id: "s-001",
    title: "Financial Audit Report",
    instruction: "Analyze Q3 revenue statements and flag anomalies",
    idempotency_key: "audit-q3",
    status: "executing",
    plan_status: "executing",
    budget_usd: 1.5,
    spent_usd: 0.25,
    tool_turns: 3,
    created_at: "2026-11-06T12:00:00Z",
    updated_at: "2026-11-06T12:05:00Z",
    active_plan: [],
    subtasks: [
      {
        id: "st-1",
        task_id: "task-001",
        assigned_agent: "research",
        description: "Extract revenue line items",
        dependencies: [],
        status: "completed",
        input_data: {},
        output_result: { revenue_items: 45 },
        retry_count: 0,
        created_at: "2026-11-06T12:00:00Z",
        updated_at: "2026-11-06T12:02:00Z",
      },
      {
        id: "st-2",
        task_id: "task-001",
        assigned_agent: "coding",
        description: "Run statistical anomaly detector",
        dependencies: ["st-1"],
        status: "in_progress",
        input_data: {},
        retry_count: 0,
        created_at: "2026-11-06T12:02:00Z",
        updated_at: "2026-11-06T12:03:00Z",
      },
      {
        id: "st-3",
        task_id: "task-001",
        assigned_agent: "document",
        description: "Generate PDF summary deck",
        dependencies: ["st-2"],
        status: "pending",
        input_data: {},
        retry_count: 0,
        created_at: "2026-11-06T12:03:00Z",
        updated_at: "2026-11-06T12:03:00Z",
      },
    ],
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(authSession, "getAccessToken").mockReturnValue(
      "example-test-token",
    );
    vi.spyOn(authSession, "refreshSession").mockResolvedValue({
      accessToken: "example-test-token",
      expiresIn: 3600,
    });
    vi.spyOn(tasksClient, "getTask").mockResolvedValue(mockTask);
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(new ReadableStream(), {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      ),
    );
  });

  it("does not render when isOpen is false", () => {
    const { container } = render(
      <TaskCenterDrawer taskId="task-001" isOpen={false} onClose={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders task title, status badge, and subtasks when open", async () => {
    render(
      <TaskCenterDrawer taskId="task-001" isOpen={true} onClose={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByText("Financial Audit Report")).toBeInTheDocument();
    });

    expect(screen.getByTestId("task-status-badge")).toHaveTextContent(
      "executing",
    );
    expect(screen.getByTestId("task-progress-summary")).toHaveTextContent(
      "Step 2 of 3 — Run statistical anomaly detector",
    );

    // Verify subtasks
    expect(screen.getByText("Extract revenue line items")).toBeInTheDocument();
    expect(
      screen.getByText("Run statistical anomaly detector"),
    ).toBeInTheDocument();
    expect(screen.getByText("Generate PDF summary deck")).toBeInTheDocument();

    // Verify status indicators
    expect(screen.getByTestId("status-icon-completed")).toBeInTheDocument();
    expect(screen.getByTestId("status-icon-in-progress")).toBeInTheDocument();
    expect(screen.getByTestId("status-icon-pending")).toBeInTheDocument();
  });

  it("triggers abort action and displays compensation summary", async () => {
    const abortSpy = vi.spyOn(tasksClient, "abortTask").mockResolvedValueOnce({
      task_id: "task-001",
      status: "cancelled",
      compensations_triggered: 2,
      executed: 2,
      failed: 0,
      notifications: ["Cleaned up remote artifacts"],
    });

    const onAborted = vi.fn();

    render(
      <TaskCenterDrawer
        taskId="task-001"
        isOpen={true}
        onClose={vi.fn()}
        onTaskAborted={onAborted}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("task-abort-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("task-abort-button"));

    await waitFor(() => {
      expect(abortSpy).toHaveBeenCalledWith("task-001");
      expect(onAborted).toHaveBeenCalled();
    });

    expect(screen.getByTestId("abort-result-banner")).toBeInTheDocument();
    expect(
      screen.getByText(/Triggered 2 compensation actions/),
    ).toBeInTheDocument();
    expect(screen.getByText("Cleaned up remote artifacts")).toBeInTheDocument();
  });

  it("calls onClose when close button is clicked or ESC is pressed", async () => {
    const onClose = vi.fn();

    render(
      <TaskCenterDrawer taskId="task-001" isOpen={true} onClose={onClose} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("drawer-close-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("drawer-close-button"));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
