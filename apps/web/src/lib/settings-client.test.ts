import { afterEach, describe, expect, it, vi } from "vitest";

import { setAccessToken } from "./auth-session";
import { getChatSettings, updateChatSettings } from "./settings-client";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("settings-client", () => {
  afterEach(() => {
    setAccessToken(null);
    vi.unstubAllGlobals();
  });

  it("reads chat settings with the bearer token", async () => {
    setAccessToken("tok", 3600);
    const fetchMock = vi.fn(async () =>
      json({ intent_classifier: true, classifier_available: false }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const settings = await getChatSettings();
    expect(settings).toEqual({
      intent_classifier: true,
      classifier_available: false,
      memory_in_chat: true, // absent from an older server: on, the default
    });
    const call = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(call[0]).toBe("/api/v1/settings/chat");
    expect(new Headers(call[1].headers).get("Authorization")).toBe(
      "Bearer tok",
    );
  });

  it("updates with PUT and only the intent_classifier body", async () => {
    setAccessToken("tok", 3600);
    const fetchMock = vi.fn(async () =>
      json({ intent_classifier: true, classifier_available: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const settings = await updateChatSettings({ intent_classifier: true });
    expect(settings.intent_classifier).toBe(true);
    const call = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(call[1].method).toBe("PUT");
    expect(call[1].body).toBe(JSON.stringify({ intent_classifier: true }));
  });

  it("reads memory_in_chat false and sends only the field given", async () => {
    setAccessToken("tok", 3600);
    const fetchMock = vi.fn(async () =>
      json({
        intent_classifier: false,
        classifier_available: true,
        memory_in_chat: false,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const settings = await updateChatSettings({ memory_in_chat: false });
    expect(settings.memory_in_chat).toBe(false);
    const call = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(call[1].body).toBe(JSON.stringify({ memory_in_chat: false }));
  });

  it("throws on a server error", async () => {
    setAccessToken("tok", 3600);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ error: { code: "internal_error" } }, 500)),
    );
    await expect(getChatSettings()).rejects.toBeDefined();
  });
});
