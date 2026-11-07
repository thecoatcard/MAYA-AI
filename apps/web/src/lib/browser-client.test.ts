import { afterEach, describe, expect, it, vi } from "vitest";

import {
  dispatchBrowserAction,
  getBrowserTakeoverState,
  resumeBrowserSession,
} from "./browser-client";
import { setAccessToken } from "./auth-session";

describe("browser-client", () => {
  afterEach(() => {
    setAccessToken(null);
    vi.unstubAllGlobals();
  });

  it("retrieves human takeover state", async () => {
    setAccessToken("browser-token", 3600);
    const mockState = {
      session_id: "sess-123",
      status: "awaiting_human_takeover",
      current_url: "https://example.com/login",
      challenge_detected: true,
      challenge_type: "Cloudflare Turnstile",
      screenshot_base64: "dGVzdC1pbWFnZQ==",
      message: "Human takeover required",
    };

    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(mockState), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const state = await getBrowserTakeoverState("sess-123");
    expect(state.status).toBe("awaiting_human_takeover");
    expect(state.challenge_detected).toBe(true);
    expect(state.challenge_type).toBe("Cloudflare Turnstile");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/browser/session/sess-123/takeover",
      expect.objectContaining({
        headers: expect.any(Headers),
      }),
    );
  });

  it("resumes session following human resolution", async () => {
    setAccessToken("browser-token", 3600);
    const mockResume = {
      session_id: "sess-123",
      status: "resumed",
      current_url: "https://example.com/dashboard",
      message: "Resumed",
    };

    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(mockResume), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await resumeBrowserSession("sess-123");
    expect(res.status).toBe("resumed");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/browser/session/sess-123/resume",
      expect.objectContaining({
        method: "POST",
      }),
    );
  });

  it("dispatches browser action via POST", async () => {
    setAccessToken("browser-token", 3600);
    const mockResult = {
      success: true,
      action_type: "navigate",
      current_url: "https://example.com",
      bot_detected: false,
      execution_time_ms: 250,
    };

    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(mockResult), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await dispatchBrowserAction({
      action_type: "navigate",
      target_url: "https://example.com",
    });
    expect(res.success).toBe(true);
    expect(res.current_url).toBe("https://example.com");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/browser/action",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          action_type: "navigate",
          target_url: "https://example.com",
        }),
      }),
    );
  });
});
