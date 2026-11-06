import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError } from "./api-client";
import { getAccessToken, refreshSession, setAccessToken } from "./auth-session";
import {
  completeSignup,
  identifyUser,
  registerAccount,
  requestPasswordReset,
  resendVerification,
  resetPassword,
  sendSignupOtp,
  signIn,
  signOut,
  verifyEmail,
} from "./auth-client";

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
});

function installQueuedWebLocks() {
  const originalDescriptor = Object.getOwnPropertyDescriptor(
    navigator,
    "locks",
  );
  let locked = false;
  const queue: Array<() => void> = [];
  const request = vi.fn(
    (
      _name: string,
      callback: (lock: Lock | null) => unknown,
    ): Promise<unknown> =>
      new Promise((resolve, reject) => {
        const enter = () => {
          locked = true;
          Promise.resolve()
            .then(() => callback(null))
            .then(
              (value) => {
                locked = false;
                resolve(value);
                queue.shift()?.();
              },
              (error: unknown) => {
                locked = false;
                reject(error);
                queue.shift()?.();
              },
            );
        };
        if (locked) {
          queue.push(enter);
        } else {
          enter();
        }
      }),
  );
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: { request },
  });
  return {
    request,
    restore: () => {
      if (originalDescriptor === undefined) {
        Reflect.deleteProperty(navigator, "locks");
      } else {
        Object.defineProperty(navigator, "locks", originalDescriptor);
      }
    },
  };
}

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

describe("auth cookie mutation races", () => {
  it("orders a pending refresh before sign-in so the sign-in cookie wins", async () => {
    const locks = installQueuedWebLocks();
    let resolveRefresh: ((response: Response) => void) | undefined;
    let cookieOwner = "account-a";
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/refresh")) {
        return new Promise<Response>((resolve) => {
          resolveRefresh = resolve;
        });
      }
      if (url.endsWith("/token")) {
        cookieOwner = "account-b";
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "example-account-b-access",
              token_type: "bearer",
              expires_in: 900,
            }),
            { status: 200 },
          ),
        );
      }
      throw new Error("Unexpected request");
    });
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("example-account-a-access");

    try {
      const refresh = refreshSession();
      await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
      const signInPending = signIn("account-b", "test-password-placeholder");
      await Promise.resolve();
      expect(fetchMock).toHaveBeenCalledTimes(1);

      cookieOwner = "account-a-refreshed";
      resolveRefresh?.(
        new Response(
          JSON.stringify({
            access_token: "example-account-a-refreshed-access",
            token_type: "bearer",
            expires_in: 900,
          }),
          { status: 200 },
        ),
      );
      await refresh;
      await expect(signInPending).resolves.toEqual({
        accessToken: "example-account-b-access",
        expiresIn: 900,
      });

      expect(cookieOwner).toBe("account-b");
      expect(getAccessToken()).toBe("example-account-b-access");
      expect(fetchMock.mock.calls.map((call) => String(call[0]))).toEqual([
        "/api/v1/auth/refresh",
        "/api/v1/auth/token",
      ]);
      expect(locks.request).toHaveBeenCalledTimes(2);
    } finally {
      locks.restore();
    }
  });

  it("orders logout after a pending refresh and leaves no signed-in token", async () => {
    const locks = installQueuedWebLocks();
    let resolveRefresh: ((response: Response) => void) | undefined;
    let cookieOwner: string | null = "account-a";
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/refresh")) {
        return new Promise<Response>((resolve) => {
          resolveRefresh = resolve;
        });
      }
      if (url.endsWith("/logout")) {
        cookieOwner = null;
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      throw new Error("Unexpected request");
    });
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("account-a-access");

    try {
      const refresh = refreshSession();
      await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
      const signOutPending = signOut();
      await Promise.resolve();
      expect(fetchMock).toHaveBeenCalledTimes(1);

      cookieOwner = "account-a-refreshed";
      resolveRefresh?.(
        new Response(
          JSON.stringify({
            access_token: "example-account-a-refreshed-access",
            token_type: "bearer",
            expires_in: 900,
          }),
          { status: 200 },
        ),
      );
      await refresh;
      await signOutPending;

      expect(cookieOwner).toBeNull();
      expect(getAccessToken()).toBeNull();
      expect(fetchMock.mock.calls.map((call) => String(call[0]))).toEqual([
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
      ]);
      expect(locks.request).toHaveBeenCalledTimes(2);
    } finally {
      locks.restore();
    }
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

  it("handles identifyUser, sendSignupOtp and completeSignup", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/identify")) {
        return new Response(JSON.stringify({ status: "accepted" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/signup/otp")) {
        return new Response('{"status":"accepted"}', {
          status: 202,
          headers: { "Retry-After": "60" },
        });
      }
      if (url.endsWith("/signup/complete")) {
        return new Response(
          JSON.stringify({
            access_token: "fake-access-token-placeholder",
            token_type: "bearer",
            expires_in: 900,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("{}", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(identifyUser("newbie@example.com")).resolves.toBeUndefined();

    const otp = await sendSignupOtp("newbie@example.com");
    expect(otp).toEqual({
      status: "accepted",
      retryAfterSeconds: 60,
    });

    const tokens = await completeSignup({
      email: "newbie@example.com",
      code: "123456",
      username: "newbie",
      password: "fake-password-placeholder",
      full_name: "Newbie Person",
    });
    expect(tokens).toEqual({
      accessToken: "fake-access-token-placeholder",
      expiresIn: 900,
    });
  });

  it("applies signup tokens while holding the shared cookie lock", async () => {
    const locks = installQueuedWebLocks();
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            access_token: "example-signup-access-token",
            token_type: "bearer",
            expires_in: 900,
          }),
          { status: 200 },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);
    try {
      await completeSignup({
        email: "newbie@example.com",
        code: "123456",
        username: "newbie",
        password: "test-password-placeholder",
        full_name: "Newbie Person",
      });
      expect(getAccessToken()).toBe("example-signup-access-token");
      expect(locks.request).toHaveBeenCalledWith(
        "maya-auth-refresh-cookie",
        expect.any(Function),
      );
    } finally {
      locks.restore();
    }
  });
});
