import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { TaskDetailResponse } from "../../lib/tasks-client";

let detail: TaskDetailResponse | null = null;
vi.mock("../../hooks/useTaskEvents", () => ({
  useTaskEvents: () => ({
    taskDetail: detail,
    error: null,
    lastEvent: null,
    events: [],
    refetchTask: vi.fn(),
  }),
}));
vi.mock("./TaskFiles", () => ({ TaskFiles: () => null }));
vi.mock("../../lib/tasks-client", () => ({
  abortTask: vi.fn(),
  submitTask: vi.fn(),
}));
vi.mock("../../lib/approvals-client", () => ({
  listApprovals: async () => [],
  decideApproval: vi.fn(),
}));

import { TaskCard } from "./TaskCard";

function failed(message: string): TaskDetailResponse {
  return {
    id: "t1",
    title: "Deck",
    instruction: "make a deck",
    status: "failed",
    subtasks: [
      { id: "s", status: "failed", description: "d", error_message: message },
    ],
  } as unknown as TaskDetailResponse;
}

describe("TaskCard missing-detail question", () => {
  it("shows the question as a card and sends the answer back, then shows it answered", () => {
    detail = failed("I need a little more detail: What is the deck about?");
    const onDetailAnswered = vi.fn();
    render(<TaskCard taskId="t1" onDetailAnswered={onDetailAnswered} />);
    expect(screen.getByText("What is the deck about?")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Your answer"), {
      target: { value: "Solar energy" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Submit/ }));
    expect(onDetailAnswered).toHaveBeenCalledWith(
      "t1",
      "Deck",
      "What is the deck about?",
      "Solar energy",
    );
    expect(screen.getByTestId("ask-user-card").getAttribute("data-state")).toBe(
      "answered",
    );
  });

  it("shows no card for an ordinary failure", () => {
    detail = failed("boom");
    render(<TaskCard taskId="t1" onDetailAnswered={vi.fn()} />);
    expect(screen.queryByTestId("ask-user-card")).toBeNull();
  });
});
