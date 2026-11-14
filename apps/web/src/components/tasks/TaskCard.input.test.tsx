import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
const answerTaskInput = vi.fn();
vi.mock("../../lib/tasks-client", () => ({
  abortTask: vi.fn(),
  submitTask: vi.fn(),
  answerTaskInput: (...a: unknown[]) => answerTaskInput(...a),
}));
const listApprovals = vi.fn();
vi.mock("../../lib/approvals-client", () => ({
  listApprovals: (...a: unknown[]) => listApprovals(...a),
  decideApproval: vi.fn(),
}));

import { TaskCard } from "./TaskCard";

// The owner's wedged task: awaiting_approval, no approval row, a step with a needs_input marker.
function pausedTask(): TaskDetailResponse {
  return {
    id: "t1",
    title: "Java streams notes to Notion",
    instruction: "x",
    status: "awaiting_approval",
    subtasks: [
      {
        id: "s9",
        status: "awaiting_approval",
        description: "Create the Notion page from the finished book",
        input_data: {
          needs_input: {
            kind: "notion_parent",
            ask_id: "ask_1",
            offer_default: true,
            options: {
              "Ai NOTES (Suggested)": "40958955-61ca-80bf-98b4-d3110b2bdb64",
              Projects: "11111111-2222-3333-4444-555555555555",
            },
          },
        },
      },
    ],
  } as unknown as TaskDetailResponse;
}

describe("TaskCard question of a paused step", () => {
  beforeEach(() => {
    refetchTask.mockReset();
    listApprovals.mockReset();
    answerTaskInput.mockReset();
    detail = pausedTask();
  });

  it("shows the question with the real options, never the approval lookup", async () => {
    render(<TaskCard taskId="t1" />);
    expect(await screen.findByTestId("task-input")).toBeTruthy();
    expect(screen.getByText("Ai NOTES (Suggested)")).toBeTruthy();
    expect(screen.getByText("Projects")).toBeTruthy();
    expect(screen.queryByText(/No pending approval request/)).toBeNull();
    expect(listApprovals).not.toHaveBeenCalled();
  });

  it("sends the answer for that step and re-reads the task", async () => {
    answerTaskInput.mockResolvedValue({ message: "Continuing" });
    render(<TaskCard taskId="t1" />);
    fireEvent.click(await screen.findByText("Projects"));
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));
    await waitFor(() => expect(answerTaskInput).toHaveBeenCalledTimes(1));
    const call = answerTaskInput.mock.calls[0] as unknown[];
    expect(call[0]).toBe("t1");
    expect(call[1]).toBe("s9");
    expect(JSON.stringify(call[2])).toContain("Projects");
    await waitFor(() => expect(refetchTask).toHaveBeenCalled());
  });
});
