import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  acceptedFrame,
  deltaFrame,
  doneFrame,
  frame,
  jsonResponse,
  sseResponse,
} from "../components/chat/chat-test-utils";
import {
  ApiError,
  CHAT_MAX_MESSAGE_CHARS,
  NetworkError,
  type ChatEvent,
  type StreamChatMessageParams,
} from "../lib/api-client";
import { setAccessToken } from "../lib/auth-session";
import { useChatStream, type UseChatStreamOptions } from "./useChatStream";

type Stream = NonNullable<UseChatStreamOptions["stream"]>;
type Step = ChatEvent | Error | Promise<unknown>;

const accepted = (
  overrides: Partial<Extract<ChatEvent, { type: "message.accepted" }>> = {},
): ChatEvent => ({
  type: "message.accepted",
  sessionId: "session-1",
  userMessageId: "user-1",
  assistantMessageId: "assistant-1",
  replayed: false,
  ...overrides,
});
const delta = (text: string): ChatEvent => ({ type: "delta", text });
const done = (finishReason = "stop"): ChatEvent => ({
  type: "done",
  assistantMessageId: "assistant-1",
  finishReason,
  usage: null,
});
const errorEvent = (code: string, retryable: boolean): ChatEvent => ({
  type: "error",
  code,
  retryable,
});

/** An event stream that plays the steps in order; an Error step throws, a promise step waits. */
async function* play(
  steps: Step[],
): AsyncGenerator<ChatEvent, void, undefined> {
  for (const step of steps) {
    if (step instanceof Error) {
      throw step;
    }
    if (step instanceof Promise) {
      await step;
      continue;
    }
    yield step;
  }
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

/** Waits until the signal aborts, then throws the way a real aborted fetch does. */
async function* hang(
  signal: AbortSignal | undefined,
  before: ChatEvent[] = [],
): AsyncGenerator<ChatEvent, void, undefined> {
  for (const event of before) {
    yield event;
  }
  await new Promise<void>((_, reject) => {
    const abort = () => {
      const error = new Error("aborted");
      error.name = "AbortError";
      reject(error);
    };
    if (signal?.aborted) {
      abort();
    } else {
      signal?.addEventListener("abort", abort, { once: true });
    }
  });
}

function setup(options: UseChatStreamOptions = {}) {
  const stream = vi.fn<Stream>();
  let counter = 0;
  const hook = renderHook(() =>
    useChatStream({
      stream,
      createKey: () => `key-${String(++counter).padStart(4, "0")}`,
      backoff: { baseMs: 1, maxMs: 2, random: () => 0 },
      ...options,
    }),
  );
  return { stream, ...hook };
}

function paramsOf(
  stream: ReturnType<typeof vi.fn<Stream>>,
  call: number,
): StreamChatMessageParams {
  const params = stream.mock.calls[call]?.[0];
  if (params === undefined) {
    throw new Error(`stream was not called ${call + 1} times`);
  }
  return params;
}

async function send(
  result: { current: ReturnType<typeof useChatStream> },
  text: string,
) {
  await act(async () => {
    await result.current.send(text);
  });
}

beforeEach(() => {
  setAccessToken("test-access-token");
});

afterEach(() => {
  setAccessToken(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("happy path", () => {
  it("adds the user message, appends deltas and finishes", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("Hello"), delta(" world"), done()]),
    );
    await send(result, "Hi MAYA");
    expect(result.current.messages).toMatchObject([
      { role: "user", content: "Hi MAYA", state: "sent", id: "user-1" },
      {
        role: "assistant",
        content: "Hello world",
        state: "complete",
        id: "assistant-1",
        finishReason: "stop",
      },
    ]);
    expect(result.current.status).toBe("idle");
    expect(result.current.sessionId).toBe("session-1");
    expect(result.current.error).toBeNull();
    expect(result.current.isBusy).toBe(false);
    expect(stream).toHaveBeenCalledTimes(1);
    expect(paramsOf(stream, 0)).toMatchObject({
      content: "Hi MAYA",
      sessionId: null,
      idempotencyKey: "key-0001",
    });
  });

  it("passes through sending, then streaming, then idle", async () => {
    const statuses: string[] = [];
    const stream = vi.fn<Stream>();
    const gate = deferred();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("Hel"), gate.promise, delta("lo"), done()]),
    );
    const { result } = renderHook(() => {
      const value = useChatStream({ stream, createKey: () => "key-0001" });
      statuses.push(value.status);
      return value;
    });
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() =>
      expect(result.current.messages[1]?.content).toBe("Hel"),
    );
    expect(result.current.status).toBe("streaming");
    expect(result.current.isBusy).toBe(true);
    expect(result.current.messages[1]?.state).toBe("streaming");
    gate.resolve();
    await act(async () => {
      await sending;
    });
    const unique = statuses.filter(
      (status, index) => index === 0 || status !== statuses[index - 1],
    );
    expect(unique).toEqual(["idle", "sending", "streaming", "idle"]);
  });

  it("uses the session from the first reply for later messages, with a new key each time", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("one"), done()]),
    );
    stream.mockImplementationOnce(async () =>
      play([
        accepted({
          userMessageId: "user-2",
          assistantMessageId: "assistant-2",
        }),
        delta("two"),
        done(),
      ]),
    );
    await send(result, "first");
    await send(result, "second");
    expect(paramsOf(stream, 0).sessionId).toBeNull();
    expect(paramsOf(stream, 1).sessionId).toBe("session-1");
    expect(paramsOf(stream, 0).idempotencyKey).toBe("key-0001");
    expect(paramsOf(stream, 1).idempotencyKey).toBe("key-0002");
    expect(result.current.messages.map((m) => m.content)).toEqual([
      "first",
      "one",
      "second",
      "two",
    ]);
  });

  it("marks a reply that finished as interrupted when the server says so", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("part"), done("interrupted")]),
    );
    await send(result, "Hi");
    expect(result.current.messages[1]).toMatchObject({
      state: "interrupted",
      finishReason: "interrupted",
    });
    expect(result.current.status).toBe("idle");
  });

  it("keeps a reply that hit the length limit as complete with its reason", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("long"), done("max_tokens")]),
    );
    await send(result, "Hi");
    expect(result.current.messages[1]).toMatchObject({
      state: "complete",
      finishReason: "max_tokens",
    });
  });

  it("does not call send twice while a request is in flight", async () => {
    const { result, stream } = setup();
    const gate = deferred();
    stream.mockImplementationOnce(async () =>
      play([accepted(), gate.promise, done()]),
    );
    let first!: Promise<void>;
    act(() => {
      first = result.current.send("one");
    });
    await act(async () => {
      await result.current.send("two");
    });
    expect(stream).toHaveBeenCalledTimes(1);
    gate.resolve();
    await act(async () => {
      await first;
    });
    expect(
      result.current.messages.filter((m) => m.role === "user"),
    ).toHaveLength(1);
  });
});

