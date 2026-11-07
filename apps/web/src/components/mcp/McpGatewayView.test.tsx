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
      screen.getByText("Model Context Protocol (MCP) Gateway"),
    ).toBeInTheDocument();
    expect(screen.getByText("Brave Search")).toBeInTheDocument();
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    expect(screen.getByText("Notion")).toBeInTheDocument();
    expect(screen.getByText("Google Workspace")).toBeInTheDocument();
  });

  it("renders tenant servers and enables ping health check", async () => {
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

    // Click Ping button
    const pingButton = screen.getByRole("button", { name: "Ping" });
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

    expect(screen.getByText("Register Custom MCP Server")).toBeInTheDocument();
  });
});
