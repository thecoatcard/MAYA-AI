import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryView } from "./MemoryView";
import * as systemClient from "../../lib/system-client";

vi.mock("../../lib/system-client", () => ({
  getPrincipalMe: vi.fn(),
  checkSystemHealth: vi.fn(),
}));

describe("MemoryView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(systemClient.getPrincipalMe).mockResolvedValue({
      user_id: "u-999",
      workspace_id: "ws-888",
      role: "owner",
    });
  });

  it("renders memory repository and working/long-term architecture", () => {
    render(<MemoryView />);

    expect(
      screen.getByText("Memory & Knowledge Repository"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Working Memory (Active Session)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Long-Term Memory (pgvector Embeddings)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Executive Tone & Deliverable Formats"),
    ).toBeInTheDocument();
  });

  it("filters memory entries by category", () => {
    render(<MemoryView />);

    const userPrefTab = screen.getByRole("button", {
      name: "User Preferences",
    });
    fireEvent.click(userPrefTab);

    expect(
      screen.getByText("Executive Tone & Deliverable Formats"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("MCP RFC 8785 Canonical JSON Serialization"),
    ).toBeNull();
  });

  it("opens purge modal and requires PURGE confirmation", () => {
    render(<MemoryView />);

    const purgeButton = screen.getByRole("button", {
      name: /purge memory/i,
    });
    fireEvent.click(purgeButton);

    expect(
      screen.getByText("Confirm Data Purge (GDPR / CCPA)"),
    ).toBeInTheDocument();
    const eraseButton = screen.getByRole("button", {
      name: "Erase All Memory Items",
    });
    expect(eraseButton).toBeDisabled();

    const input = screen.getByPlaceholderText("Type PURGE");
    fireEvent.change(input, { target: { value: "PURGE" } });

    expect(eraseButton).not.toBeDisabled();
  });
});