describe("replay: replaced, not appended", () => {
  it("replaces the partial text when message.accepted says replayed", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("Hel"), new NetworkError()]),
    );
    stream.mockImplementationOnce(async () =>
      play([accepted({ replayed: true }), delta("Hello world"), done()]),
    );
    await send(result, "Hi");
    expect(result.current.messages[1]).toMatchObject({
      content: "Hello world",
      state: "complete",
    });
    expect(stream).toHaveBeenCalledTimes(2);
  });

  it("keeps the partial text visible until the stored reply arrives", async () => {
    const { result, stream } = setup();
    const gate = deferred();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("Hel"), new NetworkError()]),
    );
    stream.mockImplementationOnce(async () =>
      play([
        accepted({ replayed: true }),
        gate.promise,
        delta("Hello world"),
        done(),
      ]),
    );
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() => expect(stream).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(result.current.messages[1]?.id).toBe("assistant-1"),
    );
    await waitFor(() => expect(result.current.status).toBe("streaming"));
    expect(result.current.messages[1]?.content).toBe("Hel");
    gate.resolve();
    await act(async () => {
      await sending;
    });
    expect(result.current.messages[1]?.content).toBe("Hello world");
  });

  it("appends later pieces after the replacing delta", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("ab"), new NetworkError()]),
    );
    stream.mockImplementationOnce(async () =>
      play([accepted({ replayed: true }), delta("abcd"), delta("ef"), done()]),
    );
    await send(result, "Hi");
    expect(result.current.messages[1]?.content).toBe("abcdef");
  });

  it("starts again from empty when the retry is a fresh stream (nothing duplicated)", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("Hel"), new NetworkError()]),
    );
    stream.mockImplementationOnce(async () =>
      play([accepted({ replayed: false }), delta("Hello"), done()]),
    );
    await send(result, "Hi");
    expect(result.current.messages[1]?.content).toBe("Hello");
  });
});

describe("Idempotency-Key", () => {
  it("generates one key per user message and never a new one for the same message", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () => {
      throw new NetworkError();
    });
    stream.mockImplementationOnce(async () => {
      throw new ApiError(409, "request_in_progress");
    });
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("ok"), done()]),
    );
    await send(result, "Hi");
    expect(stream).toHaveBeenCalledTimes(3);
    const keys = new Set(
      [0, 1, 2].map((call) => paramsOf(stream, call).idempotencyKey),
    );
    expect(keys).toEqual(new Set(["key-0001"]));
  });

  it("reuses the same key and the original session_id on a manual retry", async () => {
    // The stream ends without done or error, so the client does not know the
    // outcome: that is when a same-key retry is useful.
    const { result, stream } = setup({ maxAttempts: 1 });
    stream.mockImplementationOnce(async () => play([accepted(), delta("par")]));
    stream.mockImplementationOnce(async () =>
      play([
        accepted({ replayed: true }),
        delta("par and the rest"),
        done("interrupted"),
      ]),
    );
    await send(result, "Hi");
    expect(result.current.status).toBe("error");
    expect(result.current.error?.code).toBe("network_error");
    expect(result.current.canRetry).toBe(true);
    // The reply created the session, but the first request had no session_id.
    expect(result.current.sessionId).toBe("session-1");
    await act(async () => {
      await result.current.retry();
    });
    expect(stream).toHaveBeenCalledTimes(2);
    expect(paramsOf(stream, 1).idempotencyKey).toBe(
      paramsOf(stream, 0).idempotencyKey,
    );
    expect(paramsOf(stream, 1).sessionId).toBeNull();
    expect(paramsOf(stream, 1).content).toBe("Hi");
    expect(result.current.messages[1]).toMatchObject({
      content: "par and the rest",
      state: "interrupted",
    });
    expect(result.current.status).toBe("idle");
    expect(result.current.error).toBeNull();
    // The replayed reply is interrupted: Ask again is available without any
    // error notice (the stored outcome is final for this key).
    expect(result.current.canAskAgain).toBe(true);
    expect(result.current.canRetry).toBe(false);
  });

  it("keeps the key across several manual retries and gives the next message a new one", async () => {
    const { result, stream } = setup({ maxAttempts: 1 });
    stream.mockImplementation(async () => {
      throw new NetworkError();
    });
    await send(result, "Hi");
    for (let i = 0; i < 3; i += 1) {
      await act(async () => {
        await result.current.retry();
      });
    }
    expect(stream).toHaveBeenCalledTimes(4);
    expect(
      new Set([0, 1, 2, 3].map((c) => paramsOf(stream, c).idempotencyKey)),
    ).toEqual(new Set(["key-0001"]));
    stream.mockImplementationOnce(async () => play([accepted(), done()]));
    await act(async () => {
      result.current.dismissError();
    });
    await send(result, "Different message");
    expect(paramsOf(stream, 4).idempotencyKey).toBe("key-0002");
  });

  it("sends the same header and body on a retry through the real client", async () => {
    setAccessToken("real-client-token");
    const calls: Array<{ headers: Record<string, string>; body: string }> = [];
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      calls.push({
        headers: init?.headers as Record<string, string>,
        body: String(init?.body),
      });
      if (calls.length === 1) {
        throw new TypeError("Failed to fetch");
      }
      return sseResponse([
        acceptedFrame({ replayed: true }),
        frame("task.progress", { pct: 10 }),
        deltaFrame("Hello"),
        doneFrame(),
      ]);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() =>
      useChatStream({ backoff: { baseMs: 1, maxMs: 2, random: () => 0 } }),
    );
    await send(result, "Hi");
    expect(calls).toHaveLength(2);
    expect(calls[0]?.headers["Idempotency-Key"]).toMatch(
      /^[A-Za-z0-9_-]{8,128}$/,
    );
    expect(calls[1]?.headers["Idempotency-Key"]).toBe(
      calls[0]?.headers["Idempotency-Key"],
    );
    expect(calls[1]?.body).toBe(calls[0]?.body);
    expect(calls[1]?.headers.Authorization).toBe("Bearer real-client-token");
    expect(result.current.messages[1]).toMatchObject({
      content: "Hello",
      state: "complete",
    });
  });
});

