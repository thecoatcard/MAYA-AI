"use client";

import React from "react";

import { useConversationRuntime } from "../chat/ConversationContext";
import { VoiceMuteButton } from "./VoiceMuteButton";
import { VoiceOrb } from "./VoiceOrb";

/**
 * Title-bar voice control: the MASTER voice. Orb state with a plain text label (Voice off,
 * Connecting, Listening, Speaking, Muted, Voice error); click starts or stops it. Next to it, a
 * mini microphone switch. The master session lives in the conversation runtime above the routes,
 * so it keeps running across conversations and pages and tracks every task.
 */
export function VoiceChip(): React.JSX.Element {
  const runtime = useConversationRuntime();
  const { voice } = runtime;
  const toggle = runtime.toggleMasterVoice ?? runtime.toggleVoice;
  // Stop lives on this chip too: while any session runs, a click ends it (master or conversation).
  const stop = runtime.stopVoice ?? toggle;
  const running =
    voice.state !== "idle" &&
    voice.state !== "unavailable" &&
    voice.state !== "error";
  const canMute =
    running && voice.state !== "connecting" && voice.toggleMute !== undefined;
  const muted = voice.muted ?? false;
  const action = running ? "Stop voice" : "Start voice";
  const detail = running
    ? "Voice tracks all conversations and tasks. Click to stop."
    : "Start voice: it follows all your conversations and tasks.";
  return (
    <span className="inline-flex max-w-full min-w-0 items-center gap-1">
      <button
        type="button"
        onClick={running ? stop : toggle}
        aria-label={action}
        title={detail}
        data-testid="voice-chip"
        data-muted={running && muted ? "true" : "false"}
        className="maya-no-drag motion-interactive motion-press inline-flex h-7 max-w-full min-w-0 items-center gap-2 rounded-maya-full border border-maya-border bg-maya-raised px-3 hover:bg-maya-hover active:bg-maya-pressed pointer-coarse:min-h-11"
      >
        <VoiceOrb
          state={voice.state}
          size="sm"
          label={
            running && muted && voice.state !== "connecting"
              ? "Muted"
              : undefined
          }
        />
      </button>
      {canMute && (
        <VoiceMuteButton muted={muted} onToggle={() => voice.toggleMute?.()} />
      )}
    </span>
  );
}
