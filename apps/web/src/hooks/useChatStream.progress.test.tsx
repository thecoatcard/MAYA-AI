import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatEvent, ChatProgressStep } from "../lib/api-client";
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

function progress(
  stepId: string,
  overrides: Partial<ChatProgressStep> = {},
): ChatEvent {
  return {
    type: "progress",
    step: {
      runId: "run-1",
      stepId,
      parentId: null,
      kind: "step",
      label: `Label ${stepId}`,
      detail: null,
      state: "running",
      index: null,
      total: null,
      elapsedMs: null,
      thought: null,
      ...overrides,
    },
  };
}

function finished(finishReason: string): ChatEvent {
  return {
    type: "done",
    assistantMessageId: "assistant-1",
    finishReason,
    usage: null,
  };
}

/** Plays the events, then waits until the request is aborted (a reply still in progress). */
function scripted(events: ChatEvent[], signal?: AbortSignal) {
  return (async function* () {
    for (const event of events) yield event;
    await new Promise<void>((_, reject) => {
      const abort = () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      };
      if (signal?.aborted) abort();
      else signal?.addEventListener("abort", abort, { once: true });
    });
  })();
}

function setup() {
  const stream = vi.fn<Stream>();
  let counter = 0;
  const hook = renderHook(() =>
    useChatStream({
      stream,
      createKey: () => `key-${String(++counter).padStart(4, "0")}`,
      backoff: { baseMs: 1, maxMs: 2, random: () => 0 },
    }),
  );
  return { stream, ...hook };
}

beforeEach(() => {
  setAccessToken("test-access-token");
});

afterEach(() => {
  setAccessToken(null);
  vi.restoreAllMocks();
});

describe("chat stream: progress steps", () => {
  it("adds steps and updates a known step_id in place", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async (params) =>
      scripted(
        [
          accepted,
          progress("a"),
          progress("b"),
          progress("a", { state: "done" }),
        ],
        params.signal,
      ),
    );
    act(() => {
      void result.current.send("hi");
    });
    await waitFor(() =>
      expect(
        result.current.messages[1]?.progress?.map((s) => [s.stepId, s.state]),
      ).toEqual([
        ["a", "done"],
        ["b", "running"],
      ]),
    );
    act(() => result.current.stop());
  });

  it("moves narration out of the reply body into its note, once, and keeps the final text", async () => {
    const { result, stream } = setup();
    const note = (id: string, text: string, len: number) =>
      progress(id, {
        kind: "note",
        label: "Note",
        state: "done",
        note: text,
        textLen: len,
      });
    stream.mockImplementationOnce(async (params) =>
      scripted(
        [
          accepted,
          { type: "delta", text: "Let me search. " },
          note("n1", "Let me search.", 15),
          progress("t1", { kind: "search", label: "Searching the web" }),
          { type: "delta", text: "Found it. " },
          note("n2", "Found it.", 10),
          // A replayed frame of the same note must not remove anything again.
          note("n2", "Found it.", 10),
          { type: "delta", text: "The answer is 42." },
        ],
        params.signal,
      ),
    );
    act(() => {
      void result.current.send("hi");
    });
    await waitFor(() =>
      expect(result.current.messages[1]?.content).toBe("The answer is 42."),
    );
    expect(
      result.current.messages[1]?.progress?.map((s) => [
        s.stepId,
        s.note ?? null,
      ]),
    ).toEqual([
      ["n1", "Let me search."],
      ["t1", null],
      ["n2", "Found it."],
    ]);
    act(() => result.current.stop());
  });

  it("shows the model-busy step, then the overload message with the Retry control", async () => {
    const { result, stream } = setup();
    const overload =
      "The AI model is overloaded right now (Google returned 503 or timeouts). I tried 4 times. Try again in a minute, or press Retry and I will try again now.";
    stream.mockImplementationOnce(async () =>
      (async function* () {
        yield accepted;
        yield progress("busy", {
          label: "The model is busy",
          detail: "Retrying (attempt 2 of 4)",
        });
        yield { type: "delta", text: overload } as ChatEvent;
        yield { type: "error", code: "stream_interrupted", retryable: true };
      })(),
    );
    await act(async () => {
      await result.current.send("hi");
    });
    const reply = result.current.messages[1];
    expect(reply?.progress?.[0]).toMatchObject({
      label: "The model is busy",
      detail: "Retrying (attempt 2 of 4)",
      state: "failed",
    });
    expect(reply?.state).toBe("interrupted");
    expect(reply?.content).toContain("The AI model is overloaded");
    expect(result.current.canAskAgain).toBe(true);
  });

  it("marks a step still running when the reply ends as done", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      (async function* () {
        yield accepted;
        yield progress("a");
        yield finished("stop");
      })(),
    );
    await act(async () => {
      await result.current.send("hi");
    });
    expect(result.current.messages[1]?.progress?.[0]?.state).toBe("done");
  });

  it("marks a running step failed when the reply is interrupted", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      (async function* () {
        yield accepted;
        yield progress("a");
        yield finished("interrupted");
      })(),
    );
    await act(async () => {
      await result.current.send("hi");
    });
    expect(result.current.messages[1]?.state).toBe("interrupted");
    expect(result.current.messages[1]?.progress?.[0]?.state).toBe("failed");
  });

  it("marks running steps skipped when the user stops", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async (params) =>
      scripted(
        [accepted, progress("a"), progress("b", { state: "done" })],
        params.signal,
      ),
    );
    act(() => {
      void result.current.send("hi");
    });
    await waitFor(() =>
      expect(result.current.messages[1]?.progress).toHaveLength(2),
    );
    act(() => result.current.stop());
    expect(result.current.messages[1]?.state).toBe("stopped");
    expect(result.current.messages[1]?.progress?.map((s) => s.state)).toEqual([
      "skipped",
      "done",
    ]);
  });

  it("rebuilds the list from metadata.progress on reload, skipping bad entries", () => {
    const { result } = setup();
    act(() => {
      result.current.openSession("session-7", [
        {
          id: "a-7",
          role: "assistant",
          content: "Here it is.",
          created_at: "2026-11-11T10:00:00Z",
          finish_reason: "stop",
          metadata: {
            progress: [
              {
                run_id: "r",
                step_id: "p1",
                kind: "plan",
                label: "Plan",
                state: "done",
              },
              {
                run_id: "r",
                step_id: "p2",
                kind: "search",
                label: "Search",
                state: "running",
              },
              {
                run_id: "r",
                step_id: "bad",
                kind: "nope",
                label: "x",
                state: "done",
              },
            ],
          },
        },
      ]);
    });
    // The reply has ended, so a step still marked running is settled, not left spinning.
    expect(
      result.current.messages[0]?.progress?.map((s) => [s.stepId, s.state]),
    ).toEqual([
      ["p1", "done"],
      ["p2", "done"],
    ]);
  });
});