describe("automatic retry is bounded", () => {
  it("retries a network error and then succeeds", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () => {
      throw new NetworkError();
    });
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("ok"), done()]),
    );
    await send(result, "Hi");
    expect(stream).toHaveBeenCalledTimes(2);
    expect(result.current.status).toBe("idle");
    expect(result.current.retrying).toBeNull();
  });

  it("gives up after 3 attempts on network errors and offers Retry", async () => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new NetworkError();
    });
    await send(result, "Hi");
    expect(stream).toHaveBeenCalledTimes(3);
    expect(result.current.status).toBe("error");
    expect(result.current.error).toMatchObject({
      code: "network_error",
      retryable: true,
    });
    expect(result.current.canRetry).toBe(true);
    // Delivery is unknown, so the message is not labelled "Not sent".
    expect(result.current.messages).toMatchObject([
      { role: "user", state: "unconfirmed" },
    ]);
  });

  it("retries 409 request_in_progress up to 3 attempts", async () => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(409, "request_in_progress");
    });
    await send(result, "Hi");
    expect(stream).toHaveBeenCalledTimes(3);
    expect(result.current.error).toMatchObject({
      code: "request_in_progress",
      retryable: true,
    });
  });

  it("recovers when a 409 clears on the last attempt", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () => {
      throw new ApiError(409, "request_in_progress");
    });
    stream.mockImplementationOnce(async () => {
      throw new ApiError(409, "request_in_progress");
    });
    stream.mockImplementationOnce(async () =>
      play([accepted({ replayed: true }), delta("stored"), done()]),
    );
    await send(result, "Hi");
    expect(stream).toHaveBeenCalledTimes(3);
    expect(result.current.messages[1]?.content).toBe("stored");
    expect(result.current.status).toBe("idle");
  });

  it("retries a stream that ends without done or error", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () => play([accepted(), delta("cut")]));
    stream.mockImplementationOnce(async () =>
      play([accepted({ replayed: true }), delta("cut and finished"), done()]),
    );
    await send(result, "Hi");
    expect(stream).toHaveBeenCalledTimes(2);
    expect(result.current.messages[1]?.content).toBe("cut and finished");
  });

  it("honours a custom attempt limit", async () => {
    const { result, stream } = setup({ maxAttempts: 2 });
    stream.mockImplementation(async () => {
      throw new NetworkError();
    });
    await send(result, "Hi");
    expect(stream).toHaveBeenCalledTimes(2);
  });

  it("shows the attempt while waiting to retry", async () => {
    vi.useFakeTimers();
    try {
      const { result, stream } = setup({
        backoff: { baseMs: 50, maxMs: 50, random: () => 1 },
      });
      stream.mockImplementationOnce(async () => {
        throw new NetworkError();
      });
      stream.mockImplementationOnce(async () => play([accepted(), done()]));
      act(() => {
        void result.current.send("Hi");
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10);
      });
      expect(result.current.retrying).toEqual({ attempt: 2, maxAttempts: 3 });
      expect(result.current.status).toBe("sending");
      expect(result.current.isBusy).toBe(true);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60);
      });
      expect(result.current.retrying).toBeNull();
      expect(result.current.status).toBe("idle");
      expect(stream).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("uses exponential backoff with jitter between attempts", async () => {
    vi.useFakeTimers();
    try {
      const { result, stream } = setup({
        backoff: { baseMs: 100, maxMs: 10_000, random: () => 0.5 },
      });
      stream.mockImplementation(async () => {
        throw new NetworkError();
      });
      const advance = async (ms: number) => {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(ms);
        });
      };
      act(() => {
        void result.current.send("Hi");
      });
      await advance(0);
      expect(stream).toHaveBeenCalledTimes(1);
      // First wait: step 100 ms, equal jitter at random 0.5 gives 75 ms.
      await advance(74);
      expect(stream).toHaveBeenCalledTimes(1);
      await advance(1);
      expect(stream).toHaveBeenCalledTimes(2);
      // Second wait: step 200 ms gives 150 ms (the step doubles).
      await advance(149);
      expect(stream).toHaveBeenCalledTimes(2);
      await advance(1);
      expect(stream).toHaveBeenCalledTimes(3);
      // No fourth attempt, however long we wait.
      await advance(60_000);
      expect(stream).toHaveBeenCalledTimes(3);
      expect(result.current.status).toBe("error");
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not retry other API errors automatically", async () => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(422, "validation_error", { fields: ["content"] });
    });
    await send(result, "Hi");
    expect(stream).toHaveBeenCalledTimes(1);
    expect(result.current.error).toMatchObject({
      code: "validation_error",
      retryable: false,
    });
    expect(result.current.canRetry).toBe(false);
  });

  it("does not retry on an error event and ignores its retryable flag", async () => {
    // The server stores the failed outcome under the key, so the same key would
    // only replay this error. Only Ask again (a new key) can change anything.
    const { result, stream } = setup();
    stream.mockImplementation(async () =>
      play([accepted(), delta("x"), errorEvent("internal_error", true)]),
    );
    await send(result, "Hi");
    expect(stream).toHaveBeenCalledTimes(1);
    expect(result.current.error).toMatchObject({
      code: "internal_error",
      retryable: false,
    });
    expect(result.current.canRetry).toBe(false);
    expect(result.current.canAskAgain).toBe(true);
  });
});

