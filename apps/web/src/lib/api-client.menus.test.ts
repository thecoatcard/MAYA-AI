import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  duplicateSession,
  exportSessionMarkdown,
  isUnsupported,
  listSessions,
  sendMessageFeedback,
  streamChatMessage,
  updateSession,
} from "./api-client";
import { setAccessToken } from "./auth-session";

const SESSION = {
  id: "s1",
  title: "T",
  channel: "web",
  status: "active",
  created_at: "2026-11-01T00:00:00Z",
  pinned: true,
  archived: false,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => setAccessToken("tok"));
afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
});

function stub(res: Response): ReturnType<typeof vi.fn> {
  const f = vi.fn(async () => res);
  vi.stubGlobal("fetch", f);
  return f;
}

describe("conversation action clients (backend contract)", () => {
  it("lists with archived=true or all, default sends no query", async () => {
    const f = stub(json({ items: [SESSION], next_cursor: null }));
    const page = await listSessions({ archived: "true" });
    expect(String(f.mock.calls[0]?.[0])).toContain("/sessions?archived=true");
    expect(page.items[0]?.pinned).toBe(true);
    const g = stub(json({ items: [], next_cursor: null }));
    await listSessions();
    expect(String(g.mock.calls[0]?.[0])).toMatch(/\/sessions$/);
  });

  it("PATCHes only the given fields and returns the session", async () => {
    const f = stub(json({ ...SESSION, title: "New" }));
    const saved = await updateSession("s1", { title: "New" });
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/sessions/s1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ title: "New" });
    expect(saved.title).toBe("New");
  });

  it("duplicates with and without from_message_id", async () => {
    const f = stub(json({ ...SESSION, id: "s2", title: "T (copy)" }, 201));
    const copy = await duplicateSession("s1", "m9");
    const [url, init] = f.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/sessions/s1/duplicate");
    expect(JSON.parse(String(init.body))).toEqual({ from_message_id: "m9" });
    expect(copy.id).toBe("s2");
    stub(json({ ...SESSION, id: "s3" }, 201));
    await duplicateSession("s1");
  });

  it("exports Markdown text", async () => {
    const f = stub(
      new Response("# Title", {
        status: 200,
        headers: { "Content-Type": "text/markdown" },
      }),
    );
    expect(await exportSessionMarkdown("s1")).toBe("# Title");
    expect(String(f.mock.calls[0]?.[0])).toContain(
      "/sessions/s1/export?format=md",
    );
  });

  it("sends feedback (204) and clears it with null", async () => {
    const f = stub(new Response(null, { status: 204 }));
    await sendMessageFeedback("m1", "up");
    await sendMessageFeedback("m1", null);
    const bodies = f.mock.calls.map((c) =>
      JSON.parse(String((c[1] as RequestInit).body)),
    );
    expect(bodies).toEqual([{ value: "up" }, { value: null }]);
  });

  it("a 404 or 501 is reported as unsupported so the UI hides the action", async () => {
    stub(json({ error: "not_found" }, 404));
    const err = await updateSession("s1", { pinned: true }).catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ApiError);
    expect(isUnsupported(err)).toBe(true);
    stub(json({ error: "chat_unavailable" }, 503));
    const other = await updateSession("s1", { pinned: true }).catch(
      (e: unknown) => e,
    );
    expect(isUnsupported(other)).toBe(false);
  });

  it("source_message_id goes in the message body next to session_id", async () => {
    const f = stub(
      new Response("", {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );
    await streamChatMessage({
      content: "Make this a PDF.",
      idempotencyKey: "k",
      sessionId: "s1",
      sourceMessageId: "m1",
    });
    const body = JSON.parse(String((f.mock.calls[0]?.[1] as RequestInit).body));
    expect(body).toEqual({
      content: "Make this a PDF.",
      session_id: "s1",
      source_message_id: "m1",
    });
  });
});
