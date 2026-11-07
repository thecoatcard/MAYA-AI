import type { Metadata } from "next";
import React from "react";

import { ArtifactsManagerView } from "../../components/artifacts/ArtifactsManagerView";
import { AppShell } from "../../components/layout/AppShell";

export const metadata: Metadata = {
  title: "Deliverables & Artifacts | MAYA AI",
  description:
    "Cryptographically verified documents, spreadsheets, presentations, and code deliverables.",
};

export default function DeliverablesPage() {
  return (
    <AppShell title="Deliverables" breadcrumb="Workspace">
      <ArtifactsManagerView />
    </AppShell>
  );
}