describe("429 rate limit", () => {
  it("shows the Retry-After wait and does not retry automatically", async () => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(429, "rate_limited", { retryAfterSeconds: 7 });
    });
    await send(result, "Hi");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(stream).toHaveBeenCalledTimes(1);
    expect(result.current.error).toMatchObject({
      code: "rate_limited",
      retryAfterSeconds: 7,
      retryable: true,
    });
    expect(result.current.error?.message).toContain("rate limit");
    expect(result.current.messages[0]).toMatchObject({
      role: "user",
      state: "failed",
    });
  });

  it("works without a Retry-After header", async () => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(429, "rate_limited");
    });
    await send(result, "Hi");
    expect(result.current.error?.retryAfterSeconds).toBeNull();
  });
});

describe("error event", () => {
  it("ends the stream cleanly, keeps the partial text and offers only Ask again", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([
        accepted(),
        delta("partial"),
        errorEvent("stream_interrupted", true),
      ]),
    );
    await send(result, "Hi");
    expect(result.current.status).toBe("error");
    expect(result.current.isBusy).toBe(false);
    expect(result.current.error).toMatchObject({
      code: "stream_interrupted",
      retryable: false,
    });
    expect(result.current.canRetry).toBe(false);
    expect(result.current.canAskAgain).toBe(true);
    expect(result.current.messages[1]).toMatchObject({
      content: "partial",
      state: "interrupted",
    });
    expect(result.current.messages[0]?.state).toBe("sent");
    await act(async () => {
      await result.current.retry();
    });
    expect(stream).toHaveBeenCalledTimes(1);
  });

  it.each([
    "content_blocked",
    "stream_interrupted",
    "model_unavailable",
    "internal_error",
  ])(
    "offers no same-key Retry after %s, whatever the retryable flag says",
    async (code) => {
      for (const flag of [true, false]) {
        const { result, stream } = setup();
        stream.mockImplementationOnce(async () =>
          play([accepted(), errorEvent(code, flag)]),
        );
        await send(result, "Hi");
        expect(result.current.error?.code).toBe(code);
        expect(result.current.canRetry).toBe(false);
        expect(result.current.canAskAgain).toBe(true);
        await act(async () => {
          await result.current.retry();
        });
        expect(stream).toHaveBeenCalledTimes(1);
      }
    },
  );

  it("shows model_unavailable with no text as an interrupted reply with only Ask again", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), errorEvent("model_unavailable", true)]),
    );
    await send(result, "Hi");
    expect(result.current.messages[1]).toMatchObject({
      content: "",
      state: "interrupted",
    });
    expect(result.current.error?.code).toBe("model_unavailable");
    expect(result.current.canRetry).toBe(false);
    expect(result.current.canAskAgain).toBe(true);
    // Ask again is a new user action: new key, same text.
    stream.mockImplementationOnce(async () =>
      play([
        accepted({
          userMessageId: "user-2",
          assistantMessageId: "assistant-2",
        }),
        delta("back"),
        done(),
      ]),
    );
    await act(async () => {
      await result.current.askAgain();
    });
    expect(stream).toHaveBeenCalledTimes(2);
    expect(paramsOf(stream, 1).idempotencyKey).not.toBe(
      paramsOf(stream, 0).idempotencyKey,
    );
    expect(paramsOf(stream, 1).content).toBe("Hi");
    expect(result.current.messages[3]?.content).toBe("back");
  });

  it("offers no Retry for content_blocked", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), errorEvent("content_blocked", false)]),
    );
    await send(result, "Hi");
    expect(result.current.error).toMatchObject({
      code: "content_blocked",
      retryable: false,
    });
    expect(result.current.canRetry).toBe(false);
    await act(async () => {
      await result.current.retry();
    });
    expect(stream).toHaveBeenCalledTimes(1);
  });

  it("never shows server text, only a plain message for the code", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), errorEvent("something_new", false)]),
    );
    await send(result, "Hi");
    expect(result.current.error?.message).toBe(
      "Something went wrong. Try again.",
    );
  });
});

