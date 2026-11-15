import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApprovalRequest } from "../../lib/approvals-client";
import type { TaskDetailResponse } from "../../lib/tasks-client";

let detail: TaskDetailResponse | null = null;
const refetchTask = vi.fn();
vi.mock("../../hooks/useTaskEvents", () => ({
  useTaskEvents: () => ({
    taskDetail: detail,
    error: null,
    lastEvent: null,
    events: [],
    refetchTask,
  }),
}));
vi.mock("./TaskFiles", () => ({ TaskFiles: () => null }));
vi.mock("../../lib/tasks-client", () => ({
  abortTask: vi.fn(),
  submitTask: vi.fn(),
}));
const listApprovals = vi.fn();
const decideApproval = vi.fn();
vi.mock("../../lib/approvals-client", () => ({
  listApprovals: (...a: unknown[]) => listApprovals(...a),
  decideApproval: (...a: unknown[]) => decideApproval(...a),
}));

import { TaskCard } from "./TaskCard";

const pendingApproval = {
  id: "a1",
  task_id: "t1",
  tool_name: "notion_create_page",
  tier: "APPROVAL_REQUIRED",
  status: "pending",
  action_description: "Notion: create the page 'Java Notes' under the page P",
  tool_args: {},
  payload_hash: "h",
  requested_at: "2026-11-11T10:00:00Z",
  expires_at: "2099-11-11T10:30:00Z",
} as unknown as ApprovalRequest;

function task(status: string, subtasks: unknown[] = []): TaskDetailResponse {
  return {
    id: "t1",
    title: "Notion: create the page 'Java Notes'",
    instruction: "x",
    status,
    subtasks,
  } as unknown as TaskDetailResponse;
}

describe("TaskCard inline approval", () => {
  beforeEach(() => {
    refetchTask.mockReset();
    listApprovals.mockReset();
    decideApproval.mockReset();
    detail = task("awaiting_approval");
  });
  afterEach(() => vi.useRealTimers());

  it("shows the approval for this task only, right in the card", async () => {
    listApprovals.mockResolvedValue([
      pendingApproval,
      { ...pendingApproval, id: "other", task_id: "t2" },
    ]);
    render(<TaskCard taskId="t1" />);
    expect(await screen.findAllByTestId("inline-approval")).toHaveLength(1);
    expect(screen.getByText(/create the page 'Java Notes' under/)).toBeTruthy();
    expect(listApprovals).toHaveBeenCalledWith("pending", 50);
  });

  it("Approve decides through the client, refetches the task and tells the caller", async () => {
    listApprovals.mockResolvedValue([pendingApproval]);
    decideApproval.mockResolvedValue({ id: "a1", status: "approved" });
    const onApprovalDecided = vi.fn();
    render(<TaskCard taskId="t1" onApprovalDecided={onApprovalDecided} />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(onApprovalDecided).toHaveBeenCalledWith("t1"));
    expect(decideApproval).toHaveBeenCalledTimes(1);
    expect(refetchTask).toHaveBeenCalled();
  });

  it("moves from waiting to running to done without a reload and shows the result with its link", async () => {
    vi.useFakeTimers();
    listApprovals.mockResolvedValue([pendingApproval]);
    refetchTask.mockResolvedValue(null);
    const view = render(<TaskCard taskId="t1" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("task-status").textContent).toBe(
      "Waiting for your approval",
    );

    detail = task("executing", [
      { id: "s", status: "in_progress", description: "d" },
    ]);
    view.rerender(<TaskCard taskId="t1" />);
    expect(screen.getByTestId("task-status").textContent).toBe("In progress");
    expect(screen.queryByTestId("inline-approval")).toBeNull();

    detail = task("completed", [
      {
        id: "s",
        status: "completed",
        description: "d",
        output_result: {
          tool: "notion_create_page",
          output:
            "Created the Notion page abc. Link: https://www.notion.so/Java-Notes-abc\nVERIFIED by reading it back: 11 blocks match what was sent.",
        },
      },
    ]);
    view.rerender(<TaskCard taskId="t1" />);
    expect(screen.getByTestId("task-status").textContent).toBe("Completed");
    expect(screen.getByTestId("task-result").textContent).toContain(
      "VERIFIED by reading it back",
    );
    const link = screen.getByRole("link", {
      name: "https://www.notion.so/Java-Notes-abc",
    });
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("re-reads the task every 5 seconds while it waits (backup for a missed event)", async () => {
    vi.useFakeTimers();
    listApprovals.mockResolvedValue([]);
    refetchTask.mockResolvedValue(null);
    render(<TaskCard taskId="t1" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(refetchTask).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(refetchTask).toHaveBeenCalledTimes(2);
  });

  it("says plainly when no pending request exists and when loading it failed", async () => {
    listApprovals.mockResolvedValueOnce([]);
    const view = render(<TaskCard taskId="t1" />);
    expect(await screen.findByText(/No pending approval request/)).toBeTruthy();
    view.unmount();
    listApprovals.mockRejectedValueOnce(new Error("Backend unreachable"));
    render(<TaskCard taskId="t1" />);
    expect(await screen.findByText("Backend unreachable")).toBeTruthy();
  });

  it("shows no approval for a task that is not waiting", () => {
    detail = task("executing", [
      { id: "s", status: "in_progress", description: "d" },
    ]);
    render(<TaskCard taskId="t1" />);
    expect(screen.queryByTestId("inline-approval")).toBeNull();
    expect(listApprovals).not.toHaveBeenCalled();
  });
});
