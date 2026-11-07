/**
 * React hook for real-time bidirectional voice interaction with Gemini Live (Phase 12).
 *
 * Manages WebSocket audio bridge, 16 kHz AudioWorklet microphone capture,
 * 24 kHz audio playback buffer queue, browser speech synthesis, and sub-150 ms barge-in interruption flush.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getAccessToken } from "../lib/auth-session";

export interface TranscriptItem {
  readonly sender: "user" | "model";
  readonly text: string;
  readonly timestamp: number;
}

export interface ActiveTaskInfo {
  readonly taskId: string;
  readonly intent?: string;
  readonly status?: string;
}

export interface LiveActionInfo {
  readonly toolName: string;
  readonly taskId?: string;
  readonly status: string;
  readonly intent?: string;
  readonly args?: Record<string, unknown>;
  readonly result?: Record<string, unknown>;
}

export interface UseLiveVoiceOptions {
  readonly onTranscript?: (transcript: string) => void;
  readonly onInterrupt?: () => void;
  readonly onError?: (error: Error) => void;
  readonly wsUrl?: string;
  readonly sessionId?: string | null;
  readonly onToolExecuted?: (action: LiveActionInfo) => void;
  readonly model?: string;
}

export interface UseLiveVoiceResult {
  readonly isConnected: boolean;
  readonly isSpeaking: boolean;
  readonly isListening: boolean;
  readonly isInterrupted: boolean;
  readonly interactionStatus: "IN_PROGRESS" | "IDLE" | null;
  readonly transcripts: readonly TranscriptItem[];
  readonly activeTask: ActiveTaskInfo | null;
  readonly activeAction: LiveActionInfo | null;
  readonly error: Error | null;
  readonly startSession: () => Promise<void>;
  readonly stopSession: () => void;
  readonly interruptPlayback: () => void;
  readonly sendUserText: (text: string) => void;
  readonly speakMessage: (text: string) => void;
}

/**
 * Utter text using browser SpeechSynthesis for voice feedback whenever
 * raw PCM audio is unavailable or silent (e.g. mock mode, text fallback).
 */
export function speakVoiceText(text: string, onEnd?: () => void): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  try {
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.0;
    utterance.pitch = 1.0;

    const voices = window.speechSynthesis.getVoices();
    const selectedVoice =
      voices.find(
        (v) =>
          v.lang.startsWith("en") &&
          (v.name.includes("Google") ||
            v.name.includes("Natural") ||
            v.name.includes("Jenny") ||
            v.name.includes("Samantha") ||
            v.name.includes("Zira")),
      ) || voices.find((v) => v.lang.startsWith("en"));
    if (selectedVoice) {
      utterance.voice = selectedVoice;
    }
    if (onEnd) {
      utterance.onend = onEnd;
      utterance.onerror = onEnd;
    }
    window.speechSynthesis.speak(utterance);
  } catch {
    // Speech synthesis blocked or unavailable
  }
}

export function stopVoiceSpeech(): void {
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    try {
      window.speechSynthesis.cancel();
    } catch {
      // Ignore
    }
  }
}