describe("ask again", () => {
  it("sends the same text as a new message with a new key after an interrupted reply", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("cut"), errorEvent("stream_interrupted", true)]),
    );
    stream.mockImplementationOnce(async () =>
      play([
        accepted({
          userMessageId: "user-2",
          assistantMessageId: "assistant-2",
        }),
        delta("full answer"),
        done(),
      ]),
    );
    await send(result, "Question");
    await act(async () => {
      await result.current.askAgain();
    });
    expect(paramsOf(stream, 1).content).toBe("Question");
    expect(paramsOf(stream, 1).idempotencyKey).toBe("key-0002");
    expect(paramsOf(stream, 1).sessionId).toBe("session-1");
    expect(result.current.messages.map((m) => m.content)).toEqual([
      "Question",
      "cut",
      "Question",
      "full answer",
    ]);
    expect(result.current.status).toBe("idle");
  });

  it("is not available when the message was never accepted", async () => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(503, "chat_unavailable");
    });
    await send(result, "Hi");
    await act(async () => {
      await result.current.askAgain();
    });
    expect(stream).toHaveBeenCalledTimes(1);
  });
});

describe("failed before the server accepted the message", () => {
  it("returns the text so the composer can restore it, and removes the message", async () => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(422, "validation_error", { fields: ["content"] });
    });
    await send(result, "Please keep this text");
    let restored: string | null = null;
    act(() => {
      restored = result.current.dismissError();
    });
    expect(restored).toBe("Please keep this text");
    expect(result.current.messages).toEqual([]);
    expect(result.current.status).toBe("idle");
    expect(result.current.error).toBeNull();
  });

  it("keeps messages and returns null when dismissing after an accepted exchange failed", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("x"), errorEvent("stream_interrupted", true)]),
    );
    await send(result, "Hi");
    let restored: string | null = "unset";
    act(() => {
      restored = result.current.dismissError();
    });
    expect(restored).toBeNull();
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.status).toBe("idle");
    await act(async () => {
      await result.current.retry();
    });
    expect(stream).toHaveBeenCalledTimes(1);
  });

  it("maps an unauthenticated request to a plain message", async () => {
    setAccessToken(null);
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(401, "not_authenticated");
    });
    await send(result, "Hi");
    expect(result.current.error?.code).toBe("not_authenticated");
    expect(result.current.error?.message).toContain("Sign-in is not built yet");
    expect(result.current.canRetry).toBe(false);
  });

  it("retries a chat_unavailable response manually", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () => {
      throw new ApiError(503, "chat_unavailable");
    });
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("back"), done()]),
    );
    await send(result, "Hi");
    expect(result.current.canRetry).toBe(true);
    expect(stream).toHaveBeenCalledTimes(1);
    await act(async () => {
      await result.current.retry();
    });
    expect(result.current.messages[0]?.state).toBe("sent");
    expect(result.current.messages[1]?.content).toBe("back");
    expect(paramsOf(stream, 1).idempotencyKey).toBe(
      paramsOf(stream, 0).idempotencyKey,
    );
  });
});

describe("stop()", () => {
  it("aborts the request, keeps the text and returns to idle", async () => {
    const { result, stream } = setup();
    let signal: AbortSignal | undefined;
    stream.mockImplementationOnce(async (params) => {
      signal = params.signal;
      return hang(params.signal, [accepted(), delta("partial")]);
    });
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() =>
      expect(result.current.messages[1]?.content).toBe("partial"),
    );
    expect(signal?.aborted).toBe(false);
    act(() => {
      result.current.stop();
    });
    expect(signal?.aborted).toBe(true);
    expect(result.current.status).toBe("idle");
    expect(result.current.messages[1]).toMatchObject({
      content: "partial",
      state: "stopped",
    });
    expect(result.current.error).toBeNull();
    await act(async () => {
      await sending;
    });
    expect(stream).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("idle");
  });

  it("stops before the server answered without leaving a stuck state", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async (params) => hang(params.signal));
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() => expect(stream).toHaveBeenCalled());
    act(() => {
      result.current.stop();
    });
    await act(async () => {
      await sending;
    });
    expect(result.current.status).toBe("idle");
    expect(result.current.isBusy).toBe(false);
    expect(result.current.error).toBeNull();
    // A new message can be sent afterwards, with a new key.
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("fine"), done()]),
    );
    await send(result, "Again");
    expect(paramsOf(stream, 1).idempotencyKey).toBe("key-0002");
  });

  it("cancels the wait between automatic retries", async () => {
    const { result, stream } = setup({
      backoff: { baseMs: 60_000, maxMs: 60_000, random: () => 1 },
    });
    stream.mockImplementation(async () => {
      throw new NetworkError();
    });
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() => expect(result.current.retrying).not.toBeNull());
    act(() => {
      result.current.stop();
    });
    await act(async () => {
      await sending;
    });
    expect(stream).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("idle");
    expect(result.current.retrying).toBeNull();
  });

  it("does nothing when there is nothing to stop", () => {
    const { result } = setup();
    act(() => {
      result.current.stop();
    });
    expect(result.current.status).toBe("idle");
  });

  it("applies nothing from a stream that keeps talking after stop", async () => {
    const { result, stream } = setup();
    const gate = deferred();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("kept"), gate.promise, delta(" LATE"), done()]),
    );
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() =>
      expect(result.current.messages[1]?.content).toBe("kept"),
    );
    act(() => {
      result.current.stop();
    });
    gate.resolve();
    await act(async () => {
      await sending;
    });
    expect(result.current.messages[1]).toMatchObject({
      content: "kept",
      state: "stopped",
    });
  });
});

describe("unmount", () => {
  it("aborts the in-flight request and applies nothing afterwards", async () => {
    const errorSpy = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);
    const { result, stream, unmount } = setup();
    let signal: AbortSignal | undefined;
    stream.mockImplementationOnce(async (params) => {
      signal = params.signal;
      return hang(params.signal, [accepted(), delta("x")]);
    });
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() => expect(result.current.messages[1]?.content).toBe("x"));
    unmount();
    expect(signal?.aborted).toBe(true);
    await sending;
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("aborts while waiting to retry", async () => {
    const { result, stream, unmount } = setup({
      backoff: { baseMs: 60_000, maxMs: 60_000, random: () => 1 },
    });
    stream.mockImplementation(async () => {
      throw new NetworkError();
    });
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() => expect(result.current.retrying).not.toBeNull());
    unmount();
    await sending;
    expect(stream).toHaveBeenCalledTimes(1);
  });
});

