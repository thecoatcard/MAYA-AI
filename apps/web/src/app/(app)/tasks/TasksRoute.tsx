"use client";

import { useSearchParams } from "next/navigation";
import React from "react";

import { useOptionalWorkspace } from "../../../components/layout/WorkspaceProvider";
import { TaskCenterView } from "../../../components/tasks/TaskCenterView";

/** Supports ?task_id= deep links. Client-only so the page stays static. */
export function TasksRoute(): React.JSX.Element {
  const taskId = useSearchParams()?.get("task_id") ?? null;
  const workspace = useOptionalWorkspace();
  return (
    <TaskCenterView
      key={taskId ?? "none"}
      initialTaskId={taskId}
      onApprovalDecided={() => void workspace?.refreshPendingApprovals()}
    />
  );
}
