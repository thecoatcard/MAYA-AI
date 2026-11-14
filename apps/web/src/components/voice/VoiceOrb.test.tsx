import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { LiveVoiceState } from "../../hooks/useLiveVoice";
import { ORB_LABELS, VoiceOrb } from "./VoiceOrb";

const STATES: LiveVoiceState[] = [
  "idle",
  "connecting",
  "listening",
  "thinking",
  "speaking",
  "interrupted",
  "unavailable",
  "error",
];

describe("VoiceOrb", () => {
  it.each(STATES)("shows a text label for %s", (state) => {
    render(<VoiceOrb state={state} />);
    expect(screen.getByText(ORB_LABELS[state])).toBeTruthy();
    expect(screen.getByTestId("voice-orb").dataset.state).toBe(state);
  });

  it("keeps the label for assistive technology when visually hidden", () => {
    render(<VoiceOrb state="listening" showLabel={false} />);
    expect(screen.getByText("Listening").className).toContain("sr-only");
  });

  it("does not touch the microphone", () => {
    const original = navigator.mediaDevices;
    render(<VoiceOrb state="listening" />);
    expect(navigator.mediaDevices).toBe(original);
  });
});

describe("VoiceOrb level meter", () => {
  it.each([
    ["idle", "idle"],
    ["connecting", "thinking"],
    ["listening", "listening"],
    ["thinking", "thinking"],
    ["speaking", "speaking"],
    ["interrupted", "listening"],
  ] as const)(
    "draws nine bars for %s with motion state %s",
    (state, visual) => {
      render(<VoiceOrb state={state} />);
      const meter = screen
        .getByTestId("voice-orb")
        .querySelector(".motion-orb") as HTMLElement;
      expect(meter.dataset.state).toBe(visual);
      expect(meter.querySelectorAll("i")).toHaveLength(9);
    },
  );

  it.each(["unavailable", "error"] as const)(
    "shows an icon instead of bars when voice is %s, with the state in words",
    (state) => {
      render(<VoiceOrb state={state} />);
      expect(
        screen.getByTestId("voice-orb").querySelector(".motion-orb"),
      ).toBeNull();
      expect(screen.getByText(ORB_LABELS[state])).toBeTruthy();
    },
  );
});
