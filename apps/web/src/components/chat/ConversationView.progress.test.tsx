import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../../hooks/useChatStream";
import type { ChatProgressStep } from "../../lib/api-client";
import {
  ConversationContext,
  type ConversationRuntimeValue,
} from "./ConversationContext";

vi.mock("../../hooks/useTaskEvents", () => ({
  useTaskEvents: () => ({ taskDetail: null, error: null }),
}));

import { ConversationView } from "./ConversationView";

function step(
  stepId: string,
  label: string,
  state: ChatProgressStep["state"],
): ChatProgressStep {
  return {
    runId: "r",
    stepId,
    parentId: null,
    kind: "step",
    label,
    detail: null,
    state,
    index: null,
    total: null,
    elapsedMs: null,
    thought: null,
  };
}

function value(
  voiceState: string,
  messages: ChatMessage[],
  lastTaskEvent: unknown = null,
): ConversationRuntimeValue {
  return {
    chat: {
      status: "streaming",
      isBusy: true,
      error: null,
      retrying: null,
      canRetry: false,
      canAskAgain: false,
      stop: vi.fn(),
      sessionId: null,
      messages,
    },
    voice: {
      state: voiceState,
      transcripts: [],
      unavailableReason: null,
      error: null,
      interruptPlayback: vi.fn(),
      lastTaskEvent,
    },
    voiceLive: voiceState === "listening",
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

const reply: ChatMessage = {
  key: "a1",
  id: "a1",
  role: "assistant",
  content: "",
  state: "streaming",
  finishReason: null,
  usage: null,
  progress: [
    step("s1", "Planned the reply", "done"),
    step("s2", "Drawing the chart", "running"),
  ],
};

describe("voice status line", () => {
  it("shows the newest running step as one line above the composer", () => {
    render(
      <ConversationContext.Provider value={value("listening", [reply])}>
        <ConversationView />
      </ConversationContext.Provider>,
    );
    expect(screen.getByTestId("voice-progress-line")).toHaveTextContent(
      "Drawing the chart",
    );
    // Deliberate change: the dock card is gone; the status line remains.
    expect(screen.queryByTestId("voice-dock")).toBeNull();
  });

  it("is not shown when voice is closed", () => {
    render(
      <ConversationContext.Provider value={value("idle", [reply])}>
        <ConversationView />
      </ConversationContext.Provider>,
    );
    expect(screen.queryByTestId("voice-progress-line")).toBeNull();
  });

  const taskFrame = (state: string) => ({
    type: "TASK_EVENT",
    task_id: "t1",
    event_type: "task_progress",
    step: {
      run_id: "t1",
      step_id: "sub-1",
      kind: "step",
      label: "Research agent",
      state,
    },
  });

  it("shows the running step of a background task started by voice", () => {
    const { rerender } = render(
      <ConversationContext.Provider
        value={value("listening", [], taskFrame("running"))}
      >
        <ConversationView />
      </ConversationContext.Provider>,
    );
    expect(screen.getByTestId("voice-progress-line")).toHaveTextContent(
      "Research agent",
    );

    // The step ended (done, failed or the task was cancelled): the line goes away.
    rerender(
      <ConversationContext.Provider
        value={value("listening", [], taskFrame("failed"))}
      >
        <ConversationView />
      </ConversationContext.Provider>,
    );
    expect(screen.queryByTestId("voice-progress-line")).toBeNull();
  });

  it("prefers the reply that is streaming over a task step", () => {
    render(
      <ConversationContext.Provider
        value={value("listening", [reply], taskFrame("running"))}
      >
        <ConversationView />
      </ConversationContext.Provider>,
    );
    expect(screen.getByTestId("voice-progress-line")).toHaveTextContent(
      "Drawing the chart",
    );
  });
});
