import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NewTaskModal } from "./NewTaskModal";
import * as tasksClient from "../../lib/tasks-client";

describe("NewTaskModal", () => {
  const mockCreatedTask: tasksClient.TaskSubmissionResponse = {
    task_id: "t-new-123",
    status: "planning",
    idempotency_key: "task-test-key",
    created_at: "2026-11-07T12:00:00Z",
    is_new: true,
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(tasksClient, "submitTask").mockResolvedValue(mockCreatedTask);
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockImplementation(() => Promise.resolve()),
      },
    });
  });

  it("does not render when isOpen is false", () => {
    const { container } = render(
      <NewTaskModal isOpen={false} onClose={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders presets and populates instruction upon selecting preset", () => {
    render(<NewTaskModal isOpen={true} onClose={vi.fn()} />);

    expect(screen.getByText("Assign Autonomous Task")).toBeInTheDocument();
    expect(screen.getByText("Research")).toBeInTheDocument();
    expect(screen.getByText("Coding")).toBeInTheDocument();
    expect(screen.getByText("Document")).toBeInTheDocument();
    expect(screen.getByText("Email")).toBeInTheDocument();
    expect(screen.getByText("PM")).toBeInTheDocument();

    // Click Research preset
    fireEvent.click(screen.getByText("Research"));

    expect(
      screen.getByDisplayValue("Competitive Market & Technical Research"),
    ).toBeInTheDocument();
    expect(
      screen.getByDisplayValue(
        /Investigate the latest architectural benchmarks/i,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("$1.50 USD")).toBeInTheDocument();
  });

  it("updates budget cap with slider and quick presets", () => {
    render(<NewTaskModal isOpen={true} onClose={vi.fn()} />);

    // Click $5 quick button
    fireEvent.click(screen.getByText("$5"));
    expect(screen.getByText("$5.00 USD")).toBeInTheDocument();

    // Adjust budget slider
    const slider = screen.getByLabelText(/Execution Budget Cap/i);
    fireEvent.change(slider, { target: { value: "10" } });
    expect(screen.getByText("$10.00 USD")).toBeInTheDocument();
  });

  it("regenerates and copies idempotency key", () => {
    render(<NewTaskModal isOpen={true} onClose={vi.fn()} />);

    const copyBtn = screen.getByTitle("Copy idempotency key");
    fireEvent.click(copyBtn);
    expect(navigator.clipboard.writeText).toHaveBeenCalled();

    const regenBtn = screen.getByTitle("Regenerate idempotency key");
    fireEvent.click(regenBtn);
  });

  it("submits task and invokes onTaskCreated callback", async () => {
    const onCreated = vi.fn();
    const onClose = vi.fn();

    render(
      <NewTaskModal
        isOpen={true}
        onClose={onClose}
        onTaskCreated={onCreated}
      />,
    );

    // Click coding preset
    fireEvent.click(screen.getByText("Coding"));

    // Submit form
    const submitBtn = screen.getByText("Execute Task");
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(tasksClient.submitTask).toHaveBeenCalled();
      expect(onCreated).toHaveBeenCalledWith(mockCreatedTask);
      expect(onClose).toHaveBeenCalled();
    });
  });
});
