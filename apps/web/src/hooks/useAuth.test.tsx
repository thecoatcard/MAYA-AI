import { act, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as authClient from "../lib/auth-client";
import * as authSession from "../lib/auth-session";
import * as systemClient from "../lib/system-client";
import { AuthProvider, useAuth } from "./useAuth";

const mockPush = vi.fn();
const mockReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: mockReplace,
  }),
}));

describe("useAuth hook and AuthProvider", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authSession.setAccessToken(null);
  });

  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <AuthProvider>{children}</AuthProvider>
  );

  it("manages initial loading state and session resolution when unauthenticated", async () => {
    let resolveRefresh: (val: null) => void = () => {};
    const refreshPromise = new Promise<null>((resolve) => {
      resolveRefresh = resolve;
    });
    vi.spyOn(authSession, "refreshSession").mockReturnValue(refreshPromise);

    const { result } = renderHook(() => useAuth(), { wrapper });

    // Initial state must be loading
    expect(result.current.isLoading).toBe(true);
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();

    // Resolve refresh to unauthenticated (null)
    act(() => {
      resolveRefresh(null);
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
    expect(result.current.token).toBeNull();
  });

  it("resolves authenticated state when token and principal exist on mount", async () => {
    authSession.setAccessToken("placeholder-valid-token", 900);

    const principalMock: systemClient.UserPrincipal = {
      user_id: "usr-456",
      workspace_id: "ws-789",
      role: "owner",
    };
    vi.spyOn(systemClient, "getPrincipalMe").mockResolvedValue(principalMock);

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
      expect(result.current.isAuthenticated).toBe(true);
    });

    expect(result.current.user).toEqual(principalMock);
    expect(result.current.token).toBe("placeholder-valid-token");
  });

  it("restores user session when initial token is null but refreshSession succeeds", async () => {
    vi.spyOn(authSession, "refreshSession").mockImplementation(async () => {
      authSession.setAccessToken("placeholder-refreshed-token", 900);
      return { accessToken: "placeholder-refreshed-token", expiresIn: 900 };
    });

    const principalMock: systemClient.UserPrincipal = {
      user_id: "usr-999",
      workspace_id: "ws-888",
      role: "admin",
    };
    vi.spyOn(systemClient, "getPrincipalMe").mockResolvedValue(principalMock);

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
      expect(result.current.isAuthenticated).toBe(true);
    });

    expect(result.current.user?.role).toBe("admin");
    expect(result.current.user?.user_id).toBe("usr-999");
    expect(result.current.user?.workspace_id).toBe("ws-888");
  });

  it("sets unauthenticated state when refreshSession fails or rejects", async () => {
    vi.spyOn(authSession, "refreshSession").mockRejectedValue(
      new Error("Refresh rejected"),
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
    expect(result.current.token).toBeNull();
  });

  it("handles logout by revoking session, resetting state, and navigating to login", async () => {
    authSession.setAccessToken("active-token", 900);
    vi.spyOn(systemClient, "getPrincipalMe").mockResolvedValue({
      user_id: "usr-1",
      workspace_id: "ws-1",
      role: "member",
    });
    const signOutSpy = vi.spyOn(authClient, "signOut").mockResolvedValue();

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isAuthenticated).toBe(true);
    });

    await act(async () => {
      await result.current.logout();
    });

    expect(signOutSpy).toHaveBeenCalled();
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
    expect(result.current.token).toBeNull();
    expect(mockPush).toHaveBeenCalledWith("/login");
  });

  it("navigates to custom route on logout when specified", async () => {
    authSession.setAccessToken("active-token", 900);
    vi.spyOn(systemClient, "getPrincipalMe").mockResolvedValue({
      user_id: "usr-1",
      workspace_id: "ws-1",
      role: "member",
    });
    vi.spyOn(authClient, "signOut").mockResolvedValue();

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isAuthenticated).toBe(true);
    });

    await act(async () => {
      await result.current.logout("/custom-auth");
    });

    expect(mockPush).toHaveBeenCalledWith("/custom-auth");
  });

  it("subscribes to token changes across tabs or external auth actions", async () => {
    vi.spyOn(authSession, "refreshSession").mockResolvedValue(null);
    const principalMock: systemClient.UserPrincipal = {
      user_id: "usr-tab2",
      workspace_id: "ws-tab2",
      role: "member",
    };
    const getPrincipalSpy = vi
      .spyOn(systemClient, "getPrincipalMe")
      .mockResolvedValue(principalMock);

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
    expect(result.current.isAuthenticated).toBe(false);

    // Simulate cross-tab login or token arrival
    await act(async () => {
      authSession.setAccessToken("peer-token", 900);
    });

    await waitFor(() => {
      expect(result.current.isAuthenticated).toBe(true);
      expect(result.current.user).toEqual(principalMock);
    });
    expect(getPrincipalSpy).toHaveBeenCalled();

    // Simulate cross-tab logout
    await act(async () => {
      authSession.setAccessToken(null);
    });

    await waitFor(() => {
      expect(result.current.isAuthenticated).toBe(false);
      expect(result.current.user).toBeNull();
    });
  });

  it("provides manual refresh capability", async () => {
    authSession.setAccessToken("old-token", 900);
    vi.spyOn(systemClient, "getPrincipalMe").mockResolvedValue({
      user_id: "usr-1",
      workspace_id: "ws-1",
      role: "member",
    });

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isAuthenticated).toBe(true);
    });

    // Mock rotated session and updated principal
    const refreshedMock: systemClient.UserPrincipal = {
      user_id: "usr-1",
      workspace_id: "ws-1",
      role: "lead",
    };
    vi.spyOn(authSession, "refreshSession").mockImplementation(async () => {
      authSession.setAccessToken("placeholder-rotated-token", 900);
      return { accessToken: "placeholder-rotated-token", expiresIn: 900 };
    });
    vi.spyOn(systemClient, "getPrincipalMe").mockResolvedValue(refreshedMock);

    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.user?.role).toBe("lead");
    expect(result.current.token).toBe("placeholder-rotated-token");
  });

  it("provides fallback values when called outside AuthProvider", () => {
    const { result } = renderHook(() => useAuth());
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });
});
