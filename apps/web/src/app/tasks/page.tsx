import type { Metadata } from "next";
import React from "react";

import { AppShell } from "../../components/layout/AppShell";
import { TaskCenterView } from "../../components/tasks/TaskCenterView";

export const metadata: Metadata = {
  title: "Task Center | MAYA AI",
  description:
    "Autonomous task execution engine, LangGraph DAG planner, and real-time event ledger.",
};

export default function TasksPage() {
  return (
    <AppShell title="Task Center" breadcrumb="Workspace">
      <TaskCenterView />
    </AppShell>
  );
}
