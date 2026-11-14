"use client";

import { AlertCircle, MicOff } from "lucide-react";
import React from "react";

import type { LiveVoiceState } from "../../hooks/useLiveVoice";

/**
 * Props-driven voice orb: a nine-bar level meter (the `motion-orb` motion class from the design
 * system). It owns no microphone and no session: the state comes from the single useLiveVoice
 * instance in the conversation runtime. Every state is also written as text, and the two failure
 * states swap the bars for an icon, so colour and motion never carry state alone.
 */

export const ORB_LABELS: Readonly<Record<LiveVoiceState, string>> = {
  idle: "Voice off",
  connecting: "Connecting",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  interrupted: "Interrupted",
  unavailable: "Voice unavailable",
  error: "Voice error",
};

/** The five visual states of the meter; the runtime has a few more that map onto these. */
export type OrbVisualState =
  "idle" | "listening" | "thinking" | "speaking" | "unavailable";

const VISUAL_STATE: Readonly<Record<LiveVoiceState, OrbVisualState>> = {
  idle: "idle",
  connecting: "thinking",
  listening: "listening",
  thinking: "thinking",
  speaking: "speaking",
  interrupted: "listening",
  unavailable: "unavailable",
  error: "unavailable",
};

const ORB_TONES: Readonly<Record<LiveVoiceState, string>> = {
  idle: "text-maya-muted",
  connecting: "text-maya-muted",
  listening: "text-maya-accent",
  thinking: "text-maya-info",
  speaking: "text-maya-success",
  interrupted: "text-maya-warning",
  unavailable: "text-maya-muted",
  error: "text-maya-danger",
};

/** Resting height of each bar (0 to 1): a soft bell, tallest in the middle. */
const BAR_HEIGHTS = [0.3, 0.5, 0.7, 0.9, 1, 0.9, 0.7, 0.5, 0.3] as const;

export interface VoiceOrbProps {
  readonly state: LiveVoiceState;
  /** Show the text label next to the orb. Default true. */
  readonly showLabel?: boolean;
  readonly size?: "sm" | "md";
  readonly className?: string;
  /** Replaces the state's text label (for example "Muted"). */
  readonly label?: string;
}

export function VoiceOrb({
  state,
  showLabel = true,
  size = "md",
  className = "",
  label,
}: VoiceOrbProps): React.JSX.Element {
  const visual = VISUAL_STATE[state];
  const failing = state === "unavailable" || state === "error";
  const Icon = state === "error" ? AlertCircle : MicOff;
  return (
    <span
      className={`inline-flex min-w-0 items-center gap-2 ${ORB_TONES[state]} ${className}`}
      data-testid="voice-orb"
      data-state={state}
    >
      {failing ? (
        <span
          className="maya-orb maya-orb--icon"
          data-size={size}
          aria-hidden="true"
        >
          <Icon size={size === "sm" ? 14 : 18} />
        </span>
      ) : (
        <span
          className="maya-orb motion-orb"
          data-state={visual}
          data-size={size}
          aria-hidden="true"
        >
          {BAR_HEIGHTS.map((height, index) => (
            <i
              key={index}
              style={{ "--h": height, "--n": index } as React.CSSProperties}
            />
          ))}
        </span>
      )}
      {showLabel ? (
        <span className="min-w-0 truncate text-maya-small text-maya-text">
          {label ?? ORB_LABELS[state]}
        </span>
      ) : (
        <span className="maya-sr-only">{label ?? ORB_LABELS[state]}</span>
      )}
    </span>
  );
}
