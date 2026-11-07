import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LiveActionCard } from "./LiveActionCard";

vi.mock("../../hooks/useTaskEvents", () => ({
  useTaskEvents: vi.fn().mockReturnValue({
    isConnected: true,
    isReconnecting: false,
    events: [],
    lastEvent: null,
    error: null,
    clearEvents: vi.fn(),
  }),
}));

describe("LiveActionCard", () => {
  it("renders live tool execution card with tool name and intent", () => {
    render(
      <LiveActionCard
        toolName="submit_task"
        taskId="task-12345"
        status="running"
        intent="Create a PDF on Java comprehensive notes"
      />,
    );

    expect(screen.getByTestId("live-action-card")).toBeInTheDocument();
    expect(screen.getByText("Maya Called Tool")).toBeInTheDocument();
    expect(
      screen.getByText("Autonomous Task Orchestrator"),
    ).toBeInTheDocument();
    expect(screen.getByText("ID: task-12345")).toBeInTheDocument();
    expect(
      screen.getByText(/Create a PDF on Java comprehensive notes/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Processing Live")).toBeInTheDocument();
  });

  it("handles clicks on View Deliverables and Task Center buttons", () => {
    const onViewDeliverables = vi.fn();
    const onOpenTaskDetails = vi.fn();

    render(
      <LiveActionCard
        toolName="submit_task"
        taskId="task-12345"
        status="running"
        onViewDeliverables={onViewDeliverables}
        onOpenTaskDetails={onOpenTaskDetails}
      />,
    );

    const deliverableBtn = screen.getByRole("button", {
      name: /View Deliverable Canvas/i,
    });
    fireEvent.click(deliverableBtn);
    expect(onViewDeliverables).toHaveBeenCalledTimes(1);

    const taskCenterBtn = screen.getByRole("button", {
      name: /Task Center Telemetry/i,
    });
    fireEvent.click(taskCenterBtn);
    expect(onOpenTaskDetails).toHaveBeenCalledWith("task-12345");
  });

  it("renders completed state accurately", () => {
    render(
      <LiveActionCard
        toolName="document_agent"
        status="completed"
        intent="Generate Java PDF"
      />,
    );

    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("100% Complete (3/3)")).toBeInTheDocument();
  });
});
