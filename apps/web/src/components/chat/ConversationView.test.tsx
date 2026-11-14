import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import {
  ConversationContext,
  type ConversationRuntimeValue,
} from "./ConversationContext";
import type { ThreadItem } from "./threadItems";

vi.mock("../../hooks/useTaskEvents", () => ({
  useTaskEvents: () => ({ taskDetail: null, error: null }),
}));

import { ConversationView } from "./ConversationView";

interface Over {
  state?: string;
  items?: ThreadItem[];
  unavailableReason?: string | null;
}

function runtime(over: Over = {}): ConversationRuntimeValue {
  const { state = "idle", items = [], unavailableReason = null } = over;
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
    },
    voice: {
      state,
      transcripts: [],
      unavailableReason,
      error: null,
      interruptPlayback: vi.fn(),
    },
    voiceLive: ["listening", "thinking", "speaking", "interrupted"].includes(
      state,
    ),
    items,
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

function mount(value: ConversationRuntimeValue) {
  return render(
    <ConversationContext.Provider value={value}>
      <ConversationView />
    </ConversationContext.Provider>,
  );
}

describe("ConversationView composer", () => {
  it("has one control: textarea plus a mic button, with no dock while voice is off", () => {
    mount(runtime());
    expect(screen.getByRole("textbox", { name: "Message" })).toBeTruthy();
    expect(screen.getByTestId("mic-button")).toBeTruthy();
    // The voice dock card is gone (owner decision): the composer toggle is the only control.
    expect(screen.queryByTestId("voice-dock")).toBeNull();
    expect(screen.queryByTestId("voice-mute-mini")).toBeNull();
  });

  it("starts voice from the mic button", () => {
    const value = runtime();
    mount(value);
    fireEvent.click(screen.getByRole("button", { name: "Start voice" }));
    expect(value.toggleVoice).toHaveBeenCalled();
  });

  it("has no dock card while live and still sends typed text", () => {
    const value = runtime({ state: "listening" });
    mount(value);
    // Deliberate change: the dock card was removed.
    expect(screen.queryByTestId("voice-dock")).toBeNull();
    expect(screen.queryByText(/only you can approve it here/)).toBeNull();
    expect(screen.getByTestId("mode-label").textContent).toContain(
      "voice live",
    );
    const box = screen.getByRole("textbox", { name: "Message" });
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(value.sendText).toHaveBeenCalledWith("hello");
  });

  it("interrupts speech with Escape while speaking", () => {
    const value = runtime({ state: "speaking" });
    mount(value);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(value.voice.interruptPlayback).toHaveBeenCalledTimes(1);
  });

  it("does not interrupt on Escape when MAYA is not speaking", () => {
    const value = runtime({ state: "listening" });
    mount(value);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(value.voice.interruptPlayback).not.toHaveBeenCalled();
  });

  it("states the unavailable reason plainly and keeps the textbox enabled", () => {
    mount(runtime({ state: "unavailable", unavailableReason: "no_api_key" }));
    expect(screen.getByRole("alert").textContent).toContain("no_api_key");
    expect(
      (screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement)
        .disabled,
    ).toBe(false);
  });

  it("sends typed work to the task engine with Run as task", () => {
    const value = runtime();
    mount(value);
    const box = screen.getByRole("textbox", { name: "Message" });
    const button = screen.getByRole("button", { name: /run as task/i });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(box, { target: { value: "build report" } });
    fireEvent.click(button);
    expect(value.runAsTask).toHaveBeenCalledWith("build report");
  });
});

describe("ConversationView thread", () => {
  it("renders voice turns with the spoken marker and interim text", () => {
    const turn: ThreadItem[] = [
      {
        kind: "voiceTurn",
        id: "voice:t1",
        ts: 1,
        turnId: "t1",
        role: "user",
        text: "open the",
        isFinal: false,
        source: "spoken",
      },
    ];
    mount(runtime({ state: "listening", items: turn }));
    expect(screen.getByTestId("voice-turn-text").textContent).toBe("open the");
    expect(screen.getByText("Spoken")).toBeTruthy();
  });

  it("labels a turn Spoken only when it really was spoken, as a quiet inline chip", () => {
    const message = (
      id: string,
      role: "user" | "assistant",
      modality: "text" | "voice",
    ): ThreadItem => ({
      kind: "message",
      id,
      ts: Number(id.slice(1)),
      spoken: modality === "voice",
      message: {
        key: id,
        id,
        role,
        content: "hello",
        state: "complete",
        modality,
        createdAt: 1,
      } as never,
    });
    mount(
      runtime({
        items: [
          message("m1", "user", "text"),
          message("m2", "assistant", "text"),
          message("m3", "assistant", "voice"),
        ],
      }),
    );
    const chips = screen.getAllByTestId("spoken-marker");
    // Only the one voice reply is marked; the typed turns are not.
    expect(chips).toHaveLength(1);
    expect(chips[0]?.className).toContain("maya-chip");
    // The chip lives in the header row (a flex row), never as a stretched grid child of the message.
    expect(chips[0]?.parentElement?.className).toContain("maya-msg__head");
  });

  it("gives message bodies dir=auto so right-to-left text lays out correctly", () => {
    const turn: ThreadItem[] = [
      {
        kind: "voiceTurn",
        id: "voice:t2",
        ts: 1,
        turnId: "t2",
        role: "assistant",
        text: "hello",
        isFinal: true,
        source: "typed",
      },
    ];
    mount(runtime({ items: turn }));
    expect(screen.getByTestId("voice-turn-text")).toHaveAttribute(
      "dir",
      "auto",
    );
    // A typed turn in a live session is not marked as spoken.
    expect(screen.queryByTestId("spoken-marker")).toBeNull();
  });
});
