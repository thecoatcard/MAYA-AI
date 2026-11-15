import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useTaskEvents } from "./useTaskEvents";
import * as authSession from "../lib/auth-session";

describe("useTaskEvents hook", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(authSession, "getAccessToken").mockReturnValue(
      "example-test-token",
    );
    vi.spyOn(authSession, "refreshSession").mockResolvedValue({
      accessToken: "example-test-token",
      expiresIn: 3600,
    });
  });

  it("does not connect when taskId is null or enabled is false", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const { result: res1 } = renderHook(() =>
      useTaskEvents({ taskId: null, enabled: true }),
    );
    expect(res1.current.isConnected).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();

    const { result: res2 } = renderHook(() =>
      useTaskEvents({ taskId: "task-123", enabled: false }),
    );
    expect(res2.current.isConnected).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("connects to SSE stream and dispatches events", async () => {
    const mockSseText = [
      'data: {"event_type":"connected","task_id":"task-123"}\n\n',
      'data: {"event_type":"subtask_status_changed","payload":{"subtask_id":"st-1","status":"in_progress"}}\n\n',
      'data: {"event_type":"task_completed","payload":{"task_id":"task-123"}}\n\n',
    ].join("");

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(mockSseText));
        controller.close();
      },
    });

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );

    const onEventMock = vi.fn();

    renderHook(() =>
      useTaskEvents({
        taskId: "task-123",
        enabled: true,
        onEvent: onEventMock,
      }),
    );

    await waitFor(() => {
      expect(onEventMock).toHaveBeenCalled();
    });

    expect(onEventMock).toHaveBeenCalledWith(
      expect.objectContaining({ event_type: "connected" }),
    );
    expect(onEventMock).toHaveBeenCalledWith(
      expect.objectContaining({ event_type: "subtask_status_changed" }),
    );
    expect(onEventMock).toHaveBeenCalledWith(
      expect.objectContaining({ event_type: "task_completed" }),
    );
  });

  it("maps task_cancelled to a cancelled status", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'data: {"event_type":"task_cancelled","task_id":"task-9","payload":{}}\n\n',
          ),
        );
        controller.close();
      },
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );
    const onEvent = vi.fn();
    renderHook(() =>
      useTaskEvents({ taskId: "task-9", enabled: true, onEvent }),
    );
    await waitFor(() => {
      expect(onEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          event_type: "task_cancelled",
          status: "cancelled",
        }),
      );
    });
  });
});

describe("useTaskEvents per-task state", () => {
  it("does not carry one task's detail into the next task", async () => {
    vi.restoreAllMocks();
    vi.spyOn(authSession, "getAccessToken").mockReturnValue(
      "example-test-token",
    );
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("stream down"));
    const tasksClient = await import("../lib/tasks-client");
    vi.spyOn(tasksClient, "getTask").mockImplementation((id: string) =>
      Promise.resolve({
        id,
        title: `title-${id}`,
        status: "executing",
        subtasks: [],
      } as never),
    );

    const { result, rerender } = renderHook(
      ({ id }: { id: string }) =>
        useTaskEvents({ taskId: id, pollIntervalMs: 20 }),
      { initialProps: { id: "task-a" } },
    );
    await waitFor(() => expect(result.current.taskDetail?.id).toBe("task-a"));

    rerender({ id: "task-b" });
    // Never task A's detail under task B, not even for one render.
    expect(result.current.taskDetail?.id).not.toBe("task-a");
    await waitFor(() => expect(result.current.taskDetail?.id).toBe("task-b"));
  });
});
