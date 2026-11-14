import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { McpGatewayView } from "./McpGatewayView";
import * as mcpClient from "../../lib/mcp-client";

vi.mock("../../lib/mcp-client", () => ({
  listMcpServers: vi.fn(),
  getMcpServerHealth: vi.fn(),
  registerMcpServer: vi.fn(),
  deleteMcpServer: vi.fn(),
}));

describe("McpGatewayView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders core integration cards and title", async () => {
    vi.mocked(mcpClient.listMcpServers).mockResolvedValueOnce([]);

    render(<McpGatewayView />);

    expect(
      screen.getByRole("heading", { name: "Connections", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByText("Suggestions, not connected")).toBeInTheDocument();
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    expect(screen.getByText("Notion")).toBeInTheDocument();
    expect(screen.getByText("Google Workspace")).toBeInTheDocument();
  });

  it("renders servers and refreshes their status", async () => {
    const mockServers: mcpClient.McpServer[] = [
      {
        id: "srv-123",
        workspace_id: "ws-1",
        user_id: "u-1",
        name: "custom_analytics_mcp",
        transport: "stdio",
        command: "python",
        is_active: true,
        health_status: "healthy",
        tool_count: 3,
        consecutive_failures: 0,
        schema_hash: "sha256:abc123def456",
        created_at: "2026-11-07T10:00:00Z",
      },
    ];

    vi.mocked(mcpClient.listMcpServers).mockResolvedValueOnce(mockServers);
    vi.mocked(mcpClient.getMcpServerHealth).mockResolvedValueOnce({
      server_id: "srv-123",
      name: "custom_analytics_mcp",
      health_status: "healthy",
      last_ping_at: "2026-11-07T12:00:00Z",
      consecutive_failures: 0,
      tool_count: 3,
    });

    render(<McpGatewayView />);

    await waitFor(() => {
      expect(screen.getByText("custom_analytics_mcp")).toBeInTheDocument();
    });

    // Click the status button
    const pingButton = screen.getByRole("button", { name: "Refresh status" });
    fireEvent.click(pingButton);

    await waitFor(() => {
      expect(mcpClient.getMcpServerHealth).toHaveBeenCalledWith("srv-123");
    });
  });

  it("opens register server modal", async () => {
    vi.mocked(mcpClient.listMcpServers).mockResolvedValueOnce([]);

    render(<McpGatewayView />);

    const registerBtn = screen.getByRole("button", {
      name: /register server/i,
    });
    fireEvent.click(registerBtn);

    expect(
      screen.getByText("Register a server", { selector: "h2" }),
    ).toBeInTheDocument();
  });

  it("offers only the transports the backend accepts and no invented defaults", () => {
    vi.mocked(mcpClient.listMcpServers).mockResolvedValueOnce([]);
    render(<McpGatewayView />);
    fireEvent.click(screen.getByRole("button", { name: /register server/i }));
    const select = screen.getByLabelText("Transport") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual([
      "stdio",
      "streamable_http",
    ]);
    expect(screen.getByLabelText(/Pinned version/)).toHaveValue("");
  });

  it("shows an error with retry when the list fails, never an empty list", async () => {
    vi.mocked(mcpClient.listMcpServers)
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce([]);
    render(<McpGatewayView />);
    expect(
      await screen.findByText("Your servers could not be loaded"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No servers registered")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(
      await screen.findByText("No servers registered"),
    ).toBeInTheDocument();
  });

  it("shows Unknown for an unrecognised status, and never echoes secrets", async () => {
    vi.mocked(mcpClient.listMcpServers).mockResolvedValueOnce([
      {
        id: "s1",
        workspace_id: "w",
        user_id: "u",
        name: "remote",
        transport: "streamable_http",
        url: "https://user:pw@example.com/mcp?token=SECRET123",
        is_active: true,
        health_status: "weird",
        tool_count: 0,
        consecutive_failures: 0,
      },
      {
        id: "s2",
        workspace_id: "w",
        user_id: "u",
        name: "local",
        transport: "stdio",
        command: "node",
        args: ["server.js", "--api-key=ABC999"],
        is_active: true,
        health_status: "dead",
        tool_count: 1,
        consecutive_failures: 2,
        pinned_version: "1.0.0",
        schema_hash: "sha256:abc",
      },
    ]);
    render(<McpGatewayView />);
    expect(
      await screen.findByText("Unknown", { selector: "span" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/SECRET123|user:pw|ABC999/);
    expect(text).toContain("example.com/mcp");
    expect(text).toMatch(/needs a pinned version and a schema hash/);
  });

  it("asks before removing a server", async () => {
    vi.mocked(mcpClient.listMcpServers).mockResolvedValue([
      {
        id: "s2",
        workspace_id: "w",
        user_id: "u",
        name: "local",
        transport: "stdio",
        command: "node",
        is_active: true,
        health_status: "healthy",
        tool_count: 1,
        consecutive_failures: 0,
      },
    ]);
    vi.mocked(mcpClient.deleteMcpServer).mockResolvedValue(undefined);
    render(<McpGatewayView />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Remove local" }),
    );
    expect(mcpClient.deleteMcpServer).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Remove server" }));
    await waitFor(() =>
      expect(mcpClient.deleteMcpServer).toHaveBeenCalledWith("s2"),
    );
  });
});
