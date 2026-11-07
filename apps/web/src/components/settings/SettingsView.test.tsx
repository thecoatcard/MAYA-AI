import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsView } from "./SettingsView";
import * as systemClient from "../../lib/system-client";

vi.mock("../../lib/auth-client", () => ({
  signOut: vi.fn(),
}));

vi.mock("../../lib/system-client", () => ({
  getPrincipalMe: vi.fn(),
  checkSystemHealth: vi.fn(),
}));

describe("SettingsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(systemClient.getPrincipalMe).mockResolvedValue({
      user_id: "usr_maya_specialist",
      workspace_id: "ws_maya_enterprise",
      role: "admin",
    });
    vi.mocked(systemClient.checkSystemHealth).mockResolvedValue({
      status: "ready",
    });
  });

  it("renders settings header and principal identity", async () => {
    render(<SettingsView />);

    expect(
      screen.getByText("System & Workspace Preferences"),
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("usr_maya_specialist")).toBeInTheDocument();
      expect(screen.getByText("ws_maya_enterprise")).toBeInTheDocument();
    });
  });

  it("switches to Model Routing tab and shows Gemini models", () => {
    render(<SettingsView />);

    const routingTab = screen.getByRole("button", { name: "Model Routing" });
    fireEvent.click(routingTab);

    expect(
      screen.getByText("Gemini Model Gateway Routing (Decision D13)"),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText(/gemini-3.8-flash/i).length,
    ).toBeGreaterThanOrEqual(1);
  });

  it("switches to Danger Zone and opens session revocation modal", () => {
    render(<SettingsView />);

    const dangerTab = screen.getByRole("button", { name: "Danger Zone" });
    fireEvent.click(dangerTab);

    expect(
      screen.getByText("Danger Zone & Session Revocation"),
    ).toBeInTheDocument();

    const revokeBtn = screen.getByRole("button", { name: /revoke sessions/i });
    fireEvent.click(revokeBtn);

    expect(screen.getByText("Confirm Session Revocation")).toBeInTheDocument();
  });
});
