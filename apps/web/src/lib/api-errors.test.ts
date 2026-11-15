import { afterEach, describe, expect, it, vi } from "vitest";

import {
  BackendUnreachableError,
  apiErrorFromResponse,
  checkBackendReady,
} from "./api-client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiErrorFromResponse", () => {
  it("surfaces the backend envelope message", async () => {
    const error = await apiErrorFromResponse(
      new Response(
        JSON.stringify({ error: "bad_request", message: "Title is required" }),
        { status: 400 },
      ),
    );
    expect(error.code).toBe("bad_request");
    expect(error.message).toBe("Title is required");
  });

  it("throws BackendUnreachableError for the bare proxy text", async () => {
    await expect(
      apiErrorFromResponse(
        new Response("Internal Server Error", { status: 500 }),
      ),
    ).rejects.toBeInstanceOf(BackendUnreachableError);
  });
});

describe("checkBackendReady", () => {
  it("reports ready", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ status: "ready" }), { status: 200 }),
    );
    await expect(checkBackendReady(fetchMock as never)).resolves.toEqual({
      ready: true,
    });
  });

  it("reports not ready with a reason on 503 and on network failure", async () => {
    const down = vi.fn(
      async () =>
        new Response(JSON.stringify({ status: "not_ready" }), { status: 503 }),
    );
    await expect(checkBackendReady(down as never)).resolves.toEqual({
      ready: false,
      reason: "not_ready",
    });
    const boom = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const res = await checkBackendReady(boom as never);
    expect(res.ready).toBe(false);
  });
});
