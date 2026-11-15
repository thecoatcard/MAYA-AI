import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskDetailResponse } from "../../lib/tasks-client";

let detail: TaskDetailResponse | null = null;
let gone = false;
vi.mock("../../hooks/useTaskEvents", () => ({
  useTaskEvents: () => ({
    taskDetail: detail,
    error: null,
    lastEvent: null,
    events: [],
    refetchTask: vi.fn(),
    taskGone: gone,
  }),
}));
vi.mock("./TaskFiles", () => ({ TaskFiles: () => null }));
const deleteTask = vi.fn();
vi.mock("../../lib/tasks-client", () => ({
  abortTask: vi.fn(),
  submitTask: vi.fn(),
  deleteTask: (id: string) => deleteTask(id),
}));
vi.mock("../../lib/approvals-client", () => ({
  listApprovals: async () => [],
  decideApproval: vi.fn(),
}));

import { TaskCard } from "./TaskCard";

function task(status: string): TaskDetailResponse {
  return {
    id: "t1",
    title: "Quarterly report",
    instruction: "make report",
    status,
    plan_status: status,
    budget_usd: 1,
    spent_usd: 0,
    tool_turns: 0,
    created_at: "2026-11-07T10:00:00Z",
    updated_at: "2026-11-07T10:00:00Z",
    active_plan: [],
    subtasks: [],
  } as unknown as TaskDetailResponse;
}

describe("TaskCard delete", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    gone = false;
  });

  it("a card for a task the server no longer has reads as deleted, quietly", () => {
    detail = null;
    gone = true;
    render(<TaskCard taskId="t1" fallbackTitle="Quarterly report" />);
    expect(screen.getByTestId("task-card-deleted")).toHaveTextContent(
      "This task was deleted.",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a running task cannot be deleted from the menu: it says to cancel first", () => {
    detail = task("executing");
    render(<TaskCard taskId="t1" />);
    fireEvent.click(screen.getByRole("button", { name: "More task actions" }));
    // The menu now has more items (copy, cancel, ...): pick Delete by name.
    const item = screen.getByRole("menuitem", { name: /Delete task/ });
    expect(item).toHaveAttribute("aria-disabled", "true");
    expect(item).toHaveTextContent("Cancel the task first");
    fireEvent.click(item);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(deleteTask).not.toHaveBeenCalled();
  });

  it("a finished task is deleted after confirming, then the card reads as deleted", async () => {
    detail = task("completed");
    deleteTask.mockResolvedValue(undefined);
    const onDeleted = vi.fn();
    render(<TaskCard taskId="t1" onDeleted={onDeleted} />);
    fireEvent.click(screen.getByRole("button", { name: "More task actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Delete task/ }));
    const dialog = await screen.findByRole("dialog", {
      name: "Delete this task?",
    });
    expect(within(dialog).getByTestId("delete-keeps")).toHaveTextContent(
      "audit log",
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete task" }),
    );
    await waitFor(() => expect(deleteTask).toHaveBeenCalledWith("t1"));
    expect(await screen.findByTestId("task-card-deleted")).toBeInTheDocument();
    expect(onDeleted).toHaveBeenCalledWith("t1");
  });
});
