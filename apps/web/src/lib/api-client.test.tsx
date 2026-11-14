import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  acceptedFrame,
  collect,
  controllableStream,
  deltaFrame,
  doneFrame,
  frame,
  jsonResponse,
  sseResponse,
} from "../components/chat/chat-test-utils";
import {
  ApiError,
  DEFAULT_API_BASE_URL,
  NetworkError,
  countCodePoints,
  createIdempotencyKey,
  decodeChatEvent,
  deleteSession,
  getApiBaseUrl,
  listMessages,
  listSessions,
  streamChatMessage,
  validateChatContent,
} from "./api-client";
import { getAccessToken, setAccessToken } from "./auth-session";

const SESSION = "8f8b80e8-0b53-4f9e-99f5-748a2784cf22";

beforeEach(() => {
  setAccessToken("test-access-token");
});

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function stubFetch(response: Response | (() => Response)) {
  const fetchMock = vi.fn(async () =>
    typeof response === "function" ? response() : response,
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("base URL", () => {
  it("uses the same-origin proxy by default", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");
    expect(getApiBaseUrl()).toBe(DEFAULT_API_BASE_URL);
    expect(DEFAULT_API_BASE_URL).toBe("");
  });

  it("ignores browser-visible API origin configuration", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test//");
    expect(getApiBaseUrl()).toBe("");
  });
});

describe("same-origin API proxy", () => {
  it("sends the chat request to the same-origin path", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");
    const fetchMock = stubFetch(sseResponse([acceptedFrame(), doneFrame()]));
    await collect(
      await streamChatMessage({
        content: "Hi",
        idempotencyKey: "key-12345678",
      }),
    );
    const calls = fetchMock.mock.calls as unknown as Array<
      [string, RequestInit]
    >;
    expect(calls[0]?.[0]).toBe("/api/v1/chat/message");
    const [, init] = calls[0] ?? ["", {}];
    expect((init.headers as Record<string, string>).Authorization).toMatch(
      /^Bearer /,
    );
  });

  it("uses same-origin paths for the read endpoints", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.example.test");
    const fetchMock = stubFetch(() =>
      jsonResponse({ items: [], next_cursor: null }),
    );
    await listSessions();
    await listMessages(SESSION);
    const calls = fetchMock.mock.calls as unknown as Array<
      [string, RequestInit]
    >;
    expect(calls.map((call) => call[0])).toEqual([
      "/api/v1/chat/sessions",
      `/api/v1/chat/sessions/${SESSION}/messages`,
    ]);
  });
});

