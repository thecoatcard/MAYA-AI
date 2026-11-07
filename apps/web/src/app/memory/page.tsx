import type { Metadata } from "next";
import React from "react";

import { AppShell } from "../../components/layout/AppShell";
import { MemoryView } from "../../components/memory/MemoryView";

export const metadata: Metadata = {
  title: "Memory & Knowledge Repository | MAYA AI",
  description:
    "Working memory context and long-term vector embeddings repository with source citations.",
};

export default function MemoryPage() {
  return (
    <AppShell title="Memory & Knowledge" breadcrumb="Workspace">
      <MemoryView />
    </AppShell>
  );
}
