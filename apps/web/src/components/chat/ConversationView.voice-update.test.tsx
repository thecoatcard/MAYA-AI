import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import {
  ConversationContext,
  type ConversationRuntimeValue,
} from "./ConversationContext";

vi.mock("../../hooks/useTaskEvents", () => ({
  useTaskEvents: () => ({ taskDetail: null, error: null }),
}));

import { ConversationView } from "./ConversationView";

function value(
  voiceState: string,
  lastSpokenUpdate: unknown,
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
      state: voiceState,
      transcripts: [],
      unavailableReason: null,
      error: null,
      interruptPlayback: vi.fn(),
      lastTaskEvent: null,
      lastSpokenUpdate,
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

const update = {
  taskId: "t1",
  milestone: "completed",
  text: 'The task "Solar deck" is done.',
  catchUp: false,
  timestamp: 1,
};

describe("spoken task update status line", () => {
  it("shows what MAYA said on her own, with an empty chat", () => {
    render(
      <ConversationContext.Provider value={value("listening", update)}>
        <ConversationView />
      </ConversationContext.Provider>,
    );
    expect(screen.getByTestId("voice-update-line")).toHaveTextContent(
      'MAYA: The task "Solar deck" is done.',
    );
  });

  it("shows the catch-up without a speaker prefix", () => {
    render(
      <ConversationContext.Provider
        value={value("listening", {
          ...update,
          taskId: null,
          catchUp: true,
          text: "While voice was off: one task finished.",
        })}
      >
        <ConversationView />
      </ConversationContext.Provider>,
    );
    expect(screen.getByTestId("voice-update-line")).toHaveTextContent(
      "While voice was off: one task finished.",
    );
  });

  it("is hidden when voice is closed or nothing was spoken", () => {
    const { rerender } = render(
      <ConversationContext.Provider value={value("idle", update)}>
        <ConversationView />
      </ConversationContext.Provider>,
    );
    expect(screen.queryByTestId("voice-update-line")).toBeNull();
    rerender(
      <ConversationContext.Provider value={value("listening", null)}>
        <ConversationView />
      </ConversationContext.Provider>,
    );
    expect(screen.queryByTestId("voice-update-line")).toBeNull();
  });
});
