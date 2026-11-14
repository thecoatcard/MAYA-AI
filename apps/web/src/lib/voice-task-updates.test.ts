import { describe, expect, it } from "vitest";

import {
  applySpokenUpdate,
  applyStepReview,
  isNoiseUserTurn,
  lineLabel,
  reviewLabel,
  storedReviewLabel,
  tidyTranscripts,
  type TaskUpdateBlock,
} from "./voice-task-updates";

const base = { task_id: "t1", milestone: "step_done" } as const;

describe("task updates block", () => {
  it("keeps one block per task and updates a line in place", () => {
    let blocks: readonly TaskUpdateBlock[] = [];
    blocks = applySpokenUpdate(
      blocks,
      { ...base, update_id: "u1", text: "Step 1 of 5 done: Intro" },
      1,
    );
    blocks = applySpokenUpdate(
      blocks,
      { ...base, update_id: "u2", text: "Step 2 of 5 done: Lambdas" },
      2,
    );
    blocks = applySpokenUpdate(
      blocks,
      {
        ...base,
        update_id: "u2",
        text: "Step 2 of 5 done: Lambdas",
        said: "Lambdas ho gaya.",
      },
      3,
    );
    expect(blocks).toHaveLength(1);
    expect(blocks[0].lines).toHaveLength(2);
    expect(lineLabel(blocks[0].lines[1])).toBe(
      "Step 2 of 5 done: Lambdas. Lambdas ho gaya.",
    );
    expect(blocks[0].lines[1].timestamp).toBe(2);
  });

  it("separates tasks and the catch-up", () => {
    let blocks: readonly TaskUpdateBlock[] = [];
    blocks = applySpokenUpdate(
      blocks,
      { ...base, update_id: "u1", text: "a" },
      1,
    );
    blocks = applySpokenUpdate(
      blocks,
      {
        task_id: null,
        milestone: "completed",
        update_id: "u2",
        text: "While voice was off",
      },
      2,
    );
    expect(blocks.map((b) => b.taskId)).toEqual(["t1", null]);
  });

  it("shows a review only after a verdict frame, and never invents one", () => {
    let blocks: readonly TaskUpdateBlock[] = applySpokenUpdate(
      [],
      { ...base, update_id: "u1", text: "Step 1 of 5 done: Intro" },
      1,
    );
    expect(reviewLabel(blocks[0].lines[0])).toBeNull();
    blocks = applyStepReview(blocks, {
      task_id: "t1",
      update_id: "u1",
      verdict: "ok",
      note: "",
    });
    expect(reviewLabel(blocks[0].lines[0])).toBe("Reviewed: OK");
    blocks = applyStepReview(blocks, {
      task_id: "t1",
      update_id: "u1",
      verdict: "problem",
      note: "Only two pages",
    });
    expect(reviewLabel(blocks[0].lines[0])).toBe("Review note: Only two pages");
    expect(
      applyStepReview(blocks, {
        task_id: "t1",
        update_id: "zz",
        verdict: "ok",
      }),
    ).toEqual(blocks);
  });

  it("reads the stored review of a step for the task card", () => {
    expect(storedReviewLabel({ voice_review: { verdict: "ok" } })).toBe(
      "Reviewed: OK",
    );
    expect(
      storedReviewLabel({
        voice_review: { verdict: "problem", note: "Wrong language" },
      }),
    ).toBe("Review note: Wrong language");
    expect(storedReviewLabel({ summary: "x" })).toBeNull();
    expect(storedReviewLabel(null)).toBeNull();
  });
});

describe("transcript tidying", () => {
  const turn = (role: "user" | "assistant", text: string, isFinal = true) => ({
    role,
    text,
    isFinal,
  });

  it("drops stray fragments but keeps real short answers", () => {
    expect(isNoiseUserTurn("de", true)).toBe(true);
    expect(isNoiseUserTurn("d", true)).toBe(true);
    expect(isNoiseUserTurn("de", false)).toBe(false);
    for (const word of ["ok", "yes", "haan", "no", "OK.", "ha"]) {
      expect(isNoiseUserTurn(word, true)).toBe(false);
    }
    expect(isNoiseUserTurn("abc", true)).toBe(false);
    const out = tidyTranscripts([turn("user", "de"), turn("user", "haan")]);
    expect(out.map((t) => t.text)).toEqual(["haan"]);
  });

  it("collapses identical consecutive assistant lines into one with a count", () => {
    const line = "Ek aur step complete ho gaya hai.";
    const out = tidyTranscripts([
      turn("assistant", line),
      ...Array.from({ length: 5 }, () => turn("assistant", line)),
      turn("user", "ok"),
      turn("assistant", line),
    ]);
    expect(out.map((t) => t.text)).toEqual([`${line} (x6)`, "ok", line]);
  });

  it("does not merge different or unfinished assistant lines", () => {
    const out = tidyTranscripts([
      turn("assistant", "one"),
      turn("assistant", "two"),
      turn("assistant", "two", false),
    ]);
    expect(out).toHaveLength(3);
  });
});
