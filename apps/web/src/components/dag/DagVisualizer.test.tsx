import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DagVisualizer } from "./DagVisualizer";
import type { SubtaskResponse } from "../../lib/tasks-client";

describe("DagVisualizer", () => {
  it("renders empty message when subtasks array is empty", () => {
    render(<DagVisualizer subtasks={[]} />);
    expect(screen.getByText("No steps planned yet")).toBeInTheDocument();
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
    expect(screen.getByText("depends on: 01")).toBeInTheDocument();

    // Node 1 is selected by default, inspect proof
    expect(screen.getByText("Verification Proof")).toBeInTheDocument();

    // Click node 2
    const node2Btn = screen.getByRole("button", {
      name: /Generate pricing scraper/i,
    });
    fireEvent.click(node2Btn);
    expect(onSelect).toHaveBeenCalledWith(mockSubtasks[1]);
  });

  it("keeps 30 steps readable: one focusable region, arrow keys move between nodes", () => {
    const many: SubtaskResponse[] = Array.from({ length: 30 }, (_, i) => ({
      id: `node-${i}`,
      task_id: "t-1",
      assigned_agent: "research",
      description: `Step description ${i} ${"very long ".repeat(20)}`,
      dependencies: i > 0 ? [`node-${i - 1}`] : [],
      status: i === 3 ? "failed" : "pending",
      input_data: {},
      error_message: i === 3 ? "kaboom" : undefined,
      retry_count: 0,
      created_at: "2026-11-07T10:00:00Z",
      updated_at: "2026-11-07T10:00:00Z",
    }));
    render(<DagVisualizer subtasks={many} />);

    expect(screen.getByText("30 steps")).toBeInTheDocument();
    const list = screen.getByRole("region", { name: "Plan steps list" });
    expect(list.getAttribute("tabindex")).toBe("0");
    const buttons = screen.getAllByRole("button", { name: /Step description/ });
    expect(buttons).toHaveLength(30);
    // Roving tabindex: only the selected node is in the tab order.
    expect(buttons.filter((b) => b.tabIndex === 0)).toHaveLength(1);

    buttons[0].focus();
    fireEvent.keyDown(buttons[0], { key: "ArrowDown" });
    expect(document.activeElement).toBe(buttons[1]);
    fireEvent.keyDown(buttons[1], { key: "End" });
    expect(document.activeElement).toBe(buttons[29]);
  });

  it("shows ids as secondary copyable text and the failure reason in plain words", () => {
    const failed: SubtaskResponse = {
      id: "ba6b35db-0000-4000-8000-000000000001",
      task_id: "t-1",
      assigned_agent: "research",
      description: "Perform grounded research",
      dependencies: [],
      status: "failed",
      input_data: {},
      error_message:
        "Tool 'web_search' denied: Tool 'web_search' missing required arguments: ['query']",
      retry_count: 1,
      created_at: "2026-11-07T10:00:00Z",
      updated_at: "2026-11-07T10:00:00Z",
    };
    render(<DagVisualizer subtasks={[failed]} />);

    expect(screen.queryByText("#ba6b35db")).toBeNull();
    expect(screen.getByLabelText("Copy step id")).toBeInTheDocument();
    expect(
      screen.getByText(
        'The "web_search" step was missing a required input (query).',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Retried 1 time.")).toBeInTheDocument();
    expect(screen.getByText("Technical details")).toBeInTheDocument();
  });

  it("shows the stored review under a step only when one exists", () => {
    const step = (id: string, output: unknown): SubtaskResponse => ({
      id,
      task_id: "t-1",
      assigned_agent: "writer",
      description: `Chapter ${id}`,
      dependencies: [],
      status: "completed",
      input_data: {},
      output_result: output,
      retry_count: 0,
      created_at: "2026-11-07T10:00:00Z",
      updated_at: "2026-11-07T10:01:00Z",
    });
    render(
      <DagVisualizer
        subtasks={[
          step("a", { voice_review: { verdict: "ok", note: "" } }),
          step("b", { summary: "no review" }),
        ]}
      />,
    );
    const lines = screen.getAllByTestId("step-review");
    expect(lines).toHaveLength(1);
    expect(lines[0]).toHaveTextContent("Reviewed: OK");
  });
});
