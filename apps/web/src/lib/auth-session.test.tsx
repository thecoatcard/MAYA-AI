import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getAccessToken,
  getAccessTokenVersion,
  getAuthIdentityVersion,
  refreshSession,
  setAccessToken,
  subscribeAccessToken,
  type RefreshedSession,
} from "./auth-session";

afterEach(() => {
  setAccessToken(null);
  window.localStorage.clear();
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("auth-session", () => {
  it("holds only the access token in memory", () => {
    expect(getAccessToken()).toBeNull();
    setAccessToken("token-value-1");
    expect(getAccessToken()).toBe("token-value-1");
    setAccessToken(null);
    expect(getAccessToken()).toBeNull();
  });

  it("never persists an access token in web storage or a readable cookie", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    setAccessToken("secret-token-value");
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(
      "secret-token-value",
    );
    expect(JSON.stringify(setItem.mock.calls)).not.toContain(
      "secret-token-value",
    );
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(
      "secret-token-value",
    );
    expect(document.cookie).not.toContain("secret-token-value");
    setItem.mockRestore();
  });

  it("notifies subscribers only when the token changes, until they unsubscribe", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeAccessToken(listener);
    setAccessToken("a-token-value");
    setAccessToken("a-token-value");
    expect(listener).toHaveBeenCalledTimes(1);
    setAccessToken(null);
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    setAccessToken("b-token-value");
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("changes its version on every real change, including token to token", () => {
    const start = getAccessTokenVersion();
    setAccessToken("token-of-user-a");
    const afterA = getAccessTokenVersion();
    expect(afterA).toBeGreaterThan(start);
    setAccessToken("token-of-user-a");
    expect(getAccessTokenVersion()).toBe(afterA);
    setAccessToken("token-of-user-b");
    const afterB = getAccessTokenVersion();
    expect(afterB).toBeGreaterThan(afterA);
    setAccessToken(null);
    expect(getAccessTokenVersion()).toBeGreaterThan(afterB);
    const cleared = getAccessTokenVersion();
    setAccessToken(null);
    setAccessToken("");
    expect(getAccessTokenVersion()).toBe(cleared);
  });

  it("does not expose the token through the version", () => {
    setAccessToken("secret-value-in-memory");
    expect(typeof getAccessTokenVersion()).toBe("number");
    expect(String(getAccessTokenVersion())).not.toContain("secret");
  });

  it("sends a same-origin cookie refresh and shares one in-flight request", async () => {
    let resolveResponse: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveResponse = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const first = refreshSession();
    const second = refreshSession();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(init).toMatchObject({
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
    });
    expect(init.body).toBeUndefined();
    expect(init.headers).toMatchObject({
      "X-Maya-Refresh-Transport": "cookie",
    });
    resolveResponse?.(
      new Response(
        JSON.stringify({
          access_token: "example-access-token-refreshed",
          token_type: "bearer",
          expires_in: 900,
        }),
        { status: 200 },
      ),
    );
    await expect(Promise.all([first, second])).resolves.toEqual([
      { accessToken: "example-access-token-refreshed", expiresIn: 900 },
      { accessToken: "example-access-token-refreshed", expiresIn: 900 },
    ]);
    expect(getAccessToken()).toBe("example-access-token-refreshed");
  });

  it("uses an origin-scoped Web Lock for cookie refresh when available", async () => {
    const originalDescriptor = Object.getOwnPropertyDescriptor(
      navigator,
      "locks",
    );
    const request = vi.fn(
      (
        _name: string,
        callback: (lock: Lock | null) => Promise<RefreshedSession | null>,
      ) => Promise.resolve(callback(null)),
    );
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: { request },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              access_token: "example-locked-refresh-token",
              token_type: "bearer",
              expires_in: 900,
            }),
            { status: 200 },
          ),
      ),
    );
    try {
      await expect(refreshSession()).resolves.toEqual({
        accessToken: "example-locked-refresh-token",
        expiresIn: 900,
      });
      expect(request).toHaveBeenCalledWith(
        "maya-auth-refresh-cookie",
        expect.any(Function),
      );
    } finally {
      if (originalDescriptor === undefined) {
        Reflect.deleteProperty(navigator, "locks");
      } else {
        Object.defineProperty(navigator, "locks", originalDescriptor);
      }
    }
  });

  it("updates a refreshed access token without changing the sign-in identity", async () => {
    setAccessToken("example-access-token-previous");
    const identity = getAuthIdentityVersion();
    const tokenVersion = getAccessTokenVersion();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              access_token: "example-access-token-rotated",
              token_type: "bearer",
              expires_in: 900,
            }),
            { status: 200 },
          ),
      ),
    );

    await expect(refreshSession()).resolves.toEqual({
      accessToken: "example-access-token-rotated",
      expiresIn: 900,
    });
    expect(getAccessToken()).toBe("example-access-token-rotated");
    expect(getAccessTokenVersion()).toBeGreaterThan(tokenVersion);
    expect(getAuthIdentityVersion()).toBe(identity);
  });

  it("clears the in-memory token when the refresh cookie is invalid", async () => {
    setAccessToken("example-access-token-previous");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 401 })),
    );

    await expect(refreshSession()).resolves.toBeNull();
    expect(getAccessToken()).toBeNull();
  });

  it("does not apply a late refresh after a newer identity is installed", async () => {
    let resolveResponse: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveResponse = resolve;
          }),
      ),
    );
    setAccessToken("account-a-token");
    const pending = refreshSession();

    setAccessToken("account-b-token");
    const currentIdentity = getAuthIdentityVersion();
    resolveResponse?.(
      new Response(
        JSON.stringify({
          access_token: "example-stale-account-a-token",
          token_type: "bearer",
          expires_in: 900,
        }),
        { status: 200 },
      ),
    );

    await expect(pending).resolves.toBeNull();
    expect(getAccessToken()).toBe("account-b-token");
    expect(getAuthIdentityVersion()).toBe(currentIdentity);
  });

  it("does not restore a signed-out identity when refresh completes late", async () => {
    let resolveResponse: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveResponse = resolve;
          }),
      ),
    );
    setAccessToken("account-a-token");
    const pending = refreshSession();

    setAccessToken(null);
    const signedOutIdentity = getAuthIdentityVersion();
    resolveResponse?.(
      new Response(
        JSON.stringify({
          access_token: "example-late-account-a-token",
          token_type: "bearer",
          expires_in: 900,
        }),
        { status: 200 },
      ),
    );

    await expect(pending).resolves.toBeNull();
    expect(getAccessToken()).toBeNull();
    expect(getAuthIdentityVersion()).toBe(signedOutIdentity);
  });

  it("does not let a late unauthorized refresh sign out a newer identity", async () => {
    let resolveResponse: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveResponse = resolve;
          }),
      ),
    );
    setAccessToken("account-a-token");
    const pending = refreshSession();

    setAccessToken("account-b-token");
    const currentIdentity = getAuthIdentityVersion();
    resolveResponse?.(new Response(null, { status: 401 }));

    await expect(pending).resolves.toBeNull();
    expect(getAccessToken()).toBe("account-b-token");
    expect(getAuthIdentityVersion()).toBe(currentIdentity);
  });

  it("clears the in-memory identity after another tab signals an identity change", () => {
    setAccessToken("current-tab-token");
    const currentIdentity = getAuthIdentityVersion();

    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "maya-auth-identity-change",
        newValue: "another-tab-change",
      }),
    );

    expect(getAccessToken()).toBeNull();
    expect(getAuthIdentityVersion()).toBeGreaterThan(currentIdentity);
  });
});
