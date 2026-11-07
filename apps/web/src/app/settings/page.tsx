import type { Metadata } from "next";
import React from "react";

import { AppShell } from "../../components/layout/AppShell";
import { SettingsView } from "../../components/settings/SettingsView";

export const metadata: Metadata = {
  title: "Settings | MAYA AI",
  description:
    "System settings, model routing preferences, task budgets, and privacy retention rules.",
};

export default function SettingsPage() {
  return (
    <AppShell title="System Settings" breadcrumb="Workspace">
      <SettingsView />
    </AppShell>
  );
}
