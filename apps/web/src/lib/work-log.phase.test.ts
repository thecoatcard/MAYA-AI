import { describe, expect, it } from "vitest";

import type { ChatProgressStep } from "./api-client";
import {
  currentPhase,
  finishedHeading,
  formatLiveSeconds,
  humanizeDetail,
  isHousekeeping,
  phaseTitle,
  statusLineText,
  WORKING_FALLBACK,
} from "./work-log";

function s(
  kind: ChatProgressStep["kind"],
  label: string,
  o: Partial<ChatProgressStep> = {},
): ChatProgressStep {
  return {
    runId: "r",
    stepId: `${kind}-${label}`,
    parentId: null,
    kind,
    label,
    detail: null,
    state: "running",
    index: null,
    total: null,
    elapsedMs: null,
    thought: null,
    ...o,
  };
}

describe("phaseTitle", () => {
  it.each([
    [s("plan", "Planning the reply"), "Analyzing the request"],
    [s("think", "Thinking"), "Thinking it through"],
    [s("tool", "Searching the web"), "Searching the web"],
    [s("tool", "Reading a web page"), "Reading a web page"],
    [s("tool", "Reading Notion"), "Reading your Notion page"],
    [s("tool", "Using Notion"), "Working in Notion"],
    [s("tool", "Creating document"), "Building the document"],
    [s("tool", "Reading the sheet"), "Working on the sheet"],
    [s("tool", "Saving to memory"), "Updating your memory"],
    [s("step", "Writing the answer"), "Writing the answer"],
    [s("check", "Verifying"), "Checking the result"],
  ])("maps %j to %s", (step, title) => {
    expect(phaseTitle(step)).toBe(title);
  });

  it("falls back safely for unknown steps", () => {
    expect(phaseTitle(s("step", "Pm agent"))).toBe(WORKING_FALLBACK);
    expect(phaseTitle(s("voice", ""))).toBe("Working on it");
  });
});

describe("currentPhase", () => {
  it("uses the newest running step, ignoring notes", () => {
    const steps = [
      s("tool", "Searching the web", { state: "done" }),
      s("tool", "Reading Notion"),
      s("note", "Note", { state: "done" }),
    ];
    expect(currentPhase(steps)).toEqual({
      title: "Reading your Notion page",
      waiting: false,
    });
  });

  it("falls back when nothing runs and flags an approval wait", () => {
    const done = [s("tool", "Searching the web", { state: "done" })];
    expect(currentPhase(done).title).toBe(WORKING_FALLBACK);
    expect(
      currentPhase([
        s("step", "Pm agent", {
          state: "skipped",
          detail: "Waiting for your approval.",
        }),
      ]),
    ).toEqual({ title: "Waiting for your approval", waiting: true });
  });
});

describe("status line and heading", () => {
  it("ticks seconds with a static ellipsis", () => {
    expect(statusLineText("Working on it", 0)).toBe("Working on it …");
    expect(statusLineText("Searching the web", 2)).toBe(
      "Searching the web for 2s …",
    );
    expect(formatLiveSeconds(65)).toBe("1m 5s");
  });

  it("folds a finished reply to one quiet line with only real figures", () => {
    const steps = [
      s("tool", "Searching the web", { state: "failed" }),
      s("note", "n", { state: "done" }),
    ];
    expect(finishedHeading(steps, "complete", 3000)).toBe(
      "Worked for 3.0 s · 1 step · 1 failed",
    );
    expect(finishedHeading(steps, "complete", 400)).toBe(
      "Worked · 1 step · 1 failed",
    );
    expect(finishedHeading(steps, "stopped", null)).toBe("Stopped");
  });
});

describe("housekeeping and details", () => {
  it("hides internal steps unless they failed", () => {
    const done = { state: "done" } as const;
    expect(isHousekeeping(s("plan", "Planning the reply", done))).toBe(true);
    expect(isHousekeeping(s("step", "Context injection", done))).toBe(true);
    expect(
      isHousekeeping(s("plan", "Planning the reply", { state: "failed" })),
    ).toBe(false);
    expect(isHousekeeping(s("tool", "Searching the web"))).toBe(false);
  });

  it("drops raw internal ids but keeps readable details", () => {
    expect(humanizeDetail("answer")).toBeNull();
    expect(humanizeDetail("runtime-context")).toBeNull();
    expect(humanizeDetail("create_document")).toBeNull();
    expect(humanizeDetail(null)).toBeNull();
    expect(humanizeDetail("weather in Delhi")).toBe("weather in Delhi");
    expect(humanizeDetail("example.com")).toBe("example.com");
  });
});