export function useLiveVoice(
  options: UseLiveVoiceOptions = {},
): UseLiveVoiceResult {
  const {
    onTranscript,
    onInterrupt,
    onError,
    wsUrl,
    sessionId,
    onToolExecuted,
    model = "gemini-3.8-live",
  } = options;

  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [isSpeaking, setIsSpeaking] = useState<boolean>(false);
  const [isListening, setIsListening] = useState<boolean>(false);
  const [isInterrupted, setIsInterrupted] = useState<boolean>(false);
  const [interactionStatus, setInteractionStatus] = useState<
    "IN_PROGRESS" | "IDLE" | null
  >(null);
  const [transcripts, setTranscripts] = useState<readonly TranscriptItem[]>([]);
  const [activeTask, setActiveTask] = useState<ActiveTaskInfo | null>(null);
  const [activeAction, setActiveAction] = useState<LiveActionInfo | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const activeSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const nextPlayTimeRef = useRef<number>(0);
  const isSpeakingRef = useRef<boolean>(false);

  useEffect(() => {
    isSpeakingRef.current = isSpeaking;
  }, [isSpeaking]);

  // Stop and flush all playing and scheduled audio buffers immediately (Decision D12)
  const flushAudioPlaybackQueue = useCallback(
    (isBargeIn = false) => {
      stopVoiceSpeech();

      activeSourcesRef.current.forEach((source) => {
        try {
          source.stop();
          source.disconnect();
        } catch {
          // Source may already have ended
        }
      });
      activeSourcesRef.current.clear();
      if (audioContextRef.current) {
        nextPlayTimeRef.current = audioContextRef.current.currentTime;
      }
      setIsSpeaking(false);

      if (isBargeIn) {
        setIsInterrupted(true);
        onInterrupt?.();
        // Automatically restore from transient barge-in state within Decision D12 sub-150ms SLA
        setTimeout(() => {
          setIsInterrupted(false);
        }, 150);
      }
    },
    [onInterrupt],
  );

  // Play incoming 24 kHz linear PCM audio chunk
  const playPcm24kChunk = useCallback((pcmData: ArrayBuffer) => {
    if (!audioContextRef.current) return;
    const ctx = audioContextRef.current;
    if (ctx.state === "suspended") {
      ctx.resume().catch(() => {});
    }

    const int16Array = new Int16Array(pcmData);
    if (int16Array.length === 0) return;

    // Check if chunk is purely silent frames (all zeros)
    let isSilent = true;
    for (let i = 0; i < int16Array.length; i++) {
      if (int16Array[i] !== 0) {
        isSilent = false;
        break;
      }
    }
    if (isSilent) return;

    // If real audio PCM chunk arrives, silence browser TTS to prevent echo
    stopVoiceSpeech();

    const sampleRate = 24000;
    const buffer = ctx.createBuffer(1, int16Array.length, sampleRate);
    const channelData = buffer.getChannelData(0);

    for (let i = 0; i < int16Array.length; i++) {
      channelData[i] = int16Array[i] / 32768.0;
    }

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);

    const currentTime = ctx.currentTime;
    const startTime = Math.max(currentTime, nextPlayTimeRef.current);
    source.start(startTime);
    nextPlayTimeRef.current = startTime + buffer.duration;

    activeSourcesRef.current.add(source);
    setIsSpeaking(true);
    setIsInterrupted(false);

    source.onended = () => {
      activeSourcesRef.current.delete(source);
      if (activeSourcesRef.current.size === 0) {
        setIsSpeaking(false);
      }
    };
  }, []);

  const interruptPlayback = useCallback(() => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: "CLIENT_INTERRUPT" }));
    }
    flushAudioPlaybackQueue(true);
  }, [flushAudioPlaybackQueue]);

  const sendUserText = useCallback((text: string) => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: "USER_TEXT", text }));
    }
  }, []);

  const speakMessage = useCallback((text: string) => {
    setIsSpeaking(true);
    speakVoiceText(text, () => {
      setIsSpeaking(false);
    });
  }, []);

  const stopSession = useCallback(() => {
    flushAudioPlaybackQueue(false);

    if (workletNodeRef.current) {
      workletNodeRef.current.disconnect();
      workletNodeRef.current = null;
    }

    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }

    if (audioContextRef.current && audioContextRef.current.state !== "closed") {
      audioContextRef.current.close().catch(() => {});
      audioContextRef.current = null;
    }

    if (socketRef.current) {
      socketRef.current.close();
      socketRef.current = null;
    }

    setIsConnected(false);
    setIsListening(false);
    setIsSpeaking(false);
    setIsInterrupted(false);
    setInteractionStatus(null);
  }, [flushAudioPlaybackQueue]);

  const startSession = useCallback(async () => {
    try {
      stopSession();
      setError(null);
      setIsInterrupted(false);

      const token = getAccessToken();
      let targetWsUrl = wsUrl || process.env.NEXT_PUBLIC_VOICE_WS_URL;
      if (!targetWsUrl) {
        const isHttps =
          typeof window !== "undefined" &&
          window.location.protocol === "https:";
        const wsProto = isHttps ? "wss:" : "ws:";
        const host =
          typeof window !== "undefined"
            ? window.location.hostname
            : "127.0.0.1";
        // Next.js rewrites do not proxy WebSockets; target backend port 8000 when running Next.js dev on 3000
        const isDevPort3000 =
          typeof window !== "undefined" && window.location.port === "3000";
        const port = isDevPort3000
          ? "8000"
          : (typeof window !== "undefined" && window.location.port) || "8000";
        const sessionQuery = sessionId
          ? `&session_id=${encodeURIComponent(sessionId)}`
          : "";
        const modelQuery = `&model=${encodeURIComponent(model || "gemini-3.8-live")}`;
        targetWsUrl = `${wsProto}//${host}:${port}/api/v1/voice/live?token=${token || ""}${sessionQuery}${modelQuery}`;
      }

      const socket = new WebSocket(targetWsUrl);
      socket.binaryType = "arraybuffer";
      socketRef.current = socket;

      socket.onopen = () => {
        setIsConnected(true);
      };

      socket.onmessage = (event: MessageEvent) => {
        if (event.data instanceof ArrayBuffer) {
          playPcm24kChunk(event.data);
        } else if (typeof event.data === "string") {
          try {
            const data = JSON.parse(event.data);
            if (data.type === "AUDIO_INTERRUPT") {
              flushAudioPlaybackQueue(true);
            } else if (data.type === "TRANSCRIPT_USER") {
              const item: TranscriptItem = {
                sender: "user",
                text: data.text || "",
                timestamp: Date.now(),
              };
              setTranscripts((prev) => [...prev, item]);
              onTranscript?.(data.text || "");
            } else if (data.type === "TRANSCRIPT_MODEL") {
              const item: TranscriptItem = {
                sender: "model",
                text: data.text || "",
                timestamp: Date.now(),
              };
              setTranscripts((prev) => [...prev, item]);
              onTranscript?.(data.text || "");

              // Synthesize voice audio if no active PCM stream is playing (let Maya speak aloud)
              if (data.text && activeSourcesRef.current.size === 0) {
                setIsSpeaking(true);
                speakVoiceText(data.text, () => {
                  if (activeSourcesRef.current.size === 0) {
                    setIsSpeaking(false);
                  }
                });
              }
            } else if (data.type === "TOOL_EXECUTED") {
              const action: LiveActionInfo = {
                toolName: data.tool_name || "submit_task",
                taskId: data.task_id || undefined,
                status: data.status || "running",
                intent:
                  data.intent || (data.args?.intent as string) || undefined,
                args: data.args,
                result: data.result,
              };
              setActiveAction(action);
              onToolExecuted?.(action);
              if (data.task_id) {
                setActiveTask({
                  taskId: data.task_id,
                  status: data.status || "running",
                  intent:
                    data.intent || (data.args?.intent as string) || undefined,
                });
              }
            } else if (data.type === "TASK_STATUS") {
              const action: LiveActionInfo = {
                toolName: data.tool_name || "submit_task",
                taskId: data.task_id || undefined,
                status: data.status || "running",
                intent:
                  data.intent || (data.args?.intent as string) || undefined,
                args: data.args,
                result: data.result,
              };
              setActiveAction(action);
              onToolExecuted?.(action);
              setActiveTask({
                taskId: data.task_id,
                status: data.status,
                intent:
                  data.intent || (data.args?.intent as string) || undefined,
              });
            } else if (data.type === "INTERACTION_STATUS") {
              setInteractionStatus(
                data.status === "IN_PROGRESS" || data.status === "IDLE"
                  ? data.status
                  : null,
              );
            }
          } catch {
            // Ignore non-json control message
          }
        }
      };

      socket.onerror = () => {
        const err = new Error("Voice WebSocket connection failed.");
        setError(err);
        onError?.(err);
      };

      socket.onclose = () => {
        setIsConnected(false);
        setIsListening(false);
      };

      // Set up AudioContext and AudioWorklet
      if (typeof window !== "undefined") {
        const AudioCtx =
          window.AudioContext ||
          (window as unknown as { webkitAudioContext: typeof AudioContext })
            .webkitAudioContext;
        if (AudioCtx) {
          let audioCtx: AudioContext;
          try {
            audioCtx = new AudioCtx({ sampleRate: 16000 });
          } catch {
            // Hardware fallback for platforms that do not support arbitrary sample rates
            audioCtx = new AudioCtx();
          }
          audioContextRef.current = audioCtx;

          if (navigator.mediaDevices?.getUserMedia) {
            const stream = await navigator.mediaDevices.getUserMedia({
              audio: {
                channelCount: 1,
                sampleRate: 16000,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
              },
            });
            mediaStreamRef.current = stream;

            if (audioCtx.audioWorklet) {
              await audioCtx.audioWorklet.addModule("/audio-processor.js");
              const workletNode = new AudioWorkletNode(
                audioCtx,
                "audio-processor",
              );
              workletNodeRef.current = workletNode;

              workletNode.port.onmessage = (e: MessageEvent) => {
                if (!e.data) return;

                // Handle client-side VAD control events
                if (
                  typeof e.data === "object" &&
                  !(e.data instanceof ArrayBuffer)
                ) {
                  const vadEvent = e.data as { type?: string };
                  if (vadEvent.type === "VAD_SPEECH_STOP") {
                    if (socket.readyState === WebSocket.OPEN) {
                      socket.send(JSON.stringify({ type: "VAD_SPEECH_STOP" }));
                    }
                    return;
                  }
                  if (vadEvent.type === "VAD_SPEECH_START") {
                    if (socket.readyState === WebSocket.OPEN) {
                      socket.send(JSON.stringify({ type: "VAD_SPEECH_START" }));
                    }
                    if (isSpeakingRef.current) {
                      // Barge-in: user spoke while assistant audio was playing
                      interruptPlayback();
                    }
                    return;
                  }
                }

                // Handle raw 16 kHz linear PCM audio chunk
                if (e.data instanceof ArrayBuffer) {
                  if (socket.readyState === WebSocket.OPEN) {
                    // Suppress microphone streaming while Maya is speaking to eliminate speaker echo feedback
                    if (!isSpeakingRef.current) {
                      socket.send(e.data);
                    }
                  }
                }
              };

              const sourceNode = audioCtx.createMediaStreamSource(stream);
              sourceNode.connect(workletNode);
              // Avoid connecting workletNode to audioCtx.destination to prevent speaker feedback loop
              setIsListening(true);
            }
          }
        }
      }
    } catch (err) {
      const e = err instanceof Error ? err : new Error(String(err));
      setError(e);
      onError?.(e);
      stopSession();
    }
  }, [
    flushAudioPlaybackQueue,
    interruptPlayback,
    model,
    onError,
    onToolExecuted,
    onTranscript,
    playPcm24kChunk,
    sessionId,
    stopSession,
    wsUrl,
  ]);

  useEffect(() => {
    return () => {
      stopSession();
    };
  }, [stopSession]);

  return {
    isConnected,
    isSpeaking,
    isListening,
    isInterrupted,
    interactionStatus,
    transcripts,
    activeTask,
    activeAction,
    error,
    startSession,
    stopSession,
    interruptPlayback,
    sendUserText,
    speakMessage,
  };
}
