import type { Metadata } from "next";
import React from "react";

import { SettingsView } from "../../../components/settings/SettingsView";

export const metadata: Metadata = {
  title: "Settings | MAYA AI",
  description: "Preferences, budgets and privacy.",
};

export default function SettingsPage() {
  return <SettingsView />;
}
