import { describe, expect, it } from "vitest";

import type { ChatProgressStep } from "./api-client";
import {
  applyProgressStep,
  buildProgressTree,
  countFailed,
  formatElapsed,
  isAdjusted,
  latestProgressLabel,
  runningStepLabel,
  settleProgress,
  storedProgress,
  summarizeProgress,
  taskProgressSteps,
  userFacingDetail,
} from "./progress-steps";

function step(
  id: string,
  overrides: Partial<ChatProgressStep> = {},
): ChatProgressStep {
  return {
    runId: "run-1",
    stepId: id,
    parentId: null,
    kind: "step",
    label: `Step ${id}`,
    detail: null,
    state: "running",
    index: null,
    total: null,
    elapsedMs: null,
    thought: null,
    ...overrides,
  };
}

describe("applyProgressStep", () => {
  it("appends new steps and updates a known step_id in place", () => {
    const first = applyProgressStep([], step("a"));
    const second = applyProgressStep(first, step("b"));
    const updated = applyProgressStep(
      second,
      step("a", { state: "done", label: "Renamed" }),
    );
    expect(updated.map((s) => [s.stepId, s.state, s.label])).toEqual([
      ["a", "done", "Renamed"],
      ["b", "running", "Step b"],
    ]);
  });
});

describe("settleProgress", () => {
  it("only changes steps that are still running", () => {
    const steps = [
      step("a", { state: "done" }),
      step("b"),
      step("c", { state: "failed" }),
    ];
    expect(settleProgress(steps, "skipped")?.map((s) => s.state)).toEqual([
      "done",
      "skipped",
      "failed",
    ]);
  });

  it("returns undefined when there are no steps", () => {
    expect(settleProgress(undefined, "done")).toBeUndefined();
  });
});

describe("buildProgressTree", () => {
  it("nests children by parent_id and keeps orphans at the top", () => {
    const tree = buildProgressTree([
      step("root"),
      step("child", { parentId: "root" }),
      step("grand", { parentId: "child" }),
      step("orphan", { parentId: "missing" }),
    ]);
    expect(tree.map((n) => n.step.stepId)).toEqual(["root", "orphan"]);
    expect(tree[0].children[0].step.stepId).toBe("child");
    expect(tree[0].children[0].children[0].step.stepId).toBe("grand");
  });
});

describe("formatElapsed and summaries", () => {
  it("formats times for people", () => {
    expect(formatElapsed(850)).toBe("850 ms");
    expect(formatElapsed(2300)).toBe("2.3 s");
    expect(formatElapsed(12000)).toBe("12 s");
    expect(formatElapsed(65000)).toBe("1 min 5 s");
  });

  it("summarises a finished reply in one line", () => {
    const steps = [
      step("a", { state: "done", elapsedMs: 7000 }),
      step("b", { state: "done", elapsedMs: 5000 }),
      step("c", { state: "done", parentId: "a", elapsedMs: 900 }),
    ];
    expect(summarizeProgress(steps, "complete")).toBe("Done in 12 s, 3 steps");
  });

  it("omits the time when no step reports one and counts failures", () => {
    const steps = [
      step("a", { state: "done" }),
      step("b", { state: "failed" }),
    ];
    expect(summarizeProgress(steps, "complete")).toBe(
      "Done, 2 steps, 1 failed",
    );
    expect(summarizeProgress(steps, "stopped")).toBe(
      "Stopped, 2 steps, 1 failed",
    );
  });
});

describe("latestProgressLabel", () => {
  it("prefers the newest running step, then the newest step", () => {
    expect(
      latestProgressLabel([
        step("a", { label: "Plan", state: "done" }),
        step("b", { label: "Search the web" }),
        step("c", { label: "Finished", state: "done" }),
      ]),
    ).toBe("Search the web");
    expect(
      latestProgressLabel([step("a", { label: "Only", state: "done" })]),
    ).toBe("Only");
    expect(latestProgressLabel(undefined)).toBeNull();
  });
});

describe("storedProgress", () => {
  it("reads metadata.progress and skips malformed entries", () => {
    const steps = storedProgress({
      id: "m1",
      role: "assistant",
      content: "x",
      created_at: "2026-11-11T10:00:00Z",
      metadata: {
        progress: [
          {
            run_id: "r",
            step_id: "s1",
            kind: "plan",
            label: "Plan",
            state: "done",
          },
          { step_id: "bad" },
        ],
      },
    });
    expect(steps.map((s) => [s.stepId, s.state])).toEqual([["s1", "done"]]);
  });
});

describe("task steps", () => {
  const wire = (stepId: string, state: string) => ({
    run_id: "t1",
    step_id: stepId,
    kind: "step",
    label: `Step ${stepId}`,
    state,
  });

  it("builds the list of a task from task_progress events only, in order", () => {
    const steps = taskProgressSteps([
      { event_type: "connected" },
      { event_type: "task_progress", payload: { step: wire("a", "running") } },
      { event_type: "subtask_status_changed", payload: { status: "x" } },
      { event_type: "task_progress", payload: { step: wire("b", "running") } },
      { event_type: "task_progress", payload: { step: wire("a", "done") } },
      { event_type: "task_progress", payload: { step: { step_id: "bad" } } },
      { event_type: "task_progress" },
    ]);
    expect(steps.map((s) => [s.stepId, s.state])).toEqual([
      ["a", "done"],
      ["b", "running"],
    ]);
    expect(taskProgressSteps(undefined)).toEqual([]);
  });

  it("reads the label of a running step from a voice task frame only", () => {
    expect(runningStepLabel({ step: wire("a", "running") })).toBe("Step a");
    expect(runningStepLabel({ step: wire("a", "done") })).toBeNull();
    expect(runningStepLabel({ step: "nonsense" })).toBeNull();
    expect(runningStepLabel({})).toBeNull();
    expect(runningStepLabel(null)).toBeNull();
  });
});

describe("adjusted steps and detail text", () => {
  it("treats skipped-with-detail as adjusted, but not an approval wait or a bare skip", () => {
    expect(
      isAdjusted(step("a", { state: "skipped", detail: "Shortened." })),
    ).toBe(true);
    expect(
      isAdjusted(
        step("a", { state: "skipped", detail: "Waiting for your approval." }),
      ),
    ).toBe(false);
    expect(isAdjusted(step("a", { state: "skipped" }))).toBe(false);
    expect(isAdjusted(step("a", { state: "failed", detail: "x" }))).toBe(false);
  });

  it("counts only real failures in the header", () => {
    const steps = [
      step("a", { state: "failed" }),
      step("b", { state: "skipped", detail: "Adjusted." }),
      step("c", { state: "failed" }),
      step("d", { state: "skipped", detail: "Adjusted again." }),
    ];
    expect(countFailed(steps)).toBe(2);
    expect(summarizeProgress(steps, "complete")).toBe(
      "Done, 4 steps, 2 failed",
    );
    expect(
      summarizeProgress(
        [step("a", { state: "skipped", detail: "Adjusted." })],
        "complete",
      ),
    ).toBe("Done, 1 step");
  });

  it("drops sentences that address the model and caps the length", () => {
    expect(
      userFacingDetail(
        "Too short.\nCall create_document again with more pages.",
      ),
    ).toBe("Too short.");
    expect(userFacingDetail("Please call the tool again.")).toBeNull();
    expect(userFacingDetail(null)).toBeNull();
    expect(userFacingDetail("x".repeat(2000))?.length).toBeLessThan(700);
  });
});
