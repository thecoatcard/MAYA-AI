import { describe, expect, it } from "vitest";

import type { ChatProgressStep, ChatUsage } from "./api-client";
import { buildProgressTree } from "./progress-steps";
import {
  buildTimeline,
  groupLabel,
  groupStatus,
  replyElapsedMs,
  stepFamily,
  workLogFooter,
  workLogTitle,
} from "./work-log";

function step(
  id: string,
  label: string,
  overrides: Partial<ChatProgressStep> = {},
): ChatProgressStep {
  return {
    runId: "r",
    stepId: id,
    parentId: null,
    kind: "search",
    label,
    detail: null,
    state: "done",
    index: null,
    total: null,
    elapsedMs: null,
    thought: null,
    ...overrides,
  };
}

const usage = (over: Partial<ChatUsage> = {}): ChatUsage => ({
  prompt_tokens: 1000,
  candidates_tokens: 300,
  total_tokens: 1300,
  cost_usd: null,
  is_estimate: false,
  ...over,
});

describe("stepFamily", () => {
  it("maps the server tool labels to a family", () => {
    expect(stepFamily(step("a", "Searching the web"))).toBe("web");
    expect(stepFamily(step("a", "Reading Notion"))).toBe("notion");
    expect(stepFamily(step("a", "Reading GitHub"))).toBe("github");
    expect(stepFamily(step("a", "Reading your mail"))).toBe("mail");
    expect(stepFamily(step("a", "Saving to memory", { kind: "save" }))).toBe(
      "memory",
    );
    expect(stepFamily(step("a", "Creating document", { kind: "render" }))).toBe(
      "document",
    );
    expect(stepFamily(step("a", "Using a tool", { kind: "tool" }))).toBe(
      "tool",
    );
  });

  it("never groups notes, thoughts, plans or unknown labels", () => {
    expect(stepFamily(step("a", "Note", { kind: "note" }))).toBeNull();
    expect(stepFamily(step("a", "Reasoning", { kind: "think" }))).toBeNull();
    expect(stepFamily(step("a", "Planning the reply", { kind: "plan" }))).toBe(
      null,
    );
    expect(stepFamily(step("a", "Rendering slide 3", { kind: "render" }))).toBe(
      null,
    );
  });
});

describe("buildTimeline", () => {
  const timeline = (steps: ChatProgressStep[]) =>
    buildTimeline(buildProgressTree(steps));

  it("groups consecutive steps of one family and keeps a lone step as it is", () => {
    const out = timeline([
      step("1", "Searching the web"),
      step("2", "Reading a web page"),
      step("3", "Searching the web"),
      step("4", "Reading Notion"),
    ]);
    expect(out.map((e) => e.type)).toEqual(["group", "node"]);
    const group = out[0];
    expect(group.type === "group" && group.nodes).toHaveLength(3);
  });

  it("keeps the order of notes and groups, and a note ends a run", () => {
    const out = timeline([
      step("n1", "Note", { kind: "note", note: "Let me look." }),
      step("1", "Searching the web"),
      step("2", "Searching the web"),
      step("n2", "Note", { kind: "note", note: "Now the page." }),
      step("3", "Searching the web"),
      step("4", "Searching the web"),
    ]);
    expect(out.map((e) => e.type)).toEqual(["note", "group", "note", "group"]);
  });

  it("keeps a failed step visible in its group status", () => {
    const out = timeline([
      step("1", "Searching the web"),
      step("2", "Searching the web", { state: "failed" }),
    ]);
    const group = out[0];
    expect(group.type).toBe("group");
    if (group.type === "group") {
      expect(groupStatus(group.nodes)).toEqual({ failed: 1, running: false });
    }
  });
});

describe("groupLabel", () => {
  const nodes = (labels: string[]) =>
    buildProgressTree(labels.map((l, i) => step(String(i), l)));
  it("words each family", () => {
    expect(
      groupLabel("web", nodes(["Searching the web", "Searching the web"])),
    ).toBe("Searched the web (2)");
    expect(
      groupLabel("notion", nodes(["Reading Notion", "Reading Notion"])),
    ).toBe("Read Notion (2)");
    expect(
      groupLabel("notion", nodes(["Reading Notion", "Using Notion"])),
    ).toBe("Used Notion (2)");
    expect(
      groupLabel(
        "tool",
        nodes(["Using a tool", "Using a tool", "Using a tool", "Using a tool"]),
      ),
    ).toBe("Ran 4 tools");
  });
});

describe("workLogTitle", () => {
  it("says how the reply ended and how long it took", () => {
    expect(workLogTitle([], "live", null)).toBe("Working");
    expect(workLogTitle([], "complete", 202000)).toBe(
      "Completed in 3 min 22 s",
    );
    expect(
      workLogTitle([step("a", "x", { state: "failed" })], "complete", null),
    ).toBe("Completed with errors");
    expect(workLogTitle([], "stopped", 5000)).toBe("Stopped in 5.0 s");
    expect(workLogTitle([], "interrupted", null)).toBe("Interrupted");
  });
});

describe("replyElapsedMs", () => {
  it("prefers the server's wall time, then the step times, else unknown", () => {
    const steps = [step("a", "x", { elapsedMs: 1500 })];
    expect(replyElapsedMs(steps, usage({ elapsed_ms: 9000 }))).toBe(9000);
    expect(replyElapsedMs(steps, usage())).toBe(1500);
    expect(replyElapsedMs([step("b", "x")], null)).toBeNull();
  });
});

describe("workLogFooter", () => {
  const steps = [
    step("a", "Searching the web"),
    step("n", "Note", { kind: "note", note: "hi" }),
  ];

  it("shows every figure that is real", () => {
    const out = workLogFooter(
      steps,
      usage({ turns: 3, elapsed_ms: 10000, cached_tokens: 250 }),
      10000,
    );
    expect(out).toEqual([
      "10 s",
      "1 step",
      "3 turns",
      "30 tok/s",
      "25% cached",
    ]);
  });

  it("hides what is not available and never estimates", () => {
    expect(workLogFooter(steps, null, null)).toEqual(["1 step"]);
    expect(workLogFooter(steps, usage(), null)).toEqual(["1 step"]);
    expect(
      workLogFooter(
        steps,
        usage({ turns: 1, elapsed_ms: 2000, cached_tokens: 0 }),
        2000,
      ),
    ).toEqual(["2.0 s", "1 step", "1 turn", "150 tok/s"]);
    // Provider usage that is only an estimate gives no rate and no cache share.
    expect(
      workLogFooter(
        steps,
        usage({
          is_estimate: true,
          elapsed_ms: 2000,
          cached_tokens: 500,
          turns: 2,
        }),
        2000,
      ),
    ).toEqual(["2.0 s", "1 step", "2 turns"]);
  });
});