describe("streamChatMessage request", () => {
  it("POSTs JSON with the documented headers and body", async () => {
    const fetchMock = stubFetch(sseResponse([acceptedFrame(), doneFrame()]));
    const events = await streamChatMessage({
      content: "Hello",
      sessionId: SESSION,
      idempotencyKey: "key-12345678",
    });
    await collect(events);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/api/v1/chat/message`);
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers).toMatchObject({
      Authorization: "Bearer test-access-token",
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      "Idempotency-Key": "key-12345678",
    });
    expect(JSON.parse(init.body as string)).toEqual({
      content: "Hello",
      session_id: SESSION,
    });
    expect(init.credentials).toBe("omit");
    expect(init.cache).toBe("no-store");
  });

  it("omits session_id for a new conversation", async () => {
    const fetchMock = stubFetch(sseResponse([acceptedFrame(), doneFrame()]));
    await collect(
      await streamChatMessage({
        content: "Hi",
        idempotencyKey: "key-12345678",
      }),
    );
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(init.body as string)).toEqual({ content: "Hi" });
  });

  it("passes the AbortSignal to fetch", async () => {
    const fetchMock = stubFetch(sseResponse([doneFrame()]));
    const controller = new AbortController();
    await streamChatMessage({
      content: "Hi",
      idempotencyKey: "key-12345678",
      signal: controller.signal,
    });
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(init.signal).toBe(controller.signal);
  });

  it("fails locally with not_authenticated when there is no token", async () => {
    setAccessToken(null);
    const fetchMock = stubFetch(sseResponse([]));
    await expect(
      streamChatMessage({ content: "Hi", idempotencyKey: "key-12345678" }),
    ).rejects.toMatchObject({ status: 401, code: "not_authenticated" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("streamChatMessage events", () => {
  it("yields typed events in order", async () => {
    stubFetch(
      sseResponse([
        acceptedFrame({ sessionId: SESSION }),
        deltaFrame("Hel"),
        deltaFrame("lo"),
        doneFrame("stop"),
      ]),
    );
    const events = await collect(
      await streamChatMessage({
        content: "Hi",
        idempotencyKey: "key-12345678",
      }),
    );
    expect(events).toEqual([
      {
        type: "message.accepted",
        sessionId: SESSION,
        userMessageId: "user-msg-1",
        assistantMessageId: "assistant-msg-1",
        replayed: false,
      },
      { type: "delta", text: "Hel" },
      { type: "delta", text: "lo" },
      {
        type: "done",
        assistantMessageId: "assistant-msg-1",
        finishReason: "stop",
        usage: {
          prompt_tokens: 4,
          candidates_tokens: 6,
          total_tokens: 10,
          cost_usd: null,
          is_estimate: true,
        },
      },
    ]);
  });

  it("decodes task.created and ignores a malformed one", () => {
    expect(
      decodeChatEvent({
        event: "task.created",
        data: '{"task_id":"t1","title":"Do it","status":"executing"}',
        id: null,
        retry: null,
      }),
    ).toEqual({
      type: "task.created",
      taskId: "t1",
      title: "Do it",
      status: "executing",
      promoted: false,
    });
    expect(
      decodeChatEvent({
        event: "task.created",
        data: '{"title":"x"}',
        id: null,
        retry: null,
      }),
    ).toBeNull();
  });

  it("decodes promoted task.created", () => {
    expect(
      decodeChatEvent({
        event: "task.created",
        data: '{"task_id":"t2","title":"Long","status":"executing","promoted":true}',
        id: null,
        retry: null,
      }),
    ).toEqual({
      type: "task.created",
      taskId: "t2",
      title: "Long",
      status: "executing",
      promoted: true,
    });
  });

  it("decodes tool.started and tool.finished and ignores malformed ones", () => {
    expect(
      decodeChatEvent({
        event: "tool.started",
        data: '{"call_id":"c1","name":"create_file_inline"}',
        id: null,
        retry: null,
      }),
    ).toEqual({
      type: "tool.started",
      callId: "c1",
      name: "create_file_inline",
    });
    expect(
      decodeChatEvent({
        event: "tool.finished",
        data: '{"call_id":"c1","name":"create_file_inline","status":"skipped"}',
        id: null,
        retry: null,
      }),
    ).toEqual({
      type: "tool.finished",
      callId: "c1",
      name: "create_file_inline",
      status: "skipped",
    });
    expect(
      decodeChatEvent({
        event: "tool.started",
        data: '{"name":"create_file_inline"}',
        id: null,
        retry: null,
      }),
    ).toBeNull();
    expect(
      decodeChatEvent({
        event: "tool.finished",
        data: '{"call_id":"c1","name":"x","status":"maybe"}',
        id: null,
        retry: null,
      }),
    ).toBeNull();
  });

  it("decodes file.created into a file card and ignores a malformed one", () => {
    expect(
      decodeChatEvent({
        event: "file.created",
        data: '{"file_id":"f1","name":"notes.md","file_type":"md","mime_type":"text/markdown","size_bytes":120,"sha256":"abc","action":"created","source_upload_id":null}',
        id: null,
        retry: null,
      }),
    ).toEqual({
      type: "file.created",
      file: {
        fileId: "f1",
        name: "notes.md",
        fileType: "md",
        mimeType: "text/markdown",
        sizeBytes: 120,
        action: "created",
        sourceUploadId: null,
      },
    });
    expect(
      decodeChatEvent({
        event: "file.created",
        data: '{"file_id":"f1","name":"notes.md","size_bytes":120,"action":"deleted"}',
        id: null,
        retry: null,
      }),
    ).toBeNull();
  });

  it("decodes memory.saved and ignores a malformed one", () => {
    expect(
      decodeChatEvent({
        event: "memory.saved",
        data: '{"memory_id":"m1","text":"Favourite colour is blue","updated":true}',
        id: null,
        retry: null,
      }),
    ).toEqual({
      type: "memory.saved",
      memoryId: "m1",
      text: "Favourite colour is blue",
      updated: true,
    });
    for (const data of ['{"text":"x"}', '{"memory_id":"m1"}', "not json"]) {
      expect(
        decodeChatEvent({ event: "memory.saved", data, id: null, retry: null }),
      ).toBeNull();
    }
  });

  it("decodes agent.draft and ignores a malformed one", () => {
    const data =
      '{"name":"weekly-reporter","description":"d","path":".maya/agents/weekly-reporter.md","content":"---","capabilities":["read_files"],"instructions":"i"}';
    expect(
      decodeChatEvent({ event: "agent.draft", data, id: null, retry: null }),
    ).toEqual({
      type: "agent.draft",
      name: "weekly-reporter",
      description: "d",
      path: ".maya/agents/weekly-reporter.md",
      content: "---",
      capabilities: ["read_files"],
      instructions: "i",
    });
    for (const bad of ['{"name":"x"}', '{"path":"p","content":"c"}', "nope"]) {
      expect(
        decodeChatEvent({
          event: "agent.draft",
          data: bad,
          id: null,
          retry: null,
        }),
      ).toBeNull();
    }
  });

  it("ignores event names it does not know (for example Phase 05 task events)", async () => {
    stubFetch(
      sseResponse([
        acceptedFrame(),
        frame("task.started", { task_id: "t1" }),
        deltaFrame("ok"),
        frame("task.progress", { pct: 50 }),
        ": keep-alive\n\n",
        doneFrame(),
      ]),
    );
    const events = await collect(
      await streamChatMessage({
        content: "Hi",
        idempotencyKey: "key-12345678",
      }),
    );
    expect(events.map((event) => event.type)).toEqual([
      "message.accepted",
      "delta",
      "done",
    ]);
  });

  it("ignores malformed data for known events", async () => {
    stubFetch(
      sseResponse([
        "event: delta\ndata: not json\n\n",
        'event: delta\ndata: {"text": 5}\n\n',
        "event: delta\ndata: [1]\n\n",
        deltaFrame("fine"),
      ]),
    );
    const events = await collect(
      await streamChatMessage({
        content: "Hi",
        idempotencyKey: "key-12345678",
      }),
    );
    expect(events).toEqual([{ type: "delta", text: "fine" }]);
  });

  it("does not accept a terminal event after a malformed known event", async () => {
    stubFetch(
      sseResponse([
        acceptedFrame(),
        deltaFrame("partial"),
        'event: delta\ndata: {"text":\n\n',
        doneFrame(),
      ]),
    );
    const events = await streamChatMessage({
      content: "Hi",
      idempotencyKey: "key-12345678",
    });
    expect((await events.next()).value).toMatchObject({
      type: "message.accepted",
    });
    expect((await events.next()).value).toEqual({
      type: "delta",
      text: "partial",
    });
    await expect(events.next()).rejects.toMatchObject({
      code: "stream_protocol_error",
    });
  });

  it("rejects a malformed accepted event split at every byte boundary", async () => {
    const bytes = new TextEncoder().encode(
      frame("message.accepted", {
        session_id: SESSION,
        user_message_id: "user-1",
        assistant_message_id: "assistant-1",
        replayed: "true",
      }),
    );
    for (let cut = 1; cut < bytes.length; cut += 1) {
      stubFetch(
        sseResponse([bytes.slice(0, cut), bytes.slice(cut), doneFrame()]),
      );
      const events = await streamChatMessage({
        content: "Hi",
        idempotencyKey: "key-12345678",
      });
      await expect(events.next()).rejects.toMatchObject({
        code: "stream_protocol_error",
      });
    }
  });

  it("rejects a malformed terminal event instead of treating it as clean EOF", async () => {
    stubFetch(
      sseResponse([frame("done", { assistant_message_id: "assistant-1" })]),
    );
    const events = await streamChatMessage({
      content: "Hi",
      idempotencyKey: "key-12345678",
    });
    await expect(events.next()).rejects.toMatchObject({
      status: 200,
      code: "stream_protocol_error",
    });
  });

  it.each([
    [
      "accepted without a replay flag",
      "message.accepted",
      {
        session_id: "s",
        user_message_id: "u",
        assistant_message_id: "a",
      },
    ],
    [
      "accepted with a non-boolean replay flag",
      "message.accepted",
      {
        session_id: "s",
        user_message_id: "u",
        assistant_message_id: "a",
        replayed: "true",
      },
    ],
    ["done without a finish reason", "done", { assistant_message_id: "a" }],
    [
      "done with a non-string finish reason",
      "done",
      { assistant_message_id: "a", finish_reason: 1 },
    ],
    ["error without a retryable flag", "error", { code: "internal_error" }],
    [
      "error with a non-boolean retryable flag",
      "error",
      { code: "internal_error", retryable: "false" },
    ],
  ])("rejects %s", (_description, event, data) => {
    expect(
      decodeChatEvent({
        event,
        data: JSON.stringify(data),
        id: null,
        retry: null,
      }),
    ).toBeNull();
  });

  it("decodes the error event and keeps cost_usd null as null", () => {
    expect(
      decodeChatEvent({
        event: "error",
        data: '{"code":"stream_interrupted","retryable":true}',
        id: null,
        retry: null,
      }),
    ).toEqual({ type: "error", code: "stream_interrupted", retryable: true });
    const done = decodeChatEvent({
      event: "done",
      data: JSON.stringify({
        assistant_message_id: "a",
        finish_reason: "max_tokens",
        usage: {
          prompt_tokens: 1,
          candidates_tokens: 2,
          total_tokens: 3,
          cost_usd: "0.0012",
          is_estimate: false,
        },
      }),
      id: null,
      retry: null,
    });
    expect(done).toMatchObject({
      type: "done",
      finishReason: "max_tokens",
      usage: { cost_usd: "0.0012", is_estimate: false },
    });
  });

  it("throws NetworkError when the connection fails mid-stream", async () => {
    const controlled = controllableStream();
    stubFetch(sseResponse(controlled.stream));
    const events = await streamChatMessage({
      content: "Hi",
      idempotencyKey: "key-12345678",
    });
    controlled.push(acceptedFrame());
    const first = await events.next();
    expect(first.value).toMatchObject({ type: "message.accepted" });
    controlled.fail(new TypeError("network error"));
    await expect(events.next()).rejects.toBeInstanceOf(NetworkError);
  });

  it("throws AbortError (not NetworkError) when aborted mid-stream", async () => {
    const controlled = controllableStream();
    stubFetch(sseResponse(controlled.stream));
    const controller = new AbortController();
    const events = await streamChatMessage({
      content: "Hi",
      idempotencyKey: "key-12345678",
      signal: controller.signal,
    });
    const pending = events.next();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(controlled.cancelled).toBe(true);
  });
});

describe("streamChatMessage errors", () => {
  it.each([
    [400, "idempotency_key_required"],
    [401, "invalid_token"],
    [404, "session_not_found"],
    [409, "request_in_progress"],
    [422, "idempotency_key_reused"],
    [429, "concurrent_stream_limit"],
    [503, "model_unavailable"],
    [503, "chat_unavailable"],
  ])("maps %i %s to a typed ApiError", async (status, code) => {
    stubFetch(jsonResponse({ error: code }, status));
    const error = await streamChatMessage({
      content: "Hi",
      idempotencyKey: "key-12345678",
    }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status, code, retryAfterSeconds: null });
  });

  it("keeps validation fields and never echoes submitted values", async () => {
    stubFetch(
      jsonResponse({ error: "validation_error", fields: ["content"] }, 422),
    );
    const error = (await streamChatMessage({
      content: "secret body",
      idempotencyKey: "key-12345678",
    }).catch((e: unknown) => e)) as ApiError;
    expect(error.fields).toEqual(["content"]);
    expect(error.message).not.toContain("secret body");
  });

  it("reads Retry-After on 429", async () => {
    stubFetch(
      jsonResponse({ error: "rate_limited" }, 429, { "Retry-After": "17" }),
    );
    const error = (await streamChatMessage({
      content: "Hi",
      idempotencyKey: "key-12345678",
    }).catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({
      status: 429,
      code: "rate_limited",
      retryAfterSeconds: 17,
    });
  });

  describe("refresh races", () => {
    it("does not replace a newer token when an unauthorized request has a stale refresh", async () => {
      let resolveRefresh: ((response: Response) => void) | undefined;
      const refreshResponse = new Promise<Response>((resolve) => {
        resolveRefresh = resolve;
      });
      const fetchMock = vi.fn((url: string) => {
        if (url === "/api/v1/auth/refresh") {
          return refreshResponse;
        }
        return Promise.resolve(new Response(null, { status: 401 }));
      });
      vi.stubGlobal("fetch", fetchMock);
      setAccessToken("account-a-token");

      const pending = streamChatMessage({
        content: "Hi",
        idempotencyKey: "key-12345678",
      });
      await vi.waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          "/api/v1/auth/refresh",
          expect.anything(),
        );
      });
      setAccessToken("account-b-token");
      resolveRefresh?.(
        jsonResponse({
          access_token: "example-stale-account-a-token",
          token_type: "bearer",
          expires_in: 900,
        }),
      );

      await expect(pending).rejects.toMatchObject({
        status: 401,
        code: "unknown_error",
      });
      expect(getAccessToken()).toBe("account-b-token");
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
  });

  it("reads Retry-After on the concurrent stream limit", async () => {
    stubFetch(
      jsonResponse({ error: "concurrent_stream_limit" }, 429, {
        "Retry-After": "1",
      }),
    );
    const error = (await streamChatMessage({
      content: "Hi",
      idempotencyKey: "key-12345678",
    }).catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({
      status: 429,
      code: "concurrent_stream_limit",
      retryAfterSeconds: 1,
    });
  });

  it("falls back to unknown_error for a body that is not the documented JSON", async () => {
    stubFetch(new Response("<html>bad gateway</html>", { status: 502 }));
    const error = (await streamChatMessage({
      content: "Hi",
      idempotencyKey: "key-12345678",
    }).catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({ status: 502, code: "unknown_error" });
    expect(error.message).not.toContain("bad gateway");
  });

  it("rejects an unusable error code instead of trusting it", async () => {
    stubFetch(jsonResponse({ error: "<script>alert(1)</script>" }, 500));
    const error = (await streamChatMessage({
      content: "Hi",
      idempotencyKey: "key-12345678",
    }).catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("unknown_error");
  });

  it("rejects a 200 that is not an event stream", async () => {
    stubFetch(jsonResponse({ ok: true }, 200));
    await expect(
      streamChatMessage({ content: "Hi", idempotencyKey: "key-12345678" }),
    ).rejects.toMatchObject({ code: "unexpected_response" });
  });

  it("turns a failed fetch into NetworkError but passes an abort through", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    await expect(
      streamChatMessage({ content: "Hi", idempotencyKey: "key-12345678" }),
    ).rejects.toBeInstanceOf(NetworkError);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        throw error;
      }),
    );
    await expect(
      streamChatMessage({ content: "Hi", idempotencyKey: "key-12345678" }),
    ).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("read endpoints", () => {
  it("lists sessions with limit and cursor", async () => {
    const fetchMock = stubFetch(
      jsonResponse({
        items: [
          {
            id: "s1",
            title: "First",
            channel: "web",
            status: "active",
            created_at: "2026-11-05T10:00:00Z",
          },
          { nope: true },
        ],
        next_cursor: "abc",
      }),
    );
    const page = await listSessions({ limit: 5, cursor: "c1" });
    expect(page.next_cursor).toBe("abc");
    expect(page.items).toHaveLength(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(
      `${DEFAULT_API_BASE_URL}/api/v1/chat/sessions?limit=5&cursor=c1`,
    );
    expect(init.method).toBe("GET");
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer test-access-token",
    );
  });

  it("retains sessions whose valid title is null with a display fallback", async () => {
    stubFetch(
      jsonResponse({
        items: [
          {
            id: "untitled",
            title: null,
            channel: "web",
            status: "active",
            created_at: "2026-11-05T10:00:00Z",
          },
        ],
        next_cursor: null,
      }),
    );
    expect((await listSessions()).items).toEqual([
      {
        id: "untitled",
        title: "Untitled conversation",
        channel: "web",
        status: "active",
        created_at: "2026-11-05T10:00:00Z",
      },
    ]);
  });

  it("lists messages of a session", async () => {
    const fetchMock = stubFetch(
      jsonResponse({
        items: [
          { id: "m1", role: "user", content: "Hi", created_at: "t" },
          {
            id: "m2",
            role: "assistant",
            content: "Hello",
            created_at: "t",
            finish_reason: "stop",
            model_id: "gemini",
          },
        ],
        next_cursor: null,
      }),
    );
    const page = await listMessages(SESSION);
    expect(page.items.map((item) => item.role)).toEqual(["user", "assistant"]);
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toBe(
      `${DEFAULT_API_BASE_URL}/api/v1/chat/sessions/${SESSION}/messages`,
    );
  });

  it("maps a 404 to session_not_found and rejects odd bodies", async () => {
    stubFetch(jsonResponse({ error: "session_not_found" }, 404));
    await expect(listMessages(SESSION)).rejects.toMatchObject({
      status: 404,
      code: "session_not_found",
    });
    stubFetch(jsonResponse({ unexpected: true }));
    await expect(listSessions()).rejects.toMatchObject({
      code: "unexpected_response",
    });
  });

  it("encodes the session id in the path", async () => {
    const fetchMock = stubFetch(() =>
      jsonResponse({ items: [], next_cursor: null }),
    );
    await listMessages("a/b?c");
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toContain("/sessions/a%2Fb%3Fc/messages");
  });
});

describe("content validation and keys", () => {
  it("counts code points, not UTF-16 units", () => {
    expect(countCodePoints("abc")).toBe(3);
    expect(countCodePoints("😀😀")).toBe(2);
    expect(countCodePoints("a😀")).toBe(2);
  });

  it("applies the documented content rules", () => {
    expect(validateChatContent("")).toBe("empty");
    expect(validateChatContent("   \n")).toBe("empty");
    expect(validateChatContent("a".repeat(50_000))).toBeNull();
    expect(validateChatContent("a".repeat(1_000_000))).toBeNull();
    expect(validateChatContent("😀".repeat(20_000))).toBeNull();
    expect(validateChatContent("line one\nline two\tTabbed")).toBeNull();
    expect(validateChatContent("bad\u0000char")).toBe("invalid_characters");
    expect(validateChatContent("bad\u001bescape")).toBe("invalid_characters");
    expect(validateChatContent("carriage\rreturn")).toBe("invalid_characters");
  });

  it("creates keys that fit the documented format and differ each time", () => {
    const keys = new Set(Array.from({ length: 50 }, createIdempotencyKey));
    expect(keys.size).toBe(50);
    for (const key of keys) {
      expect(key).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    }
  });
});

describe("deleteSession", () => {
  it("sends DELETE to /api/v1/chat/sessions/{id} with bearer token", async () => {
    const fetchMock = stubFetch(new Response(null, { status: 204 }));
    await deleteSession(SESSION);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(`/api/v1/chat/sessions/${SESSION}`);
    expect(init.method).toBe("DELETE");
    expect((init.headers as Record<string, string>)["Authorization"]).toBe(
      "Bearer test-access-token",
    );
  });

  it("encodes the session id in the path", async () => {
    const fetchMock = stubFetch(new Response(null, { status: 204 }));
    await deleteSession("a/b?c");
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toBe("/api/v1/chat/sessions/a%2Fb%3Fc");
  });

  it("throws ApiError when the server returns 404 session_not_found", async () => {
    stubFetch(jsonResponse({ error: "session_not_found" }, 404));
    await expect(deleteSession(SESSION)).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      code: "session_not_found",
    });
  });

  it("requires an access token", async () => {
    setAccessToken(null);
    await expect(deleteSession(SESSION)).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      code: "not_authenticated",
    });
  });
});
