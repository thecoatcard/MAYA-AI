"use client";

import { Mic, MicOff } from "lucide-react";
import React from "react";

export interface VoiceMuteButtonProps {
  readonly muted: boolean;
  readonly onToggle: () => void;
  readonly className?: string;
}

/**
 * Compact icon-only microphone switch for the top-bar (master) voice. Muting turns the user's own
 * microphone off; the session stays open and MAYA keeps speaking progress updates. The state is
 * carried by the icon, the pressed state and the label, never by colour alone.
 */
export function VoiceMuteButton({
  muted,
  onToggle,
  className = "",
}: VoiceMuteButtonProps): React.JSX.Element {
  const label = muted ? "Unmute microphone" : "Mute microphone";
  const Icon = muted ? MicOff : Mic;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={label}
      aria-pressed={muted}
      title={
        muted
          ? "Microphone is muted. Click to unmute. MAYA still speaks updates."
          : "Microphone is on. Click to mute. MAYA still speaks updates."
      }
      data-testid="voice-mute-mini"
      data-muted={muted ? "true" : "false"}
      className={`maya-no-drag motion-interactive motion-press inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-maya-full border border-maya-border bg-maya-raised hover:bg-maya-hover active:bg-maya-pressed pointer-coarse:min-h-11 pointer-coarse:min-w-11 ${
        muted ? "text-maya-warning" : "text-maya-secondary"
      } ${className}`}
    >
      <Icon size={14} aria-hidden="true" />
    </button>
  );
}
