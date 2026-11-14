import { fireEvent, render, screen } from "@testing-library/react";
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
import { MUTED_NOTICE } from "../voice/voice-copy";

const LIVE = ["listening", "thinking", "speaking", "interrupted"];

function runtime(
  over: {
    state?: string;
    muted?: boolean;
    busy?: boolean;
    scope?: "master" | "conversation" | null;
  } = {},
): ConversationRuntimeValue {
  const { state = "idle", muted = false, busy = false, scope = null } = over;
  return {
    chat: {
      status: busy ? "streaming" : "idle",
      isBusy: busy,
      error: null,
      retrying: null,
      canRetry: false,
      canAskAgain: false,
      stop: vi.fn(),
      sessionId: null,
    },
    voice: {
      state,
      muted,
      toggleMute: vi.fn(),
      transcripts: [],
      unavailableReason: null,
      error: null,
      interruptPlayback: vi.fn(),
    },
    voiceLive: LIVE.includes(state),
    voiceScope: scope,
    items: [],
    toggleVoice: vi.fn(),
    stopVoice: vi.fn(),
    sendText: vi.fn(async () => undefined),
    runAsTask: vi.fn(async () => undefined),
    trackTask: vi.fn(),
    refreshApprovals: vi.fn(async () => undefined),
    historyError: null,
    historyLoading: false,
    title: "New conversation",
  } as unknown as ConversationRuntimeValue;
}

function view(value: ConversationRuntimeValue) {
  return (
    <ConversationContext.Provider value={value}>
      <ConversationView />
    </ConversationContext.Provider>
  );
}

describe("composer voice toggle", () => {
  it("starts voice on click when idle, with no mute switch", () => {
    const value = runtime();
    render(view(value));
    const toggle = screen.getByTestId("mic-button");
    expect(toggle.getAttribute("aria-label")).toBe("Start voice");
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByTestId("voice-mute-mini")).toBeNull();
    fireEvent.click(toggle);
    expect(value.toggleVoice).toHaveBeenCalledTimes(1);
    expect(value.stopVoice).not.toHaveBeenCalled();
  });

  it("the same button stops voice on the next click", () => {
    const value = runtime({ state: "listening", scope: "conversation" });
    render(view(value));
    const toggle = screen.getByRole("button", { name: "Stop voice" });
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    expect(toggle.getAttribute("data-state")).toBe("listening");
    fireEvent.click(toggle);
    expect(value.stopVoice).toHaveBeenCalledTimes(1);
    expect(value.toggleVoice).not.toHaveBeenCalled();
  });

  it("stops a master session too and says so in the title", () => {
    const value = runtime({ state: "speaking", scope: "master" });
    render(view(value));
    const toggle = screen.getByRole("button", { name: "Stop voice" });
    expect(toggle.getAttribute("title")).toContain(
      "Voice is on (all conversations)",
    );
    expect(toggle.getAttribute("title")).toContain("Speaking");
    fireEvent.click(toggle);
    expect(value.stopVoice).toHaveBeenCalledTimes(1);
  });

  it("writes every state as text in a live region, not colour alone", () => {
    const { rerender } = render(view(runtime({ state: "listening" })));
    expect(screen.getAllByText("Voice: Listening").length).toBe(1);
    rerender(view(runtime({ state: "connecting" })));
    expect(screen.getAllByText("Voice: Connecting").length).toBe(1);
    rerender(view(runtime({ state: "error" })));
    expect(screen.getAllByText("Voice: Voice error").length).toBe(1);
    expect(
      screen.getByRole("button", { name: "Start voice again" }),
    ).toBeTruthy();
  });

  it("shows the mute switch only while voice is on and never while connecting", () => {
    const { rerender } = render(view(runtime({ state: "listening" })));
    const mute = screen.getByRole("button", { name: "Mute microphone" });
    expect(mute.getAttribute("aria-pressed")).toBe("false");
    rerender(view(runtime({ state: "connecting" })));
    expect(screen.queryByTestId("voice-mute-mini")).toBeNull();
    rerender(view(runtime({ state: "idle" })));
    expect(screen.queryByTestId("voice-mute-mini")).toBeNull();
  });

  it("muting uses the existing toggle, keeps the session and shows a mic-off state", () => {
    const value = runtime({ state: "listening", muted: true });
    render(view(value));
    const toggle = screen.getByTestId("mic-button");
    expect(toggle.getAttribute("data-muted")).toBe("true");
    expect(toggle.getAttribute("title")).toContain("Muted");
    // Muting silences only the user's microphone: the waveform of MAYA's voice session stays on
    // the toggle, and the mic-off icon belongs to the separate mute switch.
    expect(toggle.querySelector(".maya-orb")).not.toBeNull();
    expect(toggle.querySelector("svg.lucide-mic-off")).toBeNull();
    expect(screen.getAllByText("Voice: Muted").length).toBe(1);
    expect(screen.getByTestId("voice-muted-notice").textContent).toBe(
      MUTED_NOTICE,
    );
    const mute = screen.getByRole("button", { name: "Unmute microphone" });
    expect(mute.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(mute);
    expect(value.voice.toggleMute).toHaveBeenCalledTimes(1);
    expect(value.stopVoice).not.toHaveBeenCalled();
  });
});

describe("composer deep research toggle", () => {
  const research = () =>
    screen.getByRole("button", { name: "Deep research" }) as HTMLButtonElement;

  it("is off by default with a label, aria-label and aria-pressed", () => {
    render(view(runtime()));
    expect(research().getAttribute("aria-pressed")).toBe("false");
    expect(research().getAttribute("title")).toBe("Deep research");
    expect(screen.queryByTestId("deep-research-chip")).toBeNull();
    expect(screen.queryByText(/Reads 20\+ sites/)).toBeNull();
  });

  it("shows the chip and the hint when on, and the chip can dismiss it", () => {
    render(view(runtime()));
    fireEvent.click(research());
    expect(research().getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("deep-research-chip")).toBeTruthy();
    expect(
      screen.getByText("Reads 20+ sites, takes a few minutes"),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Turn off deep research" }),
    );
    expect(screen.queryByTestId("deep-research-chip")).toBeNull();
  });

  it("sends mode deep_research once, then resets to off", () => {
    const value = runtime();
    render(view(value));
    fireEvent.click(research());
    const box = screen.getByRole("textbox", { name: "Message" });
    fireEvent.change(box, { target: { value: "compare vector databases" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(value.sendText).toHaveBeenCalledWith("compare vector databases", {
      mode: "deep_research",
    });
    expect(research().getAttribute("aria-pressed")).toBe("false");
    fireEvent.change(box, { target: { value: "next one" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(value.sendText).toHaveBeenLastCalledWith("next one");
  });

  it("is disabled while a reply is streaming", () => {
    render(view(runtime({ busy: true })));
    expect(research().disabled).toBe(true);
  });

  it("is disabled during a live voice session", () => {
    render(view(runtime({ state: "listening" })));
    expect(research().disabled).toBe(true);
  });
});
