import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listMessages = vi.fn();
vi.mock("../../lib/api-client", () => ({
  listMessages: (...args: unknown[]) => listMessages(...args),
}));
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
  }: {
    href: string;
    children: React.ReactNode;
  }) => <a href={href}>{children}</a>,
}));

import { TaskResultSection } from "./TaskResultSection";

const task = { id: "t1", session_id: "s1", partial: false };

function resultMessage(
  status: string,
  content: string,
  artifacts: string[] = [],
) {
  return {
    id: "m1",
    role: "assistant",
    content,
    created_at: "2026-11-13T00:00:00Z",
    metadata: {
      task_result: { task_id: "t1", status, artifact_ids: artifacts },
    },
  };
}

describe("TaskResultSection", () => {
  beforeEach(() => listMessages.mockReset());

  it("shows the chat result text, the state and the files for a finished task", async () => {
    listMessages.mockResolvedValue({
      items: [
        resultMessage("partial", "Read 12 sources from 9 sites.", ["a1"]),
      ],
      next_cursor: null,
    });
    render(<TaskResultSection task={task} finished />);
    await waitFor(() =>
      expect(screen.getByTestId("task-result-section")).toBeTruthy(),
    );
    expect(screen.getByText(/Read 12 sources from 9 sites/)).toBeTruthy();
    expect(screen.getAllByText("Completed with errors").length).toBeGreaterThan(
      0,
    );
    expect(screen.getByText("Open Artifacts").getAttribute("href")).toBe(
      "/artifacts",
    );
  });

  it("shows nothing while the task is running or has no result message", async () => {
    render(<TaskResultSection task={task} finished={false} />);
    expect(screen.queryByTestId("task-result-section")).toBeNull();
    expect(listMessages).not.toHaveBeenCalled();

    listMessages.mockResolvedValue({ items: [], next_cursor: null });
    render(<TaskResultSection task={task} finished />);
    await waitFor(() => expect(listMessages).toHaveBeenCalled());
    expect(screen.queryByTestId("task-result-section")).toBeNull();
  });
});
