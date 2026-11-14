import { describe, expect, it } from "vitest";

import { isVoiceServerFrame } from "./voice-protocol";

describe("MEMORY_SAVED frame", () => {
  it("accepts a well-formed frame, with or without updated", () => {
    expect(
      isVoiceServerFrame({ type: "MEMORY_SAVED", memory_id: "m1", text: "x" }),
    ).toBe(true);
    expect(
      isVoiceServerFrame({
        type: "MEMORY_SAVED",
        memory_id: "m1",
        text: "x",
        updated: true,
      }),
    ).toBe(true);
  });

  it("rejects a frame without an id or text", () => {
    expect(isVoiceServerFrame({ type: "MEMORY_SAVED", text: "x" })).toBe(false);
    expect(isVoiceServerFrame({ type: "MEMORY_SAVED", memory_id: "" })).toBe(
      false,
    );
  });
});

describe("TASK_UPDATE_SPOKEN frame", () => {
  it("accepts a spoken task update and rejects one without text", () => {
    expect(
      isVoiceServerFrame({
        type: "TASK_UPDATE_SPOKEN",
        task_id: null,
        milestone: "completed",
        text: "done",
        catch_up: true,
      }),
    ).toBe(true);
    expect(
      isVoiceServerFrame({ type: "TASK_UPDATE_SPOKEN", milestone: "failed" }),
    ).toBe(false);
  });
});

describe("STEP_REVIEW frame", () => {
  it("accepts a verdict and rejects an unknown one", () => {
    const ok = {
      type: "STEP_REVIEW",
      task_id: "t1",
      subtask_id: "s1",
      verdict: "ok",
      note: "",
    };
    expect(isVoiceServerFrame(ok)).toBe(true);
    expect(isVoiceServerFrame({ ...ok, verdict: "great" })).toBe(false);
  });
});

describe("SCHEDULE_DRAFT frame", () => {
  it("reads the draft id from card.id or card.draft_id", async () => {
    const { scheduleDraftCardId } = await import("./voice-protocol");
    expect(
      scheduleDraftCardId({
        type: "SCHEDULE_DRAFT",
        message_id: "m",
        card: { id: "d1" },
      }),
    ).toBe("d1");
    expect(
      scheduleDraftCardId({
        type: "SCHEDULE_DRAFT",
        message_id: "m",
        card: { draft_id: "d2" },
      }),
    ).toBe("d2");
    expect(
      scheduleDraftCardId({
        type: "SCHEDULE_DRAFT",
        message_id: "m",
        card: {},
      }),
    ).toBeNull();
  });
});
