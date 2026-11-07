import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VoiceOrb } from "./VoiceOrb";

describe("VoiceOrb", () => {
  it("renders in standby idle state by default", () => {
    render(<VoiceOrb />);
    expect(screen.getByText("Voice Standby")).toBeInTheDocument();
    expect(
      screen.getByLabelText(
        "Voice Assistant: Voice Standby. Click to start voice interaction.",
      ),
    ).toBeInTheDocument();
  });

  it("renders controlled listening state and triggers barge-in when speaking", () => {
    const onBargeIn = vi.fn();
    const { rerender } = render(
      <VoiceOrb state="speaking" onBargeIn={onBargeIn} />,
    );

    expect(screen.getByText("Speaking")).toBeInTheDocument();
    const button = screen.getByRole("button", {
      name: /Voice Assistant: Speaking/i,
    });
    fireEvent.click(button);
    expect(onBargeIn).toHaveBeenCalledTimes(1);

    rerender(<VoiceOrb state="listening" />);
    expect(screen.getByText("Listening")).toBeInTheDocument();
  });

  it("handles mute toggle button", () => {
    const onToggleMute = vi.fn();
    const { rerender } = render(
      <VoiceOrb isMuted={false} onToggleMute={onToggleMute} />,
    );

    const muteBtn = screen.getByRole("button", { name: "Audio On" });
    fireEvent.click(muteBtn);
    expect(onToggleMute).toHaveBeenCalledTimes(1);

    rerender(<VoiceOrb isMuted={true} onToggleMute={onToggleMute} />);
    expect(screen.getByRole("button", { name: "Muted" })).toBeInTheDocument();
  });
});