describe("reset", () => {
  it("aborts and clears the conversation, so the next message starts a new session", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("one"), done()]),
    );
    await send(result, "first");
    act(() => {
      result.current.reset();
    });
    expect(result.current.messages).toEqual([]);
    expect(result.current.sessionId).toBeNull();
    expect(result.current.status).toBe("idle");
    stream.mockImplementationOnce(async () => play([accepted(), done()]));
    await send(result, "second");
    expect(paramsOf(stream, 1).sessionId).toBeNull();
  });
});

describe("local checks and unknown events", () => {
  it("refuses empty, too long and disallowed text without calling the network", async () => {
    const { result, stream } = setup();
    await send(result, "   ");
    expect(result.current.error?.code).toBe("empty");
    await send(result, "a".repeat(CHAT_MAX_MESSAGE_CHARS + 1));
    expect(result.current.error?.code).toBe("too_long");
    await send(result, "bad\u0000text");
    expect(result.current.error?.code).toBe("invalid_characters");
    expect(stream).not.toHaveBeenCalled();
    expect(result.current.messages).toEqual([]);
    act(() => {
      expect(result.current.dismissError()).toBeNull();
    });
    expect(result.current.error).toBeNull();
  });

  it("ignores event names it does not know when run on the real client", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          acceptedFrame(),
          frame("task.started", { id: "t" }),
          deltaFrame("Hi"),
          frame("future.event", { x: 1 }),
          doneFrame(),
        ]),
      ),
    );
    const { result } = renderHook(() => useChatStream());
    await send(result, "Hello");
    expect(result.current.messages[1]).toMatchObject({
      content: "Hi",
      state: "complete",
    });
    expect(result.current.status).toBe("idle");
  });

  it("maps a JSON 429 from the real client to the Retry-After wait", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ error: "rate_limited" }, 429, { "Retry-After": "12" }),
      ),
    );
    const { result } = renderHook(() => useChatStream());
    await send(result, "Hello");
    expect(result.current.error).toMatchObject({
      code: "rate_limited",
      retryAfterSeconds: 12,
    });
  });
});

describe("privacy", () => {
  it("never logs message content, even through failures", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map(
      (method) => vi.spyOn(console, method).mockImplementation(() => undefined),
    );
    const { result, stream } = setup();
    const secret = "my very private message 12345";
    stream.mockImplementationOnce(async () => {
      throw new NetworkError();
    });
    stream.mockImplementationOnce(async () =>
      play([
        accepted(),
        delta("reply text"),
        errorEvent("internal_error", true),
      ]),
    );
    await send(result, secret);
    stream.mockImplementationOnce(async () => play([accepted(), done()]));
    await act(async () => {
      await result.current.askAgain();
    });
    for (const spy of spies) {
      expect(spy).not.toHaveBeenCalled();
    }
  });
});

describe("which failures allow a same-key Retry", () => {
  it.each([
    [502, "unknown_error"],
    [504, "unknown_error"],
    [500, "unknown_error"],
    [500, "internal_error"],
    [503, "service_down"],
  ])(
    "retries a %i %s answer with the same key and the generic message",
    async (status, code) => {
      const { result, stream } = setup();
      stream.mockImplementationOnce(async () => {
        throw new ApiError(status, code);
      });
      stream.mockImplementationOnce(async () =>
        play([accepted({ replayed: true }), delta("stored"), done()]),
      );
      await send(result, "Hi");
      // Not retried automatically; the person decides.
      expect(stream).toHaveBeenCalledTimes(1);
      expect(result.current.canRetry).toBe(true);
      expect(result.current.error).toMatchObject({ code, retryable: true });
      expect(result.current.messages[0]?.state).toBe("unconfirmed");
      await act(async () => {
        await result.current.retry();
      });
      expect(paramsOf(stream, 1).idempotencyKey).toBe(
        paramsOf(stream, 0).idempotencyKey,
      );
      expect(result.current.messages[1]?.content).toBe("stored");
    },
  );

  it("uses the generic message for an unknown 5xx", async () => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(502, "unknown_error");
    });
    await send(result, "Hi");
    expect(result.current.error?.message).toBe(
      "Something went wrong. Try again.",
    );
  });

  it("retries a proxy 502 with an HTML body through the real client", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response("<html>Bad Gateway</html>", {
          status: 502,
          headers: { "Content-Type": "text/html" },
        }),
      )
      .mockResolvedValueOnce(
        sseResponse([
          acceptedFrame({ replayed: true }),
          deltaFrame("done now"),
          doneFrame(),
        ]),
      );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useChatStream());
    await send(result, "Hi");
    expect(result.current.canRetry).toBe(true);
    expect(result.current.error?.code).toBe("unknown_error");
    await act(async () => {
      await result.current.retry();
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [first, second] = fetchMock.mock.calls.map(
      (call) => (call[1] as RequestInit).headers as Record<string, string>,
    );
    expect(second["Idempotency-Key"]).toBe(first["Idempotency-Key"]);
    expect(result.current.messages[1]?.content).toBe("done now");
  });

  it("offers no same-key Retry for the documented 503 model_unavailable", async () => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(503, "model_unavailable");
    });
    await send(result, "Hi");
    expect(result.current.canRetry).toBe(false);
    expect(result.current.error?.code).toBe("model_unavailable");
    // The server answered, so the message is "Not sent" and its text can come back.
    expect(result.current.messages[0]?.state).toBe("failed");
    await act(async () => {
      await result.current.retry();
    });
    expect(stream).toHaveBeenCalledTimes(1);
  });

  it("keeps a 503 chat_unavailable retryable and labels the message Not sent", async () => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(503, "chat_unavailable");
    });
    await send(result, "Hi");
    expect(result.current.canRetry).toBe(true);
    expect(result.current.messages[0]?.state).toBe("failed");
  });

  it.each([
    [404, "session_not_found"],
    [422, "validation_error"],
    [422, "idempotency_key_reused"],
    [401, "not_authenticated"],
    [404, "unknown_error"],
  ])("offers no Retry for %i %s", async (status, code) => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(status, code);
    });
    await send(result, "Hi");
    expect(result.current.canRetry).toBe(false);
    expect(result.current.messages[0]?.state).toBe("failed");
  });

  it("labels the message Not sent for a 429, which is retried after Retry-After", async () => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(429, "rate_limited", { retryAfterSeconds: 5 });
    });
    await send(result, "Hi");
    expect(result.current.canRetry).toBe(true);
    expect(result.current.messages[0]?.state).toBe("failed");
  });

  it("labels the message Not confirmed after 409 request_in_progress", async () => {
    const { result, stream } = setup();
    stream.mockImplementation(async () => {
      throw new ApiError(409, "request_in_progress");
    });
    await send(result, "Hi");
    expect(result.current.canRetry).toBe(true);
    expect(result.current.messages[0]?.state).toBe("unconfirmed");
  });

  it("refuses an unsafe API address with a clear error and sends nothing", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.example.test");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useChatStream());
    await send(result, "Hi");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.error?.code).toBe("configuration_error");
    expect(result.current.error?.message).toContain("https");
    expect(result.current.canRetry).toBe(false);
    expect(result.current.messages[0]?.state).toBe("failed");
  });
});

