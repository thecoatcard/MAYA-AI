import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as authSession from "../../lib/auth-session";
import * as authClient from "../../lib/auth-client";
import * as systemClient from "../../lib/system-client";
import { AuthProvider } from "../../hooks/useAuth";
import { PublicNav } from "./PublicNav";

describe("PublicNav authentication-aware navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authSession.setAccessToken(null);
  });

  it("renders Sign in and Create account when visitor is logged out", async () => {
    render(
      <AuthProvider>
        <PublicNav />
      </AuthProvider>,
    );

    // Should find public links once loading settles
    expect(
      await screen.findByRole("link", { name: /Sign in/i }),
    ).toHaveAttribute("href", "/login");
    expect(
      screen.getByRole("link", { name: /Create account/i }),
    ).toHaveAttribute("href", "/register");

    // Should not render authenticated dropdown
    expect(screen.queryByText(/Console Dashboard/i)).toBeNull();
  });

  it("does not render Sign in or Create account when user is authenticated, and shows Workspace", async () => {
    // Simulate active session
    authSession.setAccessToken("mock-valid-token", 900);
    vi.spyOn(systemClient, "getPrincipalMe").mockResolvedValue({
      user_id: "u-123",
      workspace_id: "w-456",
      role: "admin",
    });

    render(
      <AuthProvider>
        <PublicNav />
      </AuthProvider>,
    );

    // Workspace link must be present once loading settles
    expect(
      await screen.findByRole("link", { name: /Workspace/i }),
    ).toHaveAttribute("href", "/workspace");

    // Sign in and Create account MUST NOT be shown
    expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Create account" })).toBeNull();

    // User profile badge should show user role
    expect(await screen.findByText("admin")).toBeInTheDocument();

    // Clicking user menu opens dropdown
    const menuBtn = screen.getByText("admin").closest("button");
    expect(menuBtn).not.toBeNull();
    fireEvent.click(menuBtn!);

    expect(screen.getByText("Console Dashboard")).toBeInTheDocument();
    expect(screen.getByText("Task Center")).toBeInTheDocument();
    expect(screen.getByText("Approvals")).toBeInTheDocument();
    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Sign Out/i }),
    ).toBeInTheDocument();
  });

  it("triggers logout and updates navigation state", async () => {
    authSession.setAccessToken("mock-valid-token", 900);
    vi.spyOn(systemClient, "getPrincipalMe").mockResolvedValue({
      user_id: "u-123",
      workspace_id: "w-456",
      role: "engineer",
    });
    const signOutSpy = vi.spyOn(authClient, "signOut").mockResolvedValue();

    render(
      <AuthProvider>
        <PublicNav />
      </AuthProvider>,
    );

    const roleBadge = await screen.findByText("engineer");
    fireEvent.click(roleBadge.closest("button")!);

    const signOutBtn = screen.getByRole("button", { name: /Sign Out/i });
    fireEvent.click(signOutBtn);

    expect(signOutSpy).toHaveBeenCalled();
  });
});
