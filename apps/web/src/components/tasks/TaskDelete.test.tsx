import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../lib/api-client";
import * as authSession from "../../lib/auth-session";
import * as tasksClient from "../../lib/tasks-client";
import { TaskCenterView } from "./TaskCenterView";

function item(id: string, status: string): tasksClient.TaskListItem {
  return {
    id,
    workspace_id: "ws-1",
    title: `Task ${id}`,
    instruction: `Do ${id}`,
    status,
    plan_status: status === "completed" ? "completed" : "executing",
    budget_usd: 1,
    spent_usd: 0.1,
    created_at: "2026-11-07T10:00:00Z",
    subtask_count: 0,
  };
}

const detail = (id: string): tasksClient.TaskDetailResponse => ({
  id,
  workspace_id: "ws-1",
  user_id: "u-1",
  session_id: "s-1",
  title: `Task ${id}`,
  instruction: `Do ${id}`,
  status: "completed",
  plan_status: "completed",
  budget_usd: 1,
  spent_usd: 0.1,
  tool_turns: 0,
  created_at: "2026-11-07T10:00:00Z",
  updated_at: "2026-11-07T10:00:00Z",
  active_plan: [],
  subtasks: [],
});

describe("deleting tasks from the Tasks page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(authSession, "getAccessToken").mockReturnValue("t");
    vi.spyOn(tasksClient, "listTasks").mockResolvedValue({
      items: [
        item("a", "completed"),
        item("b", "failed"),
        item("c", "executing"),
      ],
      total: 3,
      limit: 50,
      offset: 0,
    });
    vi.spyOn(tasksClient, "getTask").mockImplementation((id) =>
      Promise.resolve(detail(id)),
    );
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(new ReadableStream(), {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      ),
    );
  });

  it("only finished tasks can be selected; a running one says to cancel it first", async () => {
    render(<TaskCenterView />);
    const running = await screen.findByRole("checkbox", {
      name: "Select Task c",
    });
    expect(running).toBeDisabled();
    expect(running).toHaveAttribute("title", "Cancel the task first");
    expect(
      screen.getByRole("checkbox", { name: "Select Task a" }),
    ).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Delete Task c" })).toBeNull();
  });

  it("selecting several shows the bar, confirms what is removed and kept, then deletes them", async () => {
    const bulk = vi.spyOn(tasksClient, "deleteTasks").mockResolvedValue({
      deleted: ["a", "b"],
      notFound: [],
      notFinished: [],
    });
    render(<TaskCenterView />);
    fireEvent.click(
      await screen.findByRole("checkbox", { name: "Select Task a" }),
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Task b" }));
    const bar = screen.getByTestId("selection-bar");
    expect(bar).toHaveTextContent("2 selected");
    fireEvent.click(within(bar).getByRole("button", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog", {
      name: "Delete 2 tasks?",
    });
    expect(within(dialog).getByTestId("delete-removes")).toHaveTextContent(
      "files MAYA made",
    );
    expect(within(dialog).getByTestId("delete-keeps")).toHaveTextContent(
      "audit log",
    );
    expect(within(dialog).getByTestId("delete-keeps")).toHaveTextContent(
      "working folder",
    );
    expect(
      within(dialog).getByText("This cannot be undone."),
    ).toBeInTheDocument();

    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete 2 tasks" }),
    );
    await waitFor(() => expect(bulk).toHaveBeenCalledWith(["a", "b"]));
    await waitFor(() => expect(screen.queryByText("Task a")).toBeNull());
    expect(screen.queryByText("Task b")).toBeNull();
    expect(screen.getByText("Task c")).toBeInTheDocument();
    expect(screen.queryByTestId("selection-bar")).toBeNull();
  });

  it("deletes one task from its row and keeps the dialog open with a message on failure", async () => {
    const single = vi
      .spyOn(tasksClient, "deleteTask")
      .mockRejectedValueOnce(new ApiError(500, "server_error"))
      .mockResolvedValueOnce(undefined);
    render(<TaskCenterView />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Delete Task a" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Delete this task?",
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete task" }),
    );
    expect(await within(dialog).findByRole("alert")).toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete task" }),
    );
    await waitFor(() => expect(screen.queryByText("Task a")).toBeNull());
    expect(single).toHaveBeenCalledTimes(2);
  });

  it("shows the empty state once the last task is deleted", async () => {
    vi.spyOn(tasksClient, "listTasks").mockResolvedValue({
      items: [item("a", "completed")],
      total: 1,
      limit: 50,
      offset: 0,
    });
    vi.spyOn(tasksClient, "deleteTask").mockResolvedValue(undefined);
    render(<TaskCenterView />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Delete Task a" }),
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete task" }),
    );
    expect(await screen.findByText("No tasks found.")).toBeInTheDocument();
  });
});
