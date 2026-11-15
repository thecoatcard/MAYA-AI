import { describe, expect, it } from "vitest";

import type { SubtaskResponse } from "../../lib/tasks-client";
import {
  describeTask,
  explainFailure,
  formatCost,
  isTerminalPhase,
  phaseOfStatus,
} from "./taskStatus";

function sub(status: string, extra: Partial<SubtaskResponse> = {}) {
  return {
    id: status + Math.random(),
    description: `step ${status}`,
    status,
    ...extra,
  } as SubtaskResponse;
}

describe("describeTask", () => {
  it("says Loading with no task data", () => {
    expect(describeTask(null).label).toBe("Loading");
  });

  it("a completed task with a failed step is Completed with errors, never plain Completed", () => {
    const viaSteps = describeTask({
      status: "completed",
      subtasks: [sub("completed"), sub("failed", { error_message: "boom" })],
    });
    expect(viaSteps).toMatchObject({
      phase: "completed",
      label: "Completed with errors",
      partial: true,
      detail: "Failed: step failed: boom",
    });
    const viaFlag = describeTask({
      status: "completed",
      subtasks: [sub("completed")],
      partial: true,
      failed_steps: [{ step: "Chapter 3", error: "timed out" }],
    });
    expect(viaFlag.detail).toBe("Failed: Chapter 3: timed out");
    expect(
      describeTask({ status: "completed", subtasks: [sub("completed")] }),
    ).toMatchObject({ label: "Completed", partial: false });
  });

  it("says Planning and nothing else when there are no subtasks", () => {
    const s = describeTask({ status: "pending", subtasks: [] });
    expect(s).toMatchObject({ label: "Planning", detail: null });
  });

  it("shows only real counts and the running step", () => {
    const s = describeTask({
      status: "in_progress",
      subtasks: [sub("completed"), sub("in_progress"), sub("pending")],
    });
    expect(s.label).toBe("In progress");
    expect(s.detail).toBe("1 of 3 steps done. Now: step in_progress");
  });

  it("is not Processing when waiting for approval", () => {
    const s = describeTask({
      status: "in_progress",
      subtasks: [sub("awaiting_approval")],
    });
    expect(s.label).toBe("Waiting for your approval");
    expect(s.phase).toBe("awaiting_approval");
  });

  it("reports the real failure text", () => {
    const s = describeTask({
      status: "failed",
      subtasks: [sub("failed", { error_message: "SMTP refused" })],
    });
    expect(s).toMatchObject({ label: "Failed", detail: "SMTP refused" });
    expect(s.active).toBe(false);
  });

  it("handles cancelled and completed as finished", () => {
    expect(describeTask({ status: "cancelled", subtasks: [] }).active).toBe(
      false,
    );
    expect(describeTask({ status: "completed", subtasks: [] }).label).toBe(
      "Completed",
    );
  });
});

describe("status, cost and failure helpers", () => {
  it("maps raw task and step statuses to one phase vocabulary", () => {
    expect(phaseOfStatus("executing")).toBe("running");
    expect(phaseOfStatus("in_progress")).toBe("running");
    expect(phaseOfStatus("paused")).toBe("awaiting_approval");
    expect(phaseOfStatus("FAILED")).toBe("failed");
    expect(phaseOfStatus("canceled")).toBe("cancelled");
    expect(phaseOfStatus("pending")).toBe("planning");
    expect(isTerminalPhase("failed")).toBe(true);
    expect(isTerminalPhase("running")).toBe(false);
  });

  it("never states an untracked cost as a fact", () => {
    expect(formatCost(0)).toBe("Cost not tracked");
    expect(formatCost(undefined)).toBe("Cost not tracked");
    expect(formatCost(Number.NaN)).toBe("Cost not tracked");
    expect(formatCost(1.256)).toBe("$1.26");
    expect(formatCost(0.0042)).toBe("$0.0042");
  });

  it("explains known failures in words and keeps the raw text", () => {
    const raw =
      "Tool 'web_search' denied: Tool 'web_search' missing required arguments: ['query']";
    expect(explainFailure(raw)).toEqual({
      summary: 'The "web_search" step was missing a required input (query).',
      technical: raw,
    });
    expect(explainFailure("request timed out").summary).toBe(
      "A step took too long and timed out.",
    );
    expect(explainFailure("HTTP 429 Too Many Requests").summary).toBe(
      "A service limit was reached.",
    );
    expect(explainFailure("kaboom")).toEqual({
      summary: "A step failed.",
      technical: "kaboom",
    });
    expect(explainFailure(undefined).technical).toBeNull();
  });
});
