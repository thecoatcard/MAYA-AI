import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import {
  ConversationContext,
  type ConversationRuntimeValue,
} from "../chat/ConversationContext";
import { VoiceChip } from "./VoiceChip";

function setup(
  state: string,
  extra: { muted?: boolean; toggleMute?: () => void } = {},
) {
  const toggleMasterVoice = vi.fn();
  const toggleVoice = vi.fn();
  const toggleMute = extra.toggleMute ?? vi.fn();
  const value = {
    voice: { state, muted: extra.muted ?? false, toggleMute },
    toggleMasterVoice,
    toggleVoice,
  } as unknown as ConversationRuntimeValue;
  render(
    <ConversationContext.Provider value={value}>
      <VoiceChip />
    </ConversationContext.Provider>,
  );
  return { toggleMasterVoice, toggleVoice, toggleMute };
}

describe("VoiceChip (master voice)", () => {
  it("is the master toggle: a click starts voice through the master action", () => {
    const { toggleMasterVoice, toggleVoice } = setup("idle");
    expect(screen.getAllByText("Voice off").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Start voice" }));
    expect(toggleMasterVoice).toHaveBeenCalledTimes(1);
    expect(toggleVoice).not.toHaveBeenCalled();
  });

  it("has no mute button while voice is off", () => {
    setup("idle");
    expect(screen.queryByTestId("voice-mute-mini")).toBeNull();
  });

  it.each(["listening", "speaking", "thinking"])(
    "shows a labelled, unpressed mute switch while %s",
    (state) => {
      setup(state);
      const mute = screen.getByRole("button", { name: "Mute microphone" });
      expect(mute.getAttribute("aria-pressed")).toBe("false");
      expect(mute.getAttribute("title")).toMatch(/microphone is on/i);
    },
  );

  it("muting calls the existing toggle and shows Muted as text, aria and title", () => {
    const { toggleMute } = setup("listening", { muted: true });
    const mute = screen.getByRole("button", { name: "Unmute microphone" });
    expect(mute.getAttribute("aria-pressed")).toBe("true");
    expect(mute.getAttribute("data-muted")).toBe("true");
    expect(mute.getAttribute("title")).toMatch(/muted/i);
    expect(screen.getAllByText("Muted").length).toBeGreaterThan(0);
    fireEvent.click(mute);
    expect(toggleMute).toHaveBeenCalledTimes(1);
  });

  it("the mute switch does not stop voice", () => {
    const { toggleMasterVoice } = setup("listening");
    fireEvent.click(screen.getByRole("button", { name: "Mute microphone" }));
    expect(toggleMasterVoice).not.toHaveBeenCalled();
  });

  it("states connecting and error in plain text", () => {
    setup("connecting");
    expect(screen.getAllByText("Connecting").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("voice-mute-mini")).toBeNull();
  });
});

describe("VoiceChip stop (same toggle as the composer)", () => {
  it("stops the running session instead of replacing it", () => {
    const stopVoice = vi.fn();
    const toggleMasterVoice = vi.fn();
    const value = {
      voice: { state: "listening", muted: false, toggleMute: vi.fn() },
      toggleMasterVoice,
      toggleVoice: vi.fn(),
      stopVoice,
    } as unknown as ConversationRuntimeValue;
    render(
      <ConversationContext.Provider value={value}>
        <VoiceChip />
      </ConversationContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Stop voice" }));
    expect(stopVoice).toHaveBeenCalledTimes(1);
    expect(toggleMasterVoice).not.toHaveBeenCalled();
  });
});
