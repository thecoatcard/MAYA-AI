import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import {
  ConversationContext,
  type ConversationRuntimeValue,
} from "./ConversationContext";
import type { TaskUpdateBlock } from "../../lib/voice-task-updates";

vi.mock("../../hooks/useTaskEvents", () => ({
  useTaskEvents: () => ({ taskDetail: null, error: null }),
}));

import { ConversationView } from "./ConversationView";

function value(
  taskUpdates: readonly TaskUpdateBlock[],
): ConversationRuntimeValue {
  return {
    chat: {
      status: "idle",
      isBusy: false,
      error: null,
      retrying: null,
      canRetry: false,
      canAskAgain: false,
      stop: vi.fn(),
      sessionId: null,
      messages: [],
    },
    voice: {
      state: "listening",
      transcripts: [],
      unavailableReason: null,
      error: null,
      interruptPlayback: vi.fn(),
      lastTaskEvent: null,
      lastSpokenUpdate: null,
      taskUpdates,
    },
    voiceLive: true,
    items: [],
    toggleVoice: vi.fn(),
    sendText: vi.fn(async () => undefined),
    runAsTask: vi.fn(async () => undefined),
    trackTask: vi.fn(),
    refreshApprovals: vi.fn(async () => undefined),
    historyError: null,
    historyLoading: false,
    title: "New conversation",
  } as unknown as ConversationRuntimeValue;
}

const line = (id: string, text: string, extra = {}) => ({
  id,
  text,
  milestone: "step_done",
  timestamp: 1,
  ...extra,
});

describe("task updates block in the conversation", () => {
  it("shows one block per task with the newest line, not one message per update", () => {
    const blocks: TaskUpdateBlock[] = [
      {
        taskId: "t1",
        lines: [
          line("u1", "Step 1 of 5 done: Intro"),
          line("u2", "Step 2 of 5 done: Lambdas", {
            said: "Lambdas ho gaya.",
            review: { verdict: "problem", note: "Only two pages" },
          }),
        ],
      },
    ];
    render(
      <ConversationContext.Provider value={value(blocks)}>
        <ConversationView />
      </ConversationContext.Provider>,
    );
    expect(screen.getAllByTestId("task-updates-block")).toHaveLength(1);
    expect(screen.getByTestId("task-updates-newest")).toHaveTextContent(
      "Step 2 of 5 done: Lambdas. Lambdas ho gaya. (1 earlier)",
    );
    fireEvent.click(screen.getByTestId("task-updates-newest"));
    expect(screen.getByTestId("task-update-review")).toHaveTextContent(
      "Review note: Only two pages",
    );
    expect(screen.queryByTestId("voice-update-line")).toBeNull();
  });

  it("updates in place when the same block changes", () => {
    const first: TaskUpdateBlock[] = [
      { taskId: "t1", lines: [line("u1", "Step 1 of 5 done: Intro")] },
    ];
    const { rerender } = render(
      <ConversationContext.Provider value={value(first)}>
        <ConversationView />
      </ConversationContext.Provider>,
    );
    rerender(
      <ConversationContext.Provider
        value={value([
          {
            taskId: "t1",
            lines: [
              line("u1", "Step 1 of 5 done: Intro"),
              line("u2", "Step 2 of 5 done: Lambdas"),
            ],
          },
        ])}
      >
        <ConversationView />
      </ConversationContext.Provider>,
    );
    expect(screen.getAllByTestId("task-updates-block")).toHaveLength(1);
    expect(screen.getByTestId("task-updates-newest")).toHaveTextContent(
      "Step 2 of 5 done: Lambdas (1 earlier)",
    );
  });
});
