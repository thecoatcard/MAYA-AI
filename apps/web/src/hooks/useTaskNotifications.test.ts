import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listTasks, type TaskListItem } from "../lib/tasks-client";
import { toastForTask, useTaskNotifications } from "./useTaskNotifications";

vi.mock("../lib/tasks-client", () => ({ listTasks: vi.fn() }));

function task(id: string, status: string, title = "Solar deck"): TaskListItem {
  return {
    id,
    workspace_id: "w",
    title,
    instruction: "x",
    status,
    plan_status: "executing",
    budget_usd: 1,
    spent_usd: 0,
    created_at: "2026-01-01T00:00:00Z",
    subtask_count: 1,
  };
}

function reply(...items: TaskListItem[]): void {
  vi.mocked(listTasks).mockResolvedValue({
    items,
    total: items.length,
    limit: 20,
    offset: 0,
  });
}

async function tick(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("useTaskNotifications", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("toasts and counts a task that finished while voice is off, not old ones", async () => {
    const notify = vi.fn(() => "id");
    reply(task("a", "completed"), task("b", "executing"));
    const { result } = renderHook(() =>
      useTaskNotifications({
        enabled: true,
        voiceLive: false,
        notify,
        pollIntervalMs: 1000,
      }),
    );
    await tick(0);
    expect(notify).not.toHaveBeenCalled(); // first poll only records state
    reply(task("a", "completed"), task("b", "completed"));
    await tick(1000);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith(
      expect.objectContaining({ tone: "success", message: "Solar deck" }),
    );
    expect(result.current.unseen).toBe(1);
    await tick(1000); // same state: no duplicate
    expect(notify).toHaveBeenCalledTimes(1);
    act(() => result.current.clear());
    expect(result.current.unseen).toBe(0);
  });

  it("points to the Approve button on the card", async () => {
    const notify = vi.fn(() => "id");
    reply(task("a", "executing"));
    renderHook(() =>
      useTaskNotifications({
        enabled: true,
        voiceLive: false,
        notify,
        pollIntervalMs: 1000,
      }),
    );
    await tick(0);
    reply(task("a", "awaiting_approval"));
    await tick(1000);
    expect(notify).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining("Approve button"),
      }),
    );
  });

  it("stays silent while live voice is on (MAYA speaks it)", async () => {
    const notify = vi.fn(() => "id");
    reply(task("a", "executing"));
    const { result } = renderHook(() =>
      useTaskNotifications({
        enabled: true,
        voiceLive: true,
        notify,
        pollIntervalMs: 1000,
      }),
    );
    await tick(0);
    reply(task("a", "failed"));
    await tick(1000);
    expect(notify).not.toHaveBeenCalled();
    expect(result.current.unseen).toBe(0);
  });

  it("does not poll when disabled", async () => {
    const notify = vi.fn(() => "id");
    renderHook(() =>
      useTaskNotifications({ enabled: false, voiceLive: false, notify }),
    );
    await tick(20_000);
    expect(listTasks).not.toHaveBeenCalled();
  });

  it("builds a plain toast per milestone", () => {
    expect(toastForTask(task("a", "failed"), "failed").tone).toBe("danger");
    expect(toastForTask(task("a", "cancelled"), "cancelled").title).toBe(
      "Task cancelled",
    );
  });
});
