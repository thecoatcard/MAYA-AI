import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityView } from "./ActivityView";
import * as systemClient from "../../lib/system-client";

vi.mock("../../lib/system-client", () => ({
  getPrincipalMe: vi.fn(),
  checkSystemHealth: vi.fn(),
}));

describe("ActivityView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(systemClient.getPrincipalMe).mockResolvedValue({
      user_id: "user-admin-123",
      workspace_id: "ws-test-456",
      role: "admin",
    });
  });

  it("renders audit ledger title and events with hash-chain indicators", async () => {
    render(<ActivityView />);

    expect(
      screen.getByText("Activity & Tamper-Evident Audit Ledger"),
    ).toBeInTheDocument();
    expect(screen.getByText("task.created")).toBeInTheDocument();
    expect(screen.getByText("tool.invoked")).toBeInTheDocument();
    expect(screen.getByText("approval.required")).toBeInTheDocument();
    expect(
      screen.getByText(/Continuous Hash-Chain: prev_hash → row_hash/i),
    ).toBeInTheDocument();
  });

  it("filters events by category", () => {
    render(<ActivityView />);

    // Click Approvals filter
    const approvalsTab = screen.getByRole("button", {
      name: "Approvals",
    });
    fireEvent.click(approvalsTab);

    expect(screen.getByText("approval.required")).toBeInTheDocument();
    expect(screen.queryByText("task.created")).toBeNull();
  });

  it("opens audit block inspector modal", () => {
    render(<ActivityView />);

    const inspectButtons = screen.getAllByRole("button", {
      name: /inspect block/i,
    });
    fireEvent.click(inspectButtons[0]);

    expect(screen.getByText(/Audit Block: task\.created/i)).toBeInTheDocument();
  });
});
