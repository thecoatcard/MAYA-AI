import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "../../hooks/useAuth";
import { TASK_NOTIFY_POLL_MS } from "../../hooks/useTaskNotifications";
import { checkBackendReady } from "../../lib/api-client";
import { listApprovals } from "../../lib/approvals-client";
import { listTasks, type TaskListItem } from "../../lib/tasks-client";
import { AppShell } from "./AppShell";
import { makeAuth } from "./layout-test-utils";
import { WorkspaceProvider } from "./WorkspaceProvider";

let mockPathname = "/memory";

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("../../hooks/useAuth", () => ({ useAuth: vi.fn() }));
vi.mock("../../lib/api-client", () => ({ checkBackendReady: vi.fn() }));
vi.mock("../../lib/approvals-client", () => ({ listApprovals: vi.fn() }));
vi.mock("../../lib/tasks-client", () => ({ listTasks: vi.fn() }));

function task(status: string): TaskListItem {
  return {
    id: "a",
    workspace_id: "w",
    title: "Solar deck",
    instruction: "x",
    status,
    plan_status: "executing",
    budget_usd: 1,
    spent_usd: 0,
    created_at: "2026-01-01T00:00:00Z",
    subtask_count: 1,
  };
}

function reply(status: string): void {
  vi.mocked(listTasks).mockResolvedValue({
    items: [task(status)],
    total: 1,
    limit: 20,
    offset: 0,
  });
}

function shell(): React.JSX.Element {
  return (
    <WorkspaceProvider>
      <AppShell>
        <p>page body</p>
      </AppShell>
    </WorkspaceProvider>
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  mockPathname = "/memory";
  vi.mocked(useAuth).mockReturnValue(makeAuth());
  vi.mocked(checkBackendReady).mockResolvedValue({ ready: true });
  vi.mocked(listApprovals).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("task updates while voice is off", () => {
  it("toasts, badges the Tasks entry and clears the badge on the Tasks page", async () => {
    reply("executing");
    const view = render(shell());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByTestId("task-updates-count")).toBeNull();

    reply("completed");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(TASK_NOTIFY_POLL_MS);
    });
    expect(screen.getByText("Task done")).toBeInTheDocument();
    expect(screen.getAllByTestId("task-updates-count")[0]).toHaveTextContent(
      "1",
    );

    mockPathname = "/tasks";
    view.rerender(shell());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByTestId("task-updates-count")).toBeNull();
  });
});
