"use client";

import { AlertCircle, Mic, Radio, Volume2, VolumeX } from "lucide-react";
import React, { useCallback, useEffect, useRef, useState } from "react";

export type VoiceOrbState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "interrupted"
  | "disconnected"
  | "error";

export interface VoiceOrbProps {
  readonly state?: VoiceOrbState;
  readonly onStateChange?: (newState: VoiceOrbState) => void;
  readonly onTranscript?: (transcript: string) => void;
  readonly onBargeIn?: () => void;
  readonly isMuted?: boolean;
  readonly onToggleMute?: () => void;
  readonly className?: string;
  readonly compact?: boolean;
}

export function VoiceOrb({
  state: controlledState,
  onStateChange,
  onTranscript,
  onBargeIn,
  isMuted = false,
  onToggleMute,
  className = "",
  compact = false,
}: VoiceOrbProps): React.JSX.Element {
  const [internalState, setInternalState] = useState<VoiceOrbState>("idle");
  const state = controlledState ?? internalState;

  const [audioLevel, setAudioLevel] = useState<number>(0);
  const [transcriptTicker, setTranscriptTicker] = useState<string>("");

  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const animationFrameRef = useRef<number | null>(null);

  const updateState = useCallback(
    (newState: VoiceOrbState) => {
      setInternalState(newState);
      onStateChange?.(newState);
    },
    [onStateChange],
  );

  // Initialize Web Audio API Analyser for real microphone feedback
  const startListening = useCallback(async () => {
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        updateState("error");
        return;
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      micStreamRef.current = stream;

      const AudioCtx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext })
          .webkitAudioContext;
      const audioCtx = new AudioCtx();
      audioContextRef.current = audioCtx;

      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      analyserRef.current = analyser;

      const source = audioCtx.createMediaStreamSource(stream);
      source.connect(analyser);

      const dataArray = new Uint8Array(analyser.frequencyBinCount);

      const tick = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteFrequencyData(dataArray);

        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i];
        }
        const average = sum / dataArray.length;
        // Normalized 0 to 1 with smooth dampening
        setAudioLevel((prev) => prev * 0.7 + (average / 128) * 0.3);

        animationFrameRef.current = requestAnimationFrame(tick);
      };

      tick();
      updateState("listening");
      const initialTicker = "Listening for instructions…";
      setTranscriptTicker(initialTicker);
      onTranscript?.(initialTicker);
    } catch {
      updateState("error");
      const errTicker = "Microphone access denied or unavailable.";
      setTranscriptTicker(errTicker);
      onTranscript?.(errTicker);
    }
  }, [onTranscript, updateState]);

  const stopListening = useCallback(() => {
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach((track) => track.stop());
      micStreamRef.current = null;
    }
    if (audioContextRef.current && audioContextRef.current.state !== "closed") {
      audioContextRef.current.close().catch(() => {});
      audioContextRef.current = null;
    }
    setAudioLevel(0);
    updateState("idle");
    setTranscriptTicker("");
  }, [updateState]);

  // Barge-in handler: user speaks or clicks while MAYA is speaking
  const handleOrbClick = () => {
    if (state === "speaking") {
      updateState("interrupted");
      onBargeIn?.();
      return;
    }

    if (
      state === "idle" ||
      state === "disconnected" ||
      state === "interrupted" ||
      state === "error"
    ) {
      if (controlledState !== undefined) {
        onStateChange?.("listening");
      } else {
        startListening();
      }
    } else if (state === "listening") {
      if (controlledState !== undefined) {
        onStateChange?.("idle");
      } else {
        stopListening();
      }
    }
  };

  useEffect(() => {
    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
      if (micStreamRef.current) {
        micStreamRef.current.getTracks().forEach((track) => track.stop());
      }
      if (
        audioContextRef.current &&
        audioContextRef.current.state !== "closed"
      ) {
        audioContextRef.current.close().catch(() => {});
      }
    };
  }, []);

  // Compute visual ring scale based on audio amplitude (capped for elegance)
  const scale =
    state === "listening"
      ? 1 + Math.min(audioLevel * 0.4, 0.35)
      : state === "speaking"
        ? 1.08 + Math.sin(Date.now() / 200) * 0.05
        : state === "interrupted"
          ? 0.92
          : 1;

  // Determine state colors adhering to Quiet Instrument tokens
  const getStateColors = () => {
    switch (state) {
      case "listening":
        return {
          glow: "rgba(20, 184, 166, 0.25)",
          border: "border-teal-500/60",
          inner: "from-teal-950/60 to-zinc-900",
          icon: "text-teal-400",
          label: "Listening",
        };
      case "thinking":
        return {
          glow: "rgba(245, 158, 11, 0.25)",
          border: "border-amber-500/60",
          inner: "from-amber-950/50 to-zinc-900",
          icon: "text-amber-400",
          label: "Thinking",
        };
      case "speaking":
        return {
          glow: "rgba(59, 130, 246, 0.3)",
          border: "border-blue-500/70",
          inner: "from-blue-950/60 to-zinc-900",
          icon: "text-blue-400",
          label: "Speaking",
        };
      case "interrupted":
        return {
          glow: "rgba(239, 68, 68, 0.2)",
          border: "border-red-500/50",
          inner: "from-red-950/40 to-zinc-900",
          icon: "text-red-400",
          label: "Interrupted",
        };
      case "error":
        return {
          glow: "rgba(239, 68, 68, 0.3)",
          border: "border-red-600/70",
          inner: "from-red-950/60 to-zinc-900",
          icon: "text-red-400",
          label: "Voice Error",
        };
      case "disconnected":
        return {
          glow: "transparent",
          border: "border-zinc-700/40",
          inner: "from-zinc-900 to-zinc-950",
          icon: "text-zinc-500",
          label: "Disconnected",
        };
      case "idle":
      default:
        return {
          glow: "rgba(24, 24, 27, 0.5)",
          border: "border-zinc-700/60 hover:border-teal-500/40",
          inner: "from-zinc-900/90 to-zinc-950",
          icon: "text-zinc-400",
          label: "Voice Standby",
        };
    }
  };

  const colors = getStateColors();

  if (compact) {
    return (
      <div
        className={`inline-flex items-center gap-2.5 px-3 py-1.5 rounded-full border ${colors.border} bg-zinc-900/90 shadow-sm backdrop-blur-md ${className}`}
      >
        <button
          type="button"
          onClick={handleOrbClick}
          className="relative flex items-center justify-center w-7 h-7 rounded-full bg-zinc-950 focus:outline-none focus:ring-1 focus:ring-teal-400 cursor-pointer"
          aria-label={`Voice Assistant: ${colors.label}. Click to ${state === "listening" ? "stop listening" : "start voice interaction"}.`}
          title={`Click to ${state === "listening" ? "stop" : "start"}`}
        >
          <div
            className="absolute inset-0 rounded-full transition-transform duration-200 pointer-events-none"
            style={{
              background: `radial-gradient(circle, ${colors.glow} 0%, transparent 75%)`,
              transform: `scale(${scale * 1.1})`,
            }}
            aria-hidden="true"
          />
          {state === "listening" ? (
            <Mic className={`w-3.5 h-3.5 ${colors.icon} animate-pulse`} />
          ) : state === "thinking" ? (
            <Radio className={`w-3.5 h-3.5 ${colors.icon} animate-spin`} />
          ) : state === "speaking" ? (
            <Volume2 className={`w-3.5 h-3.5 ${colors.icon} animate-pulse`} />
          ) : state === "error" ? (
            <AlertCircle className={`w-3.5 h-3.5 ${colors.icon}`} />
          ) : (
            <Mic className={`w-3.5 h-3.5 ${colors.icon}`} />
          )}
        </button>

        <span
          className={`w-2 h-2 rounded-full ${
            state === "listening"
              ? "bg-teal-400 animate-pulse"
              : state === "speaking"
                ? "bg-blue-400 animate-pulse"
                : state === "thinking"
                  ? "bg-amber-400 animate-spin"
                  : state === "error"
                    ? "bg-red-500"
                    : "bg-zinc-600"
          }`}
          aria-hidden="true"
        />
        <span className="text-[11px] font-mono font-bold uppercase tracking-wider text-zinc-200">
          {colors.label}
        </span>

        {onToggleMute && (
          <button
            type="button"
            onClick={onToggleMute}
            className="ml-1 text-zinc-400 hover:text-zinc-200 transition-colors"
            title={isMuted ? "Unmute audio" : "Mute audio"}
            aria-label={isMuted ? "Muted" : "Audio On"}
          >
            {isMuted ? (
              <VolumeX className="w-3.5 h-3.5 text-zinc-500" />
            ) : (
              <Volume2 className="w-3.5 h-3.5 text-teal-400" />
            )}
          </button>
        )}
      </div>
    );
  }

  return (
    <div
      className={`flex flex-col items-center justify-center p-4 ${className}`}
    >
      {/* Voice Orb Graphic Button */}
      <div className="relative flex items-center justify-center">
        {/* Subtle breathing glow ring */}
        <div
          className="absolute w-32 h-32 rounded-full transition-transform duration-200 pointer-events-none"
          style={{
            background: `radial-gradient(circle, ${colors.glow} 0%, transparent 70%)`,
            transform: `scale(${scale * 1.25})`,
          }}
          aria-hidden="true"
        />

        {/* Core Interactive Orb */}
        <button
          type="button"
          onClick={handleOrbClick}
          className={`relative z-10 w-24 h-24 rounded-full border-2 ${colors.border} bg-gradient-to-b ${colors.inner} shadow-lg transition-transform duration-150 flex items-center justify-center focus:outline-none focus:ring-2 focus:ring-teal-500/70 focus:ring-offset-2 focus:ring-offset-zinc-950 cursor-pointer`}
          style={{ transform: `scale(${scale})` }}
          aria-label={`Voice Assistant: ${colors.label}. Click to ${state === "listening" ? "stop listening" : "start voice interaction"}.`}
          title={`Click to ${state === "listening" ? "stop" : "start"}`}
        >
          {state === "listening" ? (
            <Mic className={`w-8 h-8 ${colors.icon} animate-pulse`} />
          ) : state === "thinking" ? (
            <Radio className={`w-8 h-8 ${colors.icon} animate-spin`} />
          ) : state === "speaking" ? (
            <Volume2 className={`w-8 h-8 ${colors.icon}`} />
          ) : state === "error" ? (
            <AlertCircle className={`w-8 h-8 ${colors.icon}`} />
          ) : (
            <Mic className={`w-8 h-8 ${colors.icon} transition-colors`} />
          )}
        </button>
      </div>

      {/* State Label & Honest Status */}
      <div className="mt-4 flex flex-col items-center gap-1.5 text-center">
        <div className="flex items-center gap-2">
          <span
            className={`w-2 h-2 rounded-full ${
              state === "listening"
                ? "bg-teal-400 animate-pulse"
                : state === "speaking"
                  ? "bg-blue-400"
                  : state === "thinking"
                    ? "bg-amber-400"
                    : state === "error"
                      ? "bg-red-500"
                      : "bg-zinc-600"
            }`}
            aria-hidden="true"
          />
          <span className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
            {colors.label}
          </span>
        </div>

        {/* Live transcription ticker */}
        {transcriptTicker && (
          <p
            className="text-xs text-zinc-400 max-w-xs truncate font-mono"
            role="status"
            aria-live="polite"
          >
            {transcriptTicker}
          </p>
        )}

        {/* Operational Voice Status Notice */}
        <p className="text-[11px] text-zinc-500 max-w-xs leading-relaxed">
          {state === "idle"
            ? "Maya voice assistant ready. Speak or type anytime."
            : state === "listening"
              ? "Capturing microphone input. Press orb to stop."
              : state === "speaking"
                ? "Click orb or speak to barge-in."
                : state === "thinking"
                  ? "Processing instruction..."
                  : ""}
        </p>
      </div>

      {/* Audio controls */}
      <div className="mt-3 flex items-center gap-2">
        {onToggleMute && (
          <button
            type="button"
            onClick={onToggleMute}
            className="px-2.5 py-1 rounded border border-zinc-800 bg-zinc-900 hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 text-xs flex items-center gap-1.5 transition-colors"
            title={isMuted ? "Unmute audio" : "Mute audio"}
          >
            {isMuted ? (
              <>
                <VolumeX className="w-3.5 h-3.5" />
                <span>Muted</span>
              </>
            ) : (
              <>
                <Volume2 className="w-3.5 h-3.5" />
                <span>Audio On</span>
              </>
            )}
          </button>
        )}
      </div>
    </div>
  );
}
