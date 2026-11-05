import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getAccessToken,
  getAccessTokenVersion,
  setAccessToken,
  subscribeAccessToken,
} from "./auth-session";

afterEach(() => {
  setAccessToken(null);
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("auth-session", () => {
  it("starts empty and holds the token in memory", () => {
    expect(getAccessToken()).toBeNull();
    setAccessToken("token-value-1");
    expect(getAccessToken()).toBe("token-value-1");
    setAccessToken(null);
    expect(getAccessToken()).toBeNull();
  });

  it("treats an empty string as no token", () => {
    setAccessToken("something");
    setAccessToken("");
    expect(getAccessToken()).toBeNull();
  });

  it("never writes the token to localStorage, sessionStorage or cookies", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    setAccessToken("secret-token-value");
    expect(setItem).not.toHaveBeenCalled();
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(
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
    // The same token again is not a change.
    setAccessToken("token-of-user-a");
    expect(getAccessTokenVersion()).toBe(afterA);
    // Straight to another token, without a signed-out step in between.
    setAccessToken("token-of-user-b");
    const afterB = getAccessTokenVersion();
    expect(afterB).toBeGreaterThan(afterA);
    setAccessToken(null);
    expect(getAccessTokenVersion()).toBeGreaterThan(afterB);
    // Clearing an already empty holder changes nothing.
    const cleared = getAccessTokenVersion();
    setAccessToken(null);
    setAccessToken("");
    expect(getAccessTokenVersion()).toBe(cleared);
  });

  it("only ever increases the version", () => {
    const seen: number[] = [getAccessTokenVersion()];
    for (const token of ["a-token", "b-token", null, "a-token", "c-token"]) {
      setAccessToken(token);
      seen.push(getAccessTokenVersion());
    }
    for (let i = 1; i < seen.length; i += 1) {
      expect(seen[i]).toBeGreaterThan(seen[i - 1]);
    }
  });

  it("does not expose the token through the version", () => {
    setAccessToken("secret-value-in-memory");
    expect(typeof getAccessTokenVersion()).toBe("number");
    expect(String(getAccessTokenVersion())).not.toContain("secret");
  });

  it("notifies subscribers when one token replaces another", () => {
    const listener = vi.fn();
    setAccessToken("first-token-value");
    const unsubscribe = subscribeAccessToken(listener);
    setAccessToken("second-token-value");
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });
});
