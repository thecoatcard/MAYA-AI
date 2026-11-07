import type { Metadata } from "next";
import React from "react";

import { AppShell } from "../../components/layout/AppShell";
import { McpGatewayView } from "../../components/mcp/McpGatewayView";

export const metadata: Metadata = {
  title: "MCP Integrations Gateway | MAYA AI",
  description:
    "Model Context Protocol integrations, server health monitoring, and tool permission registries.",
};

export default function IntegrationsPage() {
  return (
    <AppShell title="MCP Integrations Gateway" breadcrumb="Workspace">
      <McpGatewayView />
    </AppShell>
  );
}
