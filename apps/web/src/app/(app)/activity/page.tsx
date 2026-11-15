import type { Metadata } from "next";
import React from "react";

import { ActivityView } from "../../../components/activity/ActivityView";

export const metadata: Metadata = {
  title: "Activity | MAYA AI",
  description: "Tamper-evident record of actions.",
};

export default function ActivityPage() {
  return <ActivityView />;
}
