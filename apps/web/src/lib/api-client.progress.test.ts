import { describe, expect, it } from "vitest";

import { decodeChatEvent, readChatProgressStep } from "./api-client";
import type { SseFrame } from "./sse";

function progressFrame(data: unknown): SseFrame {
  return {
    event: "progress",
    data: JSON.stringify(data),
    id: null,
    retry: null,
  };
}

const valid = {
  run_id: "run-1",
  step_id: "s1",
  parent_id: null,
  kind: "think",
  label: "Reading the brief",
  detail: null,
  state: "running",
  index: 4,
  total: 10,
  elapsed_ms: 2300,
  thought: "Checking the dates first.",
};

describe("decodeChatEvent: progress", () => {
  it("decodes a well-formed progress frame into a typed step", () => {
    const event = decodeChatEvent(progressFrame(valid));
    expect(event).toEqual({
      type: "progress",
      step: {
        runId: "run-1",
        stepId: "s1",
        parentId: null,
        kind: "think",
        label: "Reading the brief",
        detail: null,
        state: "running",
        index: 4,
        total: 10,
        elapsedMs: 2300,
        thought: "Checking the dates first.",
      },
    });
  });

  it("ignores unknown extra fields", () => {
    const event = decodeChatEvent(
      progressFrame({ ...valid, future_field: { x: 1 } }),
    );
    expect(event?.type).toBe("progress");
  });

  it("tolerates missing optional fields", () => {
    const event = decodeChatEvent(
      progressFrame({
        run_id: "r",
        step_id: "s2",
        kind: "tool",
        label: "Search",
        state: "done",
      }),
    );
    expect(event).toMatchObject({
      type: "progress",
      step: {
        parentId: null,
        detail: null,
        index: null,
        total: null,
        elapsedMs: null,
        thought: null,
      },
    });
  });

  it("drops the thought unless the kind is think", () => {
    const event = decodeChatEvent(
      progressFrame({ ...valid, kind: "tool", thought: "not for tools" }),
    );
    expect(event).toMatchObject({ step: { thought: null } });
  });

  it.each([
    ["not JSON", "{oops"],
    ["an array", "[]"],
    ["a null body", "null"],
  ])("ignores a frame whose data is %s", (_name, data) => {
    expect(
      decodeChatEvent({ event: "progress", data, id: null, retry: null }),
    ).toBeNull();
  });

  it.each([
    ["missing step_id", { ...valid, step_id: undefined }],
    ["empty run_id", { ...valid, run_id: "" }],
    ["unknown kind", { ...valid, kind: "dance" }],
    ["unknown state", { ...valid, state: "paused" }],
    ["non-string state", { ...valid, state: 3 }],
  ])("ignores a frame with %s", (_name, data) => {
    expect(decodeChatEvent(progressFrame(data))).toBeNull();
  });

  it("drops non-finite numbers instead of showing them", () => {
    const step = readChatProgressStep({
      ...valid,
      index: "4",
      elapsed_ms: Number.NaN,
    });
    expect(step).toMatchObject({ index: null, elapsedMs: null });
  });
});

describe("note steps and usage figures", () => {
  it("reads a note step with its text and the live text_len", () => {
    const step = readChatProgressStep({
      run_id: "r",
      step_id: "n1",
      kind: "note",
      label: "Note",
      state: "done",
      note: "Let me look.",
      text_len: 14,
    });
    expect(step).toMatchObject({
      kind: "note",
      note: "Let me look.",
      textLen: 14,
    });
  });

  it("reads turns, elapsed time and cached tokens from the done frame, and omits what is absent", () => {
    const frame = (usage: unknown): SseFrame => ({
      event: "done",
      data: JSON.stringify({
        assistant_message_id: "a",
        finish_reason: "stop",
        usage,
      }),
      id: null,
      retry: null,
    });
    const base = {
      prompt_tokens: 10,
      candidates_tokens: 5,
      total_tokens: 15,
      cost_usd: null,
      is_estimate: false,
    };
    const full = decodeChatEvent(
      frame({ ...base, cached_tokens: 4, turns: 3, elapsed_ms: 1200 }),
    );
    expect(full).toMatchObject({
      usage: { cached_tokens: 4, turns: 3, elapsed_ms: 1200 },
    });
    const bare = decodeChatEvent(frame(base));
    expect(bare?.type === "done" && bare.usage).toEqual(base);
  });
});
