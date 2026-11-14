import type { Metadata } from "next";
import React, { Suspense } from "react";

import { TasksRoute } from "./TasksRoute";

export const metadata: Metadata = {
  title: "Tasks | MAYA AI",
  description: "Running and finished work, with its plan and event record.",
};

export default function TasksPage() {
  return (
    <Suspense fallback={null}>
      <TasksRoute />
    </Suspense>
  );
}
