import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as useAuthHook from "../../hooks/useAuth";
import { AuthView } from "./AuthView";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
  }),
}));

describe("AuthView", () => {
  const mockLogout = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a loading skeleton when authentication check is in progress", () => {
    vi.spyOn(useAuthHook, "useAuth").mockReturnValue({
      user: null,
      token: null,
      isAuthenticated: false,
      isLoading: true,
      logout: mockLogout,
      refresh: vi.fn(),
      checkSession: vi.fn(),
    });

    render(<AuthView initialStep="password" />);

    expect(
      screen.getByLabelText("Checking authentication state"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Active Session Found")).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Username or email"),
    ).not.toBeInTheDocument();
  });

  it("displays helpful session message, Continue button and Sign Out option when authenticated", async () => {
    vi.spyOn(useAuthHook, "useAuth").mockReturnValue({
      user: {
        role: "admin",
        user_id: "usr_maya_999",
        workspace_id: "ws_default",
      },
      token: "valid-token",
      isAuthenticated: true,
      isLoading: false,
      logout: mockLogout,
      refresh: vi.fn(),
      checkSession: vi.fn(),
    });

    render(<AuthView initialStep="password" />);

    expect(screen.getByText("Active Session Found")).toBeInTheDocument();
    expect(
      screen.getByText(/You are already signed in as/i),
    ).toBeInTheDocument();
    expect(screen.getByText("admin (usr_maya_999)")).toBeInTheDocument();

    const workspaceBtn = screen.getByRole("link", {
      name: /Continue to Workspace/i,
    });
    expect(workspaceBtn).toBeInTheDocument();
    expect(workspaceBtn).toHaveAttribute("href", "/workspace");

    const switchAccountBtn = screen.getByRole("button", {
      name: /Sign in with a different account/i,
    });
    expect(switchAccountBtn).toBeInTheDocument();

    fireEvent.click(switchAccountBtn);
    await waitFor(() => {
      expect(mockLogout).toHaveBeenCalledTimes(1);
    });
  });

  it("renders the embedded sign-in form when unauthenticated", () => {
    vi.spyOn(useAuthHook, "useAuth").mockReturnValue({
      user: null,
      token: null,
      isAuthenticated: false,
      isLoading: false,
      logout: mockLogout,
      refresh: vi.fn(),
      checkSession: vi.fn(),
    });

    render(<AuthView initialStep="password" />);

    expect(
      screen.getByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Username or email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("renders the embedded registration form when unauthenticated on signup step", () => {
    vi.spyOn(useAuthHook, "useAuth").mockReturnValue({
      user: null,
      token: null,
      isAuthenticated: false,
      isLoading: false,
      logout: mockLogout,
      refresh: vi.fn(),
      checkSession: vi.fn(),
    });

    render(<AuthView initialStep="signup_details" />);

    expect(
      screen.getByRole("heading", { name: "Create an account" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Email address")).toBeInTheDocument();
    expect(screen.getByLabelText("Full name")).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Continue" }),
    ).toBeInTheDocument();
  });
});