describe("a message whose delivery is unknown", () => {
  it("is kept in the thread when the error is dismissed, and its text is not offered again", async () => {
    const { result, stream } = setup({ maxAttempts: 1 });
    stream.mockImplementation(async () => {
      throw new NetworkError();
    });
    await send(result, "Do not duplicate me");
    expect(result.current.messages[0]?.state).toBe("unconfirmed");
    let restored: string | null = "unset";
    act(() => {
      restored = result.current.dismissError();
    });
    expect(restored).toBeNull();
    expect(result.current.messages).toMatchObject([
      { role: "user", content: "Do not duplicate me", state: "unconfirmed" },
    ]);
    expect(result.current.error).toBeNull();
    expect(result.current.status).toBe("idle");
    // After dismissing, the same-key retry is gone too.
    await act(async () => {
      await result.current.retry();
    });
    expect(stream).toHaveBeenCalledTimes(1);
  });

  it("returns to pending and then sent when Retry works", async () => {
    const { result, stream } = setup({ maxAttempts: 1 });
    stream.mockImplementationOnce(async () => {
      throw new NetworkError();
    });
    stream.mockImplementationOnce(async () =>
      play([accepted({ replayed: true }), delta("ok"), done()]),
    );
    await send(result, "Hi");
    expect(result.current.messages[0]?.state).toBe("unconfirmed");
    await act(async () => {
      await result.current.retry();
    });
    expect(result.current.messages[0]?.state).toBe("sent");
    expect(result.current.messages).toHaveLength(2);
  });

  it("is also unconfirmed when the person stops before the server answered", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async (params) => hang(params.signal));
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() => expect(stream).toHaveBeenCalled());
    act(() => {
      result.current.stop();
    });
    await act(async () => {
      await sending;
    });
    expect(result.current.messages[0]?.state).toBe("unconfirmed");
  });
});

describe("Ask again is derived from the last message", () => {
  it("is available after a replayed interrupted done, with no error shown", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("par"), new NetworkError()]),
    );
    stream.mockImplementationOnce(async () =>
      play([accepted({ replayed: true }), delta("par"), done("interrupted")]),
    );
    stream.mockImplementationOnce(async () =>
      play([
        accepted({
          userMessageId: "user-2",
          assistantMessageId: "assistant-2",
        }),
        delta("whole"),
        done(),
      ]),
    );
    await send(result, "Question");
    // The automatic retry replayed an interrupted reply: no error, still idle.
    expect(result.current.status).toBe("idle");
    expect(result.current.error).toBeNull();
    expect(result.current.messages[1]?.state).toBe("interrupted");
    expect(result.current.canAskAgain).toBe(true);
    await act(async () => {
      await result.current.askAgain();
    });
    expect(paramsOf(stream, 2).content).toBe("Question");
    expect(paramsOf(stream, 2).idempotencyKey).not.toBe(
      paramsOf(stream, 0).idempotencyKey,
    );
    expect(result.current.canAskAgain).toBe(false);
    expect(result.current.messages.at(-1)?.content).toBe("whole");
  });

  it("stays available after the error notice is dismissed", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("cut"), errorEvent("stream_interrupted", true)]),
    );
    await send(result, "Question");
    act(() => {
      result.current.dismissError();
    });
    expect(result.current.error).toBeNull();
    expect(result.current.canAskAgain).toBe(true);
  });

  it("is not available for a complete, a stopped or a failed-before-accept exchange", async () => {
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("ok"), done()]),
    );
    await send(result, "One");
    expect(result.current.canAskAgain).toBe(false);
    stream.mockImplementationOnce(async () => {
      throw new ApiError(422, "validation_error");
    });
    await send(result, "Two");
    expect(result.current.canAskAgain).toBe(false);
    await act(async () => {
      await result.current.askAgain();
    });
    expect(stream).toHaveBeenCalledTimes(2);
  });
});

