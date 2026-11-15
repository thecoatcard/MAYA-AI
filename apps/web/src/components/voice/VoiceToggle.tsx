"use client";

import { AlertCircle, Mic, MicOff } from "lucide-react";
import React from "react";

import type { LiveVoiceState } from "../../hooks/useLiveVoice";
import { useConversationRuntime } from "../chat/ConversationContext";
import { useContextMenu } from "../ui/ContextMenu";
import { ORB_LABELS, VoiceOrb } from "./VoiceOrb";
import { VoiceMuteButton } from "./VoiceMuteButton";

/** The state the toggle shows, in words (also the title and the screen-reader text). */
export function voiceToggleStatus(
  state: LiveVoiceState,
  muted: boolean,
): string {
  if (muted && state !== "connecting" && isRunning(state)) return "Muted";
  return ORB_LABELS[state];
}

function isRunning(state: LiveVoiceState): boolean {
  return state !== "idle" && state !== "unavailable" && state !== "error";
}

/**
 * The composer's voice control: ONE toggle for the one voice session. Click starts voice, click
 * again stops it. The waveform animates while listening or speaking (existing orb) and stays
 * visible while the microphone is muted, because muting silences only the user's mic: MAYA's voice
 * session and her spoken updates continue. The muted state is shown by the mute switch next to it
 * (mic-off icon) and as text, and an alert icon shows on error; the state is also text (title,
 * screen-reader label, live region), never colour or motion alone. Master voice (title bar) and conversation voice are the same session, so both show the
 * same state; this control stops either.
 */
export function VoiceToggle(): React.JSX.Element {
  const runtime = useConversationRuntime();
  const { voice } = runtime;
  const state = voice.state;
  const running = isRunning(state);
  const muted = running && state !== "connecting" && (voice.muted ?? false);
  const canMute =
    running && state !== "connecting" && voice.toggleMute !== undefined;
  const master = runtime.voiceScope === "master";
  const status = voiceToggleStatus(state, muted);

  const onClick = (): void => {
    if (running) (runtime.stopVoice ?? runtime.toggleVoice)();
    else runtime.toggleVoice();
  };
  const label = running
    ? "Stop voice"
    : state === "error" || state === "unavailable"
      ? "Start voice again"
      : "Start voice";
  const title = running
    ? `${master ? "Voice is on (all conversations)" : "Voice is on"}: ${status}. Click to stop.`
    : state === "error" || state === "unavailable"
      ? `${status}. Click to try again.`
      : "Start voice";

  const menu = useContextMenu({
    label: "Voice actions",
    getItems: () => {
      const items: import("../ui/ContextMenu").ContextMenuEntry[] = [
        {
          id: "voice",
          label: running ? "Stop voice" : "Start voice",
          icon: running ? <MicOff size={14} /> : <Mic size={14} />,
          onSelect: onClick,
        },
      ];
      if (running) {
        items.push({
          id: "mute",
          label: "Mute microphone",
          checked: voice.muted ?? false,
          disabled: !canMute,
          disabledReason: "Voice is still connecting.",
          onSelect: () => voice.toggleMute?.(),
        });
      }
      return items;
    },
  });

  return (
    <span
      className="maya-composer__voice"
      data-testid="composer-voice"
      {...menu.triggerProps}
    >
      {menu.menu}
      <button
        type="button"
        onClick={onClick}
        aria-label={label}
        aria-pressed={running}
        title={title}
        data-testid="mic-button"
        data-state={state}
        data-active={running ? "true" : "false"}
        data-muted={muted ? "true" : "false"}
        className="maya-composer__mic maya-composer__voice-toggle motion-interactive"
      >
        {state === "error" ? (
          <span className="maya-orb maya-orb--icon" data-size="sm">
            <AlertCircle size={16} aria-hidden="true" />
          </span>
        ) : (
          <VoiceOrb state={state} size="sm" showLabel={false} />
        )}
      </button>
      {canMute && (
        <VoiceMuteButton
          muted={voice.muted ?? false}
          onToggle={() => voice.toggleMute?.()}
          className="maya-composer__mute"
        />
      )}
      <span role="status" aria-live="polite" className="maya-sr-only">
        {state === "idle" ? "" : `Voice: ${status}`}
      </span>
    </span>
  );
}
