import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DagVisualizer } from "./DagVisualizer";
import type { SubtaskResponse } from "../../lib/tasks-client";

describe("DagVisualizer", () => {
  it("renders empty message when subtasks array is empty", () => {
    render(<DagVisualizer subtasks={[]} />);
    expect(
      screen.getByText("No subtasks defined in the DAG plan."),
    ).toBeInTheDocument();
  });

  it("renders nodes and selects node to inspect verification proof", () => {
    const onSelect = vi.fn();
    const mockSubtasks: SubtaskResponse[] = [
      {
        id: "st-1",
        task_id: "t-1",
        assigned_agent: "research",
        description: "Search pricing models",
        dependencies: [],
        status: "completed",
        input_data: { query: "pricing" },
        output_result: { found: 3 },
        verification_proof: { citations: 3 },
        retry_count: 0,
        created_at: "2026-11-07T10:00:00Z",
        updated_at: "2026-11-07T10:01:00Z",
      },
      {
        id: "st-2",
        task_id: "t-1",
        assigned_agent: "coding",
        description: "Generate pricing scraper",
        dependencies: ["st-1"],
        status: "in_progress",
        input_data: {},
        retry_count: 0,
        created_at: "2026-11-07T10:01:00Z",
        updated_at: "2026-11-07T10:02:00Z",
      },
    ];

    render(
      <DagVisualizer subtasks={mockSubtasks} onSelectSubtask={onSelect} />,
    );

    expect(
      screen.getAllByText("Search pricing models").length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Generate pricing scraper")).toBeInTheDocument();
    expect(screen.getByText("depends on: st-1")).toBeInTheDocument();

    // Node 1 is selected by default, inspect proof
    expect(screen.getByText("Verification Proof")).toBeInTheDocument();

    // Click node 2
    const node2Btn = screen.getByRole("button", {
      name: /Generate pricing scraper/i,
    });
    fireEvent.click(node2Btn);
    expect(onSelect).toHaveBeenCalledWith(mockSubtasks[1]);
  });
});
