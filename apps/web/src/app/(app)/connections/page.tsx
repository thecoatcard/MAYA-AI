import type { Metadata } from "next";
import React from "react";

import { McpGatewayView } from "../../../components/mcp/McpGatewayView";
import { DecisionHealthRow } from "../../../components/settings/DecisionHealthRow";

export const metadata: Metadata = {
  title: "Connections | MAYA AI",
  description: "Connected tools and their health.",
};

export default function ConnectionsPage() {
  return (
    <>
      <McpGatewayView />
      <div className="mx-auto w-full max-w-3xl px-4 pb-6">
        <DecisionHealthRow />
      </div>
    </>
  );
}
