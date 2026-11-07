import type { Metadata } from "next";
import React from "react";

import { AgentFleetView } from "../../components/agents/AgentFleetView";
import { AppShell } from "../../components/layout/AppShell";

export const metadata: Metadata = {
  title: "Agent Operations Center | MAYA AI",
  description:
    "Fleet management, permission ceilings, and sandbox runner isolation for MAYA specialist agents.",
};

export default function AgentsPage() {
  return (
    <AppShell title="Agent Fleet Operations" breadcrumb="Workspace">
      <AgentFleetView />
    </AppShell>
  );
}
