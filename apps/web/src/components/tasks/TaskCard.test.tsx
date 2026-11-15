import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  TaskDetailResponse,
  TaskSubmissionResponse,
} from "../../lib/tasks-client";

let detail: TaskDetailResponse | null = null;
let lastEvent: { event_type: string } | null = null;
let events: Array<{ event_type: string; payload?: Record<string, unknown> }> =
  [];
const refetchTask = vi.fn();
vi.mock("../../hooks/useTaskEvents", () => ({
  useTaskEvents: () => ({
    taskDetail: detail,
    error: null,
    lastEvent,
    events,
    refetchTask,
  }),
}));
vi.mock("./TaskFiles", () => ({
  TaskFiles: ({ taskId, phase }: { taskId: string; phase: string }) => (
    <i data-testid="task-files" data-task={taskId} data-phase={phase} />
  ),
}));
const abortTask = vi.fn();
const submitTask = vi.fn();
vi.mock("../../lib/tasks-client", () => ({
  abortTask: (id: string) => abortTask(id),
  submitTask: (req: unknown) => submitTask(req),
}));

vi.mock("../../lib/approvals-client", () => ({
  listApprovals: async () => [],
  decideApproval: vi.fn(),
}));

import { TaskCard } from "./TaskCard";

function task(
  status: string,
  subtasks: Array<Record<string, unknown>> = [],
): TaskDetailResponse {
  return {
    id: "t1",
    title: "Quarterly report",
    instruction: "make report",
    status,
    subtasks,
  } as unknown as TaskDetailResponse;
}