describe("deltas are applied once per animation frame", () => {
  function stubFrames() {
    const callbacks = new Map<number, FrameRequestCallback>();
    let next = 0;
    const cancelled: number[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      next += 1;
      callbacks.set(next, callback);
      return next;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
      cancelled.push(id);
      callbacks.delete(id);
    });
    return {
      pending: () => callbacks.size,
      cancelled,
      run: () => {
        const list = Array.from(callbacks.values());
        callbacks.clear();
        for (const callback of list) {
          callback(0);
        }
      },
    };
  }

  function setupWithRenderCount(stream: ReturnType<typeof vi.fn<Stream>>) {
    const renders = { count: 0 };
    const hook = renderHook(() => {
      renders.count += 1;
      return useChatStream({
        stream,
        createKey: () => "key-0001",
        backoff: { baseMs: 1, maxMs: 2, random: () => 0 },
      });
    });
    return { renders, ...hook };
  }

  it("renders several pieces of text once, when the frame runs", async () => {
    const frames = stubFrames();
    const stream = vi.fn<Stream>();
    const gate = deferred();
    stream.mockImplementationOnce(async () =>
      play([
        accepted(),
        delta("a"),
        delta("b"),
        delta("c"),
        gate.promise,
        delta("d"),
        done(),
      ]),
    );
    const { result, renders } = setupWithRenderCount(stream);
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() => expect(frames.pending()).toBe(1));
    // Three pieces arrived, none is rendered yet.
    expect(result.current.messages[1]?.content).toBe("");
    const before = renders.count;
    act(() => frames.run());
    expect(result.current.messages[1]?.content).toBe("abc");
    expect(renders.count - before).toBe(1);
    // The next piece waits for its own frame; done flushes it first.
    gate.resolve();
    await act(async () => {
      await sending;
    });
    expect(result.current.messages[1]).toMatchObject({
      content: "abcd",
      state: "complete",
    });
    expect(frames.pending()).toBe(0);
  });

  it("flushes the buffered text before done", async () => {
    const frames = stubFrames();
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("x"), delta("y"), done()]),
    );
    await send(result, "Hi");
    expect(result.current.messages[1]?.content).toBe("xy");
    expect(frames.pending()).toBe(0);
  });

  it("flushes the buffered text before an error ends the reply", async () => {
    stubFrames();
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("par"), errorEvent("stream_interrupted", true)]),
    );
    await send(result, "Hi");
    expect(result.current.messages[1]).toMatchObject({
      content: "par",
      state: "interrupted",
    });
  });

  it("flushes the buffered text before the wait for an automatic retry", async () => {
    const frames = stubFrames();
    const { result, stream } = setup();
    const gate = deferred();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("kept"), new NetworkError()]),
    );
    stream.mockImplementationOnce(async () =>
      play([
        accepted({ replayed: true }),
        gate.promise,
        delta("kept and more"),
        done(),
      ]),
    );
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    // No frame has run: the text is on screen because the retry flushed it.
    await waitFor(() =>
      expect(result.current.messages[1]?.content).toBe("kept"),
    );
    expect(frames.pending()).toBe(0);
    gate.resolve();
    await act(async () => {
      await sending;
    });
    expect(result.current.messages[1]?.content).toBe("kept and more");
  });

  it("keeps the text that arrived when the person stops", async () => {
    const frames = stubFrames();
    const { result, stream } = setup();
    stream.mockImplementationOnce(async (params) =>
      hang(params.signal, [accepted(), delta("par"), delta("tial")]),
    );
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() => expect(frames.pending()).toBe(1));
    act(() => {
      result.current.stop();
    });
    expect(result.current.messages[1]).toMatchObject({
      content: "partial",
      state: "stopped",
    });
    expect(frames.pending()).toBe(0);
    await act(async () => {
      await sending;
    });
    act(() => frames.run());
    expect(result.current.messages[1]?.content).toBe("partial");
  });

  it("drops the buffered text and the pending frame on reset", async () => {
    const frames = stubFrames();
    const { result, stream } = setup();
    stream.mockImplementationOnce(async (params) =>
      hang(params.signal, [accepted(), delta("never shown")]),
    );
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() => expect(frames.pending()).toBe(1));
    act(() => {
      result.current.reset();
    });
    expect(frames.pending()).toBe(0);
    await act(async () => {
      await sending;
    });
    act(() => frames.run());
    expect(result.current.messages).toEqual([]);
  });

  it("cancels the pending frame on unmount and renders nothing afterwards", async () => {
    const errorSpy = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);
    const frames = stubFrames();
    const { result, stream, unmount } = setup();
    stream.mockImplementationOnce(async (params) =>
      hang(params.signal, [accepted(), delta("x")]),
    );
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() => expect(frames.pending()).toBe(1));
    unmount();
    expect(frames.pending()).toBe(0);
    expect(frames.cancelled.length).toBeGreaterThan(0);
    await sending;
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("applies text at once when requestAnimationFrame is not available", async () => {
    vi.stubGlobal("requestAnimationFrame", undefined);
    const { result, stream } = setup();
    const gate = deferred();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("now"), gate.promise, done()]),
    );
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send("Hi");
    });
    await waitFor(() =>
      expect(result.current.messages[1]?.content).toBe("now"),
    );
    gate.resolve();
    await act(async () => {
      await sending;
    });
    expect(result.current.messages[1]?.state).toBe("complete");
  });

  it("lets a replayed reply replace the partial text even when both arrive in one frame", async () => {
    stubFrames();
    const { result, stream } = setup();
    stream.mockImplementationOnce(async () =>
      play([accepted(), delta("Hel"), new NetworkError()]),
    );
    stream.mockImplementationOnce(async () =>
      play([accepted({ replayed: true }), delta("Hello world"), done()]),
    );
    await send(result, "Hi");
    expect(result.current.messages[1]?.content).toBe("Hello world");
  });
});
