import type { Metadata } from "next";
import React from "react";

import { ActivityView } from "../../components/activity/ActivityView";
import { AppShell } from "../../components/layout/AppShell";

export const metadata: Metadata = {
  title: "Activity & Audit Ledger | MAYA AI",
  description:
    "Append-only, tamper-evident cryptographic audit ledger for user actions and agent tool calls.",
};

export default function ActivityPage() {
  return (
    <AppShell title="Activity & Audit Ledger" breadcrumb="Governance">
      <ActivityView />
    </AppShell>
  );
}
