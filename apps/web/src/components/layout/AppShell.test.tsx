import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";
import { useAuth } from "../../hooks/useAuth";
import { checkSystemHealth } from "../../lib/system-client";
import {
  listApprovals,
  type ApprovalRequest,
} from "../../lib/approvals-client";

const mockPush = vi.fn();
const mockReplace = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => "/workspace",
  useRouter: () => ({
    push: mockPush,
    replace: mockReplace,
  }),
}));

const mockLogout = vi.fn().mockResolvedValue(undefined);

vi.mock("../../hooks/useAuth", () => ({
  useAuth: vi.fn(),
}));

vi.mock("../../lib/system-client", () => ({
  checkSystemHealth: vi.fn(),
}));

vi.mock("../../lib/approvals-client", () => ({
  listApprovals: vi.fn(),
}));

describe("AppShell", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.mocked(useAuth).mockReturnValue({
      user: {
        user_id: "usr-alice",
        workspace_id: "ws-primary-corp",
        role: "admin",
      },
      token: "placeholder-jwt-token",
      isAuthenticated: true,
      isLoading: false,
      checkSession: vi.fn().mockResolvedValue(true),
      refresh: vi.fn().mockResolvedValue(undefined),
      logout: mockLogout,
    });

    vi.mocked(checkSystemHealth).mockResolvedValue({ status: "ready" });
    vi.mocked(listApprovals).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders brand, navigation links, title, user role, and workspace id when authenticated", async () => {
    render(
      <AppShell title="Test Title" breadcrumb="Test Section">
        <div>Content Body</div>
      </AppShell>,
    );

    expect(screen.getByText("MAYA AI")).toBeInTheDocument();
    expect(screen.getByText("Test Title")).toBeInTheDocument();
    expect(screen.getByText("Test Section")).toBeInTheDocument();
    expect(screen.getByText("Content Body")).toBeInTheDocument();

    // Verify user profile card in sidebar footer
    const profileCard = screen.getByTestId("user-profile-card");
    expect(profileCard).toBeInTheDocument();
    expect(profileCard).toHaveTextContent("usr-alice");
    expect(profileCard).toHaveTextContent("admin");
    expect(profileCard).toHaveTextContent("ws-primary-corp");

    // Verify nav links exist
    expect(
      screen.getByRole("link", { name: /Workspace/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Tasks/i })).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Approvals/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Deliverables/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Agents/i })).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Integrations/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Memory & Knowledge/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Activity & Audit/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Settings/i })).toBeInTheDocument();
  });

  it("redirects unauthenticated user to login route with redirect target", async () => {
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      token: null,
      isAuthenticated: false,
      isLoading: false,
      checkSession: vi.fn().mockResolvedValue(false),
      refresh: vi.fn().mockResolvedValue(undefined),
      logout: mockLogout,
    });

    render(
      <AppShell title="Restricted" breadcrumb="Workspace">
        <div>Restricted Content</div>
      </AppShell>,
    );

    expect(screen.getByTestId("appshell-redirecting")).toBeInTheDocument();
    expect(screen.queryByText("Restricted Content")).not.toBeInTheDocument();
    expect(mockPush).toHaveBeenCalledWith("/login?redirect=%2Fworkspace");
  });

  it("displays a translucent loading skeleton when isLoading is true", () => {
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      token: null,
      isAuthenticated: false,
      isLoading: true,
      checkSession: vi.fn().mockResolvedValue(false),
      refresh: vi.fn().mockResolvedValue(undefined),
      logout: mockLogout,
    });

    render(
      <AppShell title="Dashboard">
        <div>Content While Loading</div>
      </AppShell>,
    );

    expect(screen.getByTestId("appshell-loading")).toBeInTheDocument();
    expect(screen.queryByText("Content While Loading")).not.toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("signs out and redirects to /login when clicking sign out button", async () => {
    render(
      <AppShell title="My Workspace">
        <div>Content Area</div>
      </AppShell>,
    );

    const signOutBtn = screen.getByRole("button", { name: "Sign out" });
    fireEvent.click(signOutBtn);

    await waitFor(() => {
      expect(mockLogout).toHaveBeenCalledTimes(1);
      expect(mockPush).toHaveBeenCalledWith("/login");
    });
  });

  it("renders pending approvals badge when listApprovals returns pending items", async () => {
    vi.mocked(listApprovals).mockResolvedValue([
      {
        id: "app-1",
        workspace_id: "ws-1",
        user_id: "u-1",
        task_id: "tsk-1",
        subtask_id: "st-1",
        tool_name: "deploy",
        tool_args: {},
        payload_hash: "hash1",
        tier: "APPROVAL_REQUIRED",
        action_description: "Deploy production build",
        status: "pending",
        requested_at: new Date().toISOString(),
        expires_at: new Date().toISOString(),
      },
      {
        id: "app-2",
        workspace_id: "ws-1",
        user_id: "u-1",
        task_id: "tsk-2",
        subtask_id: "st-2",
        tool_name: "db_migrate",
        tool_args: {},
        payload_hash: "hash2",
        tier: "RESTRICTED",
        action_description: "Execute schema migration",
        status: "pending",
        requested_at: new Date().toISOString(),
        expires_at: new Date().toISOString(),
      },
    ] as ApprovalRequest[]);

    render(
      <AppShell title="Overview">
        <div>Content</div>
      </AppShell>,
    );

    await waitFor(() => {
      expect(screen.getByText("2 Approvals")).toBeInTheDocument();
    });
  });

  it("opens NewTaskModal when clicking New Task button", async () => {
    render(
      <AppShell title="Overview">
        <div>Content</div>
      </AppShell>,
    );

    const newTaskBtn = screen.getByRole("button", { name: /New Task/i });
    fireEvent.click(newTaskBtn);

    await waitFor(() => {
      expect(screen.getByText("Assign Autonomous Task")).toBeInTheDocument();
    });
  });
});