describe("TaskCard", () => {
  beforeEach(() => {
    abortTask.mockReset();
    abortTask.mockResolvedValue({});
    submitTask.mockReset();
    refetchTask.mockReset();
    detail = null;
    lastEvent = null;
    events = [];
  });

  it("invents no stages: no subtasks means Planning only", () => {
    detail = task("pending");
    render(<TaskCard taskId="t1" />);
    expect(screen.getByTestId("task-status").textContent).toBe("Planning");
    expect(screen.queryByTestId("task-detail")).toBeNull();
    expect(screen.queryByText(/processing/i)).toBeNull();
  });

  it("shows waiting for approval instead of processing", () => {
    detail = task("awaiting_approval");
    render(<TaskCard taskId="t1" />);
    expect(screen.getByTestId("task-status").textContent).toBe(
      "Waiting for your approval",
    );
  });

  it("explains a failure in plain words and keeps the real error as technical text", () => {
    detail = task("failed", [
      { id: "s", status: "failed", description: "d", error_message: "boom" },
    ]);
    render(<TaskCard taskId="t1" />);
    expect(screen.getByTestId("task-status").textContent).toBe("Failed");
    expect(screen.getByTestId("task-detail").textContent).toBe(
      "A step failed.",
    );
    expect(screen.getByTestId("task-technical").textContent).toBe("boom");
    expect(screen.queryByRole("button", { name: /cancel task/i })).toBeNull();
  });

  it("turns the missing-input tool error into a readable sentence", () => {
    detail = task("failed", [
      {
        id: "s",
        status: "failed",
        description: "d",
        error_message:
          "Tool 'web_search' denied: Tool 'web_search' missing required arguments: ['query']",
      },
    ]);
    render(<TaskCard taskId="t1" />);
    expect(screen.getByTestId("task-detail").textContent).toBe(
      'The "web_search" step was missing a required input (query).',
    );
  });

  it("a terminal liveStatus wins over a stale in-progress detail (no spinner for a failed task)", () => {
    detail = task("in_progress", [
      { id: "s", status: "in_progress", description: "searching" },
    ]);
    render(<TaskCard taskId="t1" liveStatus="failed" />);
    expect(screen.getByTestId("task-card").getAttribute("data-phase")).toBe(
      "failed",
    );
    expect(screen.queryByRole("button", { name: /cancel task/i })).toBeNull();
  });

  it("keeps re-reading an unfinished task and stops once it has finished", () => {
    vi.useFakeTimers();
    try {
      detail = task("in_progress", [
        { id: "s", status: "in_progress", description: "outline" },
      ]);
      const { rerender } = render(<TaskCard taskId="t1" />);
      refetchTask.mockClear();
      vi.advanceTimersByTime(10_500);
      expect(refetchTask).toHaveBeenCalledTimes(2);
      detail = task("completed", [
        { id: "s", status: "completed", description: "outline" },
      ]);
      rerender(<TaskCard taskId="t1" />);
      refetchTask.mockClear();
      vi.advanceTimersByTime(20_000);
      expect(refetchTask).not.toHaveBeenCalled();
      expect(screen.getByTestId("task-files")).toHaveAttribute(
        "data-task",
        "t1",
      );
      expect(screen.queryByRole("button", { name: /cancel task/i })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("refetches the task when a lifecycle event arrives", () => {
    detail = task("in_progress", [
      { id: "s", status: "in_progress", description: "searching" },
    ]);
    lastEvent = { event_type: "task_failed" };
    render(<TaskCard taskId="t1" />);
    expect(refetchTask).toHaveBeenCalled();
  });

  it("offers Try again on a failed task and Dismiss when asked", async () => {
    detail = task("failed", [
      { id: "s", status: "failed", description: "d", error_message: "boom" },
    ]);
    const created: TaskSubmissionResponse = {
      task_id: "t2",
      status: "planning",
      idempotency_key: "k",
      created_at: "now",
      is_new: true,
    };
    submitTask.mockResolvedValue(created);
    const onRetried = vi.fn();
    const onDismiss = vi.fn();
    render(
      <TaskCard
        taskId="t1"

        onRetried={onRetried}
        onDismiss={onDismiss}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(onRetried).toHaveBeenCalledWith("t2"));
    expect(submitTask).toHaveBeenCalledWith({
      instruction: "make report",
      title: "Quarterly report",
    });
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalledWith("t1");
  });

  it("has no Work panel button and cancels through the API", async () => {
    detail = task("in_progress", [
      { id: "s", status: "in_progress", description: "fetching" },
    ]);
    const onAborted = vi.fn();
    render(<TaskCard taskId="t1" onAborted={onAborted} />);
    expect(screen.queryByRole("button", { name: /work panel/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /cancel task/i }));
    await waitFor(() => expect(onAborted).toHaveBeenCalledWith("t1"));
    expect(abortTask).toHaveBeenCalledWith("t1");
  });

  it("a completed task with a failed step reads Completed with errors and names the step", () => {
    detail = task("completed", [
      { id: "a", status: "completed", description: "Outline" },
      {
        id: "b",
        status: "failed",
        description: "Chapter 3: Loops",
        error_message: "Chapter model call failed",
      },
    ]);
    render(<TaskCard taskId="t1" />);
    expect(screen.getByTestId("task-status")).toHaveTextContent(
      "Completed with errors",
    );
    expect(screen.getByTestId("task-status")).toHaveAttribute(
      "data-partial",
      "true",
    );
    expect(screen.getByText(/Failed: Chapter 3: Loops/)).toBeInTheDocument();
  });

  it("lists the task's files in the card once it has finished, not while it runs", () => {
    detail = task("completed", [
      { id: "s", status: "completed", description: "d" },
    ]);
    const { rerender } = render(<TaskCard taskId="t1" />);
    expect(screen.getByTestId("task-files")).toHaveAttribute("data-task", "t1");
    detail = task("in_progress", [
      { id: "s", status: "in_progress", description: "d" },
    ]);
    rerender(<TaskCard taskId="t1" />);
    expect(screen.queryByTestId("task-files")).toBeNull();
  });
});

function progressEvent(
  stepId: string,
  label: string,
  state: string,
  parentId?: string,
): { event_type: string; payload: Record<string, unknown> } {
  return {
    event_type: "task_progress",
    payload: {
      task_id: "t1",
      step: {
        run_id: "t1",
        step_id: stepId,
        kind: "step",
        label,
        state,
        ...(parentId === undefined ? {} : { parent_id: parentId }),
      },
    },
  };
}

function stepStates(): Array<[string, string | null]> {
  return screen
    .getAllByTestId("progress-step")
    .map((item) => [
      item.querySelector(".maya-steps__label")?.textContent ?? "",
      item.getAttribute("data-state"),
    ]);
}

describe("TaskCard progress steps", () => {
  beforeEach(() => {
    detail = null;
    lastEvent = null;
    events = [];
    refetchTask.mockReset();
  });

  it("shows no step list before the task reports a step", () => {
    detail = task("in_progress");
    events = [{ event_type: "connected" }, { event_type: "task_plan_created" }];
    render(<TaskCard taskId="t1" />);
    expect(screen.queryByTestId("progress-steps")).toBeNull();
  });

  it("lists the steps of the task as they arrive and updates a step in place", () => {
    detail = task("in_progress", [
      { id: "s", status: "in_progress", description: "d" },
    ]);
    events = [
      progressEvent("plan-t1", "Planned the work", "done"),
      progressEvent("sub-1", "Research agent", "running"),
      progressEvent("tool-1", "Searching the web", "running", "sub-1"),
    ];
    const { rerender } = render(<TaskCard taskId="t1" />);
    expect(stepStates()).toEqual([
      ["Planned the work", "done"],
      ["Research agent", "running"],
      ["Searching the web", "running"],
    ]);

    events = [
      ...events,
      progressEvent("tool-1", "Searching the web", "done", "sub-1"),
      progressEvent("sub-1", "Research agent", "failed"),
      { event_type: "task_progress", payload: { step: { step_id: "bad" } } },
    ];
    rerender(<TaskCard taskId="t1" />);
    expect(stepStates()).toEqual([
      ["Planned the work", "done"],
      ["Research agent", "failed"],
      ["Searching the web", "done"],
    ]);
  });

  it.each([
    ["cancelled", "skipped", /^Stopped/],
    ["failed", "failed", /^Interrupted/],
    // Wording changed on purpose: a finished work log reads "Worked for ... · N steps".
    ["completed", "done", /^Worked/],
  ])("a %s task leaves no step running", (status, expectedState, summary) => {
    detail = task(status, [{ id: "s", status, description: "d" }]);
    events = [progressEvent("sub-1", "Document agent", "running")];
    render(<TaskCard taskId="t1" />);
    // A finished list is collapsed to its summary; open it to read the step.
    fireEvent.click(screen.getByRole("button", { name: summary }));
    expect(stepStates()).toEqual([["Document agent", expectedState]]);
  });

  it("refetches the task on the event names the task engine publishes", () => {
    detail = task("in_progress");
    lastEvent = { event_type: "subtask_status_changed" };
    render(<TaskCard taskId="t1" />);
    expect(refetchTask).toHaveBeenCalled();
  });

  it("does not refetch the task for a progress step", () => {
    detail = task("in_progress");
    lastEvent = { event_type: "task_progress" };
    render(<TaskCard taskId="t1" />);
    expect(refetchTask).not.toHaveBeenCalled();
  });
  it("reads Waiting for your approval, not In progress, when a step is paused for approval", () => {
    // A stale task status next to a step the engine paused for the user's approval.
    detail = task("in_progress", [
      { id: "s1", description: "Add the row", status: "in_progress" },
    ]);
    events = [
      {
        event_type: "task_progress",
        payload: {
          task_id: "t1",
          step: {
            run_id: "t1",
            step_id: "sub-1",
            kind: "step",
            label: "Pm agent",
            state: "skipped",
            detail: "Waiting for your approval.",
          },
        },
      },
    ];
    render(<TaskCard taskId="t1" />);
    expect(screen.getByTestId("task-status")).toHaveTextContent(
      "Waiting for your approval",
    );
    expect(screen.queryByText("In progress")).toBeNull();
    expect(screen.getByTestId("task-card")).toHaveAttribute(
      "data-phase",
      "awaiting_approval",
    );
  });

  it("goes back to In progress once the paused step runs again", () => {
    detail = task("in_progress", [
      { id: "s1", description: "Add the row", status: "in_progress" },
    ]);
    const paused = {
      event_type: "task_progress",
      payload: {
        step: {
          run_id: "t1",
          step_id: "sub-1",
          kind: "step",
          label: "Pm agent",
          state: "skipped",
          detail: "Waiting for your approval.",
        },
      },
    };
    const running = {
      event_type: "task_progress",
      payload: {
        step: {
          run_id: "t1",
          step_id: "sub-1",
          kind: "step",
          label: "Pm agent",
          state: "running",
        },
      },
    };
    events = [paused, running];
    render(<TaskCard taskId="t1" />);
    expect(screen.getByTestId("task-status")).toHaveTextContent("In progress");
  });
});

describe("TaskCard onFinished", () => {
  it("fires once when the task goes from running to a final state, not for an already finished one", () => {
    const onFinished = vi.fn();
    detail = task("completed");
    const { unmount } = render(
      <TaskCard taskId="t1" onFinished={onFinished} />,
    );
    expect(onFinished).not.toHaveBeenCalled();
    unmount();

    detail = task("executing", [{ id: "s1", status: "in_progress" }]);
    const view = render(<TaskCard taskId="t1" onFinished={onFinished} />);
    expect(onFinished).not.toHaveBeenCalled();
    detail = task("completed");
    view.rerender(<TaskCard taskId="t1" onFinished={onFinished} />);
    view.rerender(<TaskCard taskId="t1" onFinished={onFinished} />);
    expect(onFinished).toHaveBeenCalledTimes(1);
    expect(onFinished).toHaveBeenCalledWith("t1");
  });
});
