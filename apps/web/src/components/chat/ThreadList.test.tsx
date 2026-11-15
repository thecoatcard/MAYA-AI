import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskDetailResponse } from "../../lib/tasks-client";
import type { ThreadItem } from "./threadItems";

let detail: TaskDetailResponse | null = null;
vi.mock("../../hooks/useTaskEvents", () => ({
  useTaskEvents: () => ({
    taskDetail: detail,
    error: null,
    lastEvent: null,
    refetchTask: vi.fn(),
  }),
}));

import { ThreadList } from "./ThreadList";

const taskItem: ThreadItem = {
  kind: "taskCard",
  id: "task:t1",
  ts: 1,
  taskId: "t1",
  title: "List my repo",
};

function mount(items: ThreadItem[]) {
  return render(<ThreadList items={items} status="idle" />);
}

describe("ThreadList task cards follow the live task state", () => {
  beforeEach(() => {
    detail = null;
  });

  it("shows a failed task as Failed with its reason, never as in progress", () => {
    detail = {
      id: "t1",
      title: "List my repo",
      instruction: "list my repo",
      status: "failed",
      subtasks: [
        {
          id: "s1",
          status: "failed",
          description: "Perform grounded research",
          error_message: "Tool web_search denied: missing required arguments",
        },
      ],
    } as unknown as TaskDetailResponse;
    mount([taskItem]);
    const card = screen.getByTestId("task-card");
    expect(card).toHaveAttribute("data-phase", "failed");
    expect(screen.getByTestId("task-status").textContent).toContain("Failed");
    expect(card.textContent).not.toMatch(/in progress/i);
    expect(screen.getByRole("button", { name: /try again/i })).toBeTruthy();
  });

  it("dismisses a finished task card from the thread without touching the task", () => {
    detail = {
      id: "t1",
      title: "List my repo",
      instruction: "list my repo",
      status: "completed",
      subtasks: [],
    } as unknown as TaskDetailResponse;
    mount([taskItem]);
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByTestId("task-card")).toBeNull();
  });
});

describe("ThreadList pane", () => {
  it("is one named log region and offers no jump button while at the bottom", () => {
    mount([]);
    expect(screen.getByRole("log", { name: "Conversation" })).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /jump to latest/i }),
    ).toBeNull();
  });
});

describe("ThreadList tool-posted voice messages", () => {
  const item: ThreadItem = {
    kind: "voiceTurn",
    id: "m9",
    ts: 1,
    turnId: "chat:m9",
    role: "assistant",
    text: "### Shortcuts\n\n```python\nprint(1)\n```",
    isFinal: true,
    source: "spoken",
    markdown: true,
  };

  it("renders Markdown with a code block, not raw text", () => {
    const { container } = mount([item]);
    expect(container.querySelector("pre")).not.toBeNull();
    expect(container.textContent).toContain("print(1)");
    expect(container.textContent).not.toContain("```");
    expect(container.querySelector("h3, h4")).not.toBeNull();
  });
});
