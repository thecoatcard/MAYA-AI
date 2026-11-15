import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatEvent, StoredMessage } from "../lib/api-client";
import { setAccessToken } from "../lib/auth-session";
import { useChatStream, type UseChatStreamOptions } from "./useChatStream";

type Stream = NonNullable<UseChatStreamOptions["stream"]>;

const accepted: ChatEvent = {
  type: "message.accepted",
  sessionId: "session-1",
  userMessageId: "user-1",
  assistantMessageId: "assistant-1",
  replayed: false,
};

const step: ChatEvent = {
  type: "progress",
  step: {
    runId: "run-1",
    stepId: "a",
    parentId: null,
    kind: "step",
    label: "Searching the web",
    detail: null,
    state: "running",
    index: null,
    total: null,
    elapsedMs: null,
    thought: null,
  },
};

/** A reply the test drives by hand: push events, end it, or let the request be aborted. */
function controlled(signal?: AbortSignal) {
  const queue: ChatEvent[] = [];
  let wake: (() => void) | null = null;
  let closed = false;
  signal?.addEventListener("abort", () => {
    closed = true;
    wake?.();
  });
  const events = (async function* () {
    for (;;) {
      const next = queue.shift();
      if (next !== undefined) {
        yield next;
        if (next.type === "done") return;
        continue;
      }
      if (closed) {
        const error = new Error("aborted");
        error.name = "AbortError";
        throw error;
      }
      await new Promise<void>((resolve) => {
        wake = resolve;
      });
    }
  })();
  return {
    events,
    push: (event: ChatEvent) => {
      queue.push(event);
      wake?.();
    },
  };
}

function stored(id: string, role: "user" | "assistant", content: string) {
  return {
    id,
    role,
    content,
    created_at: "2026-11-13T10:00:00Z",
    finish_reason: role === "assistant" ? "stop" : null,
    metadata: {},
    turn_id: null,
  } as unknown as StoredMessage;
}

beforeEach(() => setAccessToken("test-access-token"));
afterEach(() => {
  setAccessToken(null);
  vi.restoreAllMocks();
});

describe("chat stream: a running reply survives switching conversations", () => {
  it("keeps the work block and streamed text and shows them again on return", async () => {
    let live: ReturnType<typeof controlled> | null = null;
    let signal: AbortSignal | undefined;
    const stream = vi.fn<Stream>().mockImplementation(async (params) => {
      signal = params.signal;
      live = controlled(params.signal);
      return live.events;
    });
    const { result } = renderHook(() =>
      useChatStream({ stream, createKey: () => "key-0001" }),
    );

    act(() => {
      void result.current.send("research this");
    });
    await waitFor(() => expect(stream).toHaveBeenCalled());
    act(() => {
      live!.push(accepted);
      live!.push(step);
      live!.push({ type: "delta", text: "First words " });
    });
    await waitFor(() =>
      expect(result.current.messages[1]?.content).toBe("First words "),
    );

    // Open another conversation: the request is NOT aborted and the thread switches.
    act(() => {
      result.current.openSession("session-2", [
        stored("m1", "user", "other"),
        stored("m2", "assistant", "other reply"),
      ]);
    });
    expect(signal?.aborted).toBe(false);
    expect(result.current.sessionId).toBe("session-2");
    expect(result.current.isBusy).toBe(false);

    // The reply keeps going in the background.
    act(() => {
      live!.push({ type: "delta", text: "and more." });
      live!.push(step);
    });
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(result.current.messages.map((m) => m.content)).toEqual([
      "other",
      "other reply",
    ]);

    // Back in the first conversation: progress and text are where they were.
    act(() => {
      result.current.openSession("session-1", [stored("user-1", "user", "x")]);
    });
    expect(result.current.sessionId).toBe("session-1");
    expect(result.current.isBusy).toBe(true);
    const reply = result.current.messages[1];
    expect(reply?.content).toBe("First words and more.");
    expect(reply?.progress?.[0]?.label).toBe("Searching the web");

    // And it still completes into the same message.
    act(() => {
      live!.push({
        type: "done",
        assistantMessageId: "assistant-1",
        finishReason: "stop",
        usage: null,
      });
    });
    await waitFor(() => expect(result.current.isBusy).toBe(false));
    expect(result.current.messages[1]?.state).toBe("complete");
  });

  it("stops every reply on unmount (logout)", async () => {
    let signal: AbortSignal | undefined;
    const stream = vi.fn<Stream>().mockImplementation(async (params) => {
      signal = params.signal;
      const live = controlled(params.signal);
      live.push(accepted);
      return live.events;
    });
    const { result, unmount } = renderHook(() => useChatStream({ stream }));
    act(() => {
      void result.current.send("hello");
    });
    await waitFor(() => expect(stream).toHaveBeenCalled());
    act(() => {
      result.current.reset();
    });
    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });
});
