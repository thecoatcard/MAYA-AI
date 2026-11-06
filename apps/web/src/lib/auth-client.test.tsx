import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError } from "./api-client";
import {
  registerAccount,
  requestPasswordReset,
  resendVerification,
  resetPassword,
  signIn,
  signOut,
  verifyEmail,
} from "./auth-client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("signIn", () => {
  it("posts credentials to the same-origin auth endpoint without browser persistence", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            access_token: "fake-access-token-placeholder",
            token_type: "bearer",
            expires_in: 900,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      signIn("maya-user", "fake-password-placeholder"),
    ).resolves.toEqual({
      accessToken: "fake-access-token-placeholder",
      expiresIn: 900,
    });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("/api/v1/auth/token");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      username: "maya-user",
      password: "fake-password-placeholder",
    });
    expect(init.credentials).toBe("same-origin");
    expect(init.cache).toBe("no-store");
    expect(init.headers).toMatchObject({
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Maya-Refresh-Transport": "cookie",
    });
  });

  it("converts authentication errors without exposing response text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "invalid_credentials" }), {
            status: 401,
          }),
      ),
    );

    const error = await signIn("maya-user", "password-value").catch(
      (reason: unknown) => reason,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 401,
      code: "invalid_credentials",
    });
    expect((error as Error).message).not.toContain("fake-password-placeholder");
  });

  it("rejects malformed success responses and network failures explicitly", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ access_token: "only" }))),
    );
    await expect(
      signIn("maya-user", "fake-password-placeholder"),
    ).rejects.toThrow("unexpected shape");

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network unavailable");
      }),
    );
    await expect(
      signIn("maya-user", "fake-password-placeholder"),
    ).rejects.toBeInstanceOf(NetworkError);
  });
});

describe("signOut", () => {
  it("revokes the HttpOnly refresh cookie without putting a token in the request", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(signOut()).resolves.toBeUndefined();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("/api/v1/auth/logout");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(init.credentials).toBe("same-origin");
    expect(init.headers).toMatchObject({
      "X-Maya-Refresh-Transport": "cookie",
    });
  });
});

describe("account requests", () => {
  it("posts the existing account flows and reads the server resend cooldown", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/resend-verification")) {
        return new Response('{"status":"accepted"}', {
          status: 202,
          headers: { "Retry-After": "47" },
        });
      }
      const status =
        url.endsWith("/verify-email") || url.endsWith("/reset-password")
          ? 200
          : 202;
      return new Response('{"status":"accepted"}', { status });
    });
    vi.stubGlobal("fetch", fetchMock);

    await registerAccount({
      username: "maya-user",
      email: "person@example.com",
      password: "example-password",
      full_name: "Maya User",
    });
    await verifyEmail({
      email: "person@example.com",
      code: "123456",
      password: "example-password",
    });
    await expect(resendVerification("person@example.com")).resolves.toEqual({
      status: "accepted",
      retryAfterSeconds: 47,
    });
    await requestPasswordReset("person@example.com");
    await resetPassword({
      email: "person@example.com",
      code: "123456",
      new_password: "new-example-password",
    });
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });
});
