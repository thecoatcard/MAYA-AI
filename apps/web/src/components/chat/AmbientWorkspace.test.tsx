import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AmbientWorkspace } from "./AmbientWorkspace";
import { listArtifacts } from "../../lib/artifacts-client";
import { checkSystemHealth } from "../../lib/system-client";
import { listApprovals } from "../../lib/approvals-client";
import { useAuth } from "../../hooks/useAuth";

const mockPush = vi.fn();
const mockReplace = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => "/workspace",
  useRouter: () => ({
    push: mockPush,
    replace: mockReplace,
  }),
}));

vi.mock("../../hooks/useAuth", () => ({
  useAuth: vi.fn(),
}));

vi.mock("../../lib/system-client", () => ({
  checkSystemHealth: vi.fn(),
}));

vi.mock("../../lib/approvals-client", () => ({
  listApprovals: vi.fn(),
}));

vi.mock("../../lib/artifacts-client", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../../lib/artifacts-client")>();
  return {
    ...actual,
    listArtifacts: vi.fn(),
  };
});

describe("AmbientWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.mocked(useAuth).mockReturnValue({
      user: {
        user_id: "usr-ambient-1",
        workspace_id: "ws-ambient-main",
        role: "owner",
      },
      token: "placeholder-valid-token",
      isAuthenticated: true,
      isLoading: false,
      checkSession: vi.fn().mockResolvedValue(true),
      logout: vi.fn().mockResolvedValue(undefined),
      refresh: vi.fn().mockResolvedValue(undefined),
    });

    vi.mocked(checkSystemHealth).mockResolvedValue({ status: "ready" });
    vi.mocked(listApprovals).mockResolvedValue([]);
    vi.mocked(listArtifacts).mockResolvedValue([
      {
        id: "art-ambient-1",
        workspace_id: "ws-ambient-main",
        task_id: "tsk-001",
        user_id: "usr-ambient-1",
        file_name: "architecture_spec.pdf",
        file_type: "pdf",
        size_bytes: 4096,
        sha256: "abcdef1234567890abcdef1234567890",
        verification_status: "verified",
        verification_report: { pages: 12 },
        created_at: new Date().toISOString(),
      },
    ]);
  });

  it("renders AmbientWorkspace with AppShell navigation and breadcrumb", async () => {
    render(<AmbientWorkspace />);

    // AppShell Title and Breadcrumb
    expect(screen.getByText("Ambient Workspace")).toBeInTheDocument();
    expect(screen.getByText("Home")).toBeInTheDocument();

    // AppShell Brand and Navigation single source of truth
    expect(screen.getByText("MAYA AI")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Workspace/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Tasks/i })).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Deliverables/i }),
    ).toBeInTheDocument();

    // User profile in sidebar footer
    const profileCard = screen.getByTestId("user-profile-card");
    expect(profileCard).toBeInTheDocument();
    expect(profileCard).toHaveTextContent("usr-ambient-1");
    expect(profileCard).toHaveTextContent("owner");
    expect(profileCard).toHaveTextContent("ws-ambient-main");
  });

  it("ensures no nested double-sidebar or double-header conflicts exist", async () => {
    render(<AmbientWorkspace />);

    // There should only be ONE main brand element (inside AppShell, not inside ChatWorkspace)
    const brandElements = screen.getAllByText("MAYA AI");
    expect(brandElements).toHaveLength(1);

    // There should be NO redundant "Conversations" sidebar header from ChatWorkspace
    expect(screen.queryByText("Conversations")).not.toBeInTheDocument();
  });

  it("renders VoiceOrb in the top banner and DeliverableCanvas split pane", async () => {
    render(<AmbientWorkspace />);

    // VoiceOrb element rendered
    expect(
      screen.getByRole("button", { name: /Voice Assistant/i }),
    ).toBeInTheDocument();

    // DeliverableCanvas loaded and displaying deliverables
    await waitFor(() => {
      expect(screen.getByText("Deliverables")).toBeInTheDocument();
      expect(
        screen.getAllByText("architecture_spec.pdf").length,
      ).toBeGreaterThanOrEqual(1);
    });

    // Close canvas via close button
    const closeBtn = screen.getByRole("button", {
      name: /Close Deliverable Canvas/i,
    });
    fireEvent.click(closeBtn);

    // Canvas toggle button appears in banner
    await waitFor(() => {
      expect(
        screen.getByRole("button", {
          name: /Deliverable Canvas \(1\)/i,
        }),
      ).toBeInTheDocument();
    });

    // Reopen canvas via banner button
    const reopenBtn = screen.getByRole("button", {
      name: /Deliverable Canvas \(1\)/i,
    });
    fireEvent.click(reopenBtn);

    await waitFor(() => {
      expect(
        screen.getAllByText("architecture_spec.pdf").length,
      ).toBeGreaterThanOrEqual(1);
    });
  });

  it("renders AmbientWorkspace cleanly without redirecting when visitor is unauthenticated", async () => {
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      token: null,
      isAuthenticated: false,
      isLoading: false,
      checkSession: vi.fn().mockResolvedValue(false),
      logout: vi.fn().mockResolvedValue(undefined),
      refresh: vi.fn().mockResolvedValue(undefined),
    });

    render(<AmbientWorkspace />);

    // Renders the workspace and VoiceOrb without triggering redirect
    expect(screen.getByText("Ambient Workspace")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Voice Assistant/i }),
    ).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
    expect(mockReplace).not.toHaveBeenCalled();
  });
});
