/**
 * React hook for real-time bidirectional voice interaction with MAYA Live (Protocol v2).
 *
 * Implements bidirectional WebSocket protocol v2, AudioWorklet microphone capture,
 * 24 kHz linear PCM audio playback, sub-150ms barge-in queue flushing,
 * epoch-based interrupt gating, and turn-based transcript reconciliation.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ensureFreshAccessToken } from "../lib/auth-session";
import {
  MAYA_TTS_PITCH,
  MAYA_TTS_RATE,
  pickFemaleVoice,
} from "../lib/tts-voice";
import {
  createClientInterruptFrame,
  createHelloFrame,
  createUserTextFrame,
  isVoiceServerFrame,
  type AudioInterruptServerFrame,
  type ChatMessageServerFrame,
  type ErrorServerFrame,
  type GoAwayServerFrame,
  type LiveUnavailableServerFrame,
  type MemorySavedServerFrame,
  type ScheduleDraftServerFrame,
  scheduleDraftCardId,
  type TaskUpdateSpokenServerFrame,
  type MessagePersistedServerFrame,
  type ReadyServerFrame,
  type StepReviewServerFrame,
  type TaskEventServerFrame,
  type TaskStatusServerFrame,
  type TranscriptServerFrame,
  type TurnStateServerFrame,
} from "../lib/voice-protocol";
import { getVoiceWebSocketUrl } from "../lib/voice-url";
import {
  applySpokenUpdate,
  applyStepReview,
  type TaskUpdateBlock,
} from "../lib/voice-task-updates";

export type LiveVoiceState =
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "interrupted"
  | "unavailable"
  | "error";

export interface TranscriptItem {
  readonly id: string;
  readonly turnId: string;
  readonly role: "user" | "assistant";
  /** Backward compatibility alias for existing components */
  readonly sender: "user" | "model";
  readonly text: string;
  readonly isFinal: boolean;
  /** Backward compatibility alias for existing components */
  readonly final: boolean;
  readonly timestamp: number;
  /** Stored message id, set once MESSAGE_PERSISTED arrives for this turn. */
  readonly messageId?: string | null;
  /** True when `text` is Markdown a tool put in the chat (CHAT_MESSAGE), not a spoken turn. */
  readonly markdown?: boolean;
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

/** A task update MAYA spoke on her own (TASK_UPDATE_SPOKEN frame). */
export interface SpokenTaskUpdate {
  readonly taskId: string | null;
  readonly milestone: TaskUpdateSpokenServerFrame["milestone"];
  readonly text: string;
  readonly catchUp: boolean;
  /** Milliseconds since epoch when the frame arrived. */
  readonly timestamp: number;
}

export type VoiceScope = "master" | "conversation";

export interface StartVoiceOptions {
  readonly scope?: VoiceScope;
  /**
   * Listen-only: MAYA speaks, but the microphone is never requested or opened (used to read a
   * scheduled update when the app opens). The session starts muted. Ending it and starting a
   * normal session is how the person gets a microphone.
   */
  readonly listenOnly?: boolean;
  /** Explicit mute choice for this start; overrides the remembered master mute. */
  readonly muted?: boolean;
}

/** Per-device choice for the master voice microphone (try/catch: storage can be unavailable). */
export const MASTER_MUTE_STORAGE_KEY = "maya.voice.master.muted";

export function readMasterMute(): boolean {
  try {
    return window.localStorage.getItem(MASTER_MUTE_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeMasterMute(value: boolean): void {
  try {
    window.localStorage.setItem(MASTER_MUTE_STORAGE_KEY, value ? "1" : "0");
  } catch {
    // Storage blocked: the choice simply lasts for this session.
  }
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

export interface LiveSavedMemory {
  readonly memoryId: string;
  readonly text: string;
  readonly updated: boolean;
  /** Milliseconds since epoch when the frame arrived. */
  readonly timestamp: number;
}

export interface UseLiveVoiceResult {
  readonly state: LiveVoiceState;
  readonly isConnected: boolean;
  readonly isSpeaking: boolean;
  readonly isListening: boolean;
  readonly isInterrupted: boolean;
  readonly interactionStatus: "IN_PROGRESS" | "IDLE" | null;
  readonly sessionId: string | null;
  readonly readySessionId: string | null;
  readonly unavailableReason: string | null;
  readonly transcripts: readonly TranscriptItem[];
  readonly activeTask: ActiveTaskInfo | null;
  /** The latest TASK_EVENT frame (task/subtask progress), or null. */
  readonly lastTaskEvent: TaskEventServerFrame | null;
  /** Memories MAYA saved during this live session (MEMORY_SAVED frames), oldest first. */
  readonly savedMemories: readonly LiveSavedMemory[];
  /** Scheduled-report drafts MAYA made by voice (SCHEDULE_DRAFT frames); shown as the chat card. */
  readonly scheduleDrafts?: readonly {
    readonly draftId: string;
    readonly messageId: string;
  }[];
  /** The latest task update MAYA spoke by herself (needs no chat message), or null. */
  readonly lastSpokenUpdate: SpokenTaskUpdate | null;
  /** Spoken task updates, one block per task, updated in place (no chat message each). */
  readonly taskUpdates: readonly TaskUpdateBlock[];
  readonly activeAction: LiveActionInfo | null;
  readonly error: Error | null;
  /** `scope: "master"` starts the app-wide tracker (top bar); the default is conversation-bound. */
  readonly startSession: (options?: StartVoiceOptions) => Promise<void>;
  readonly stopSession: () => void;
  readonly interruptPlayback: () => void;
  /**
   * True while the user has muted their own microphone. The session stays open: MAYA keeps
   * speaking and task updates keep arriving, but no microphone audio leaves the browser.
   */
  readonly muted?: boolean;
  /** Mute or unmute the microphone for the rest of this session (no-op while not connected). */
  readonly toggleMute?: () => void;
  readonly sendText: (text: string) => void;
  readonly sendUserText: (text: string) => void;
  readonly speakMessage: (text: string) => void;
}

/**
 * Utter text using browser SpeechSynthesis for manual voice feedback.
 * Automatic synthesis has been removed; this helper is kept for explicit user actions.
 */
export function speakVoiceText(text: string, onEnd?: () => void): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  try {
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = MAYA_TTS_RATE;
    utterance.pitch = MAYA_TTS_PITCH;

    // MAYA's voice is a female voice: the most natural one this device offers. If none can be
    // told apart the system default is kept (never a voice known to be male).
    const selectedVoice = pickFemaleVoice(window.speechSynthesis.getVoices());
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
  } = options;

  const [state, setState] = useState<LiveVoiceState>("idle");
  const [readySessionId, setReadySessionId] = useState<string | null>(null);
  const [unavailableReason, setUnavailableReason] = useState<string | null>(
    null,
  );
  const [transcripts, setTranscripts] = useState<readonly TranscriptItem[]>([]);
  const [activeTask, setActiveTask] = useState<ActiveTaskInfo | null>(null);
  const [lastTaskEvent, setLastTaskEvent] =
    useState<TaskEventServerFrame | null>(null);
  const [scheduleDrafts, setScheduleDrafts] = useState<
    readonly { draftId: string; messageId: string }[]
  >([]);
  const [savedMemories, setSavedMemories] = useState<
    readonly LiveSavedMemory[]
  >([]);
  const [lastSpokenUpdate, setLastSpokenUpdate] =
    useState<SpokenTaskUpdate | null>(null);
  const [taskUpdates, setTaskUpdates] = useState<readonly TaskUpdateBlock[]>(
    [],
  );
  const persistedIdsRef = useRef<Map<string, string>>(new Map());
  const [activeAction, setActiveAction] = useState<LiveActionInfo | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  // Mute is kept across an automatic reconnect (the microphone must never reopen by itself) and
  // is cleared only when the user ends the session.
  const [muted, setMuted] = useState(false);
  const mutedRef = useRef(false);
  // The scope of the CURRENT session; an automatic reconnect keeps it.
  const scopeRef = useRef<VoiceScope>("conversation");
  const listenOnlyRef = useRef(false);
  const speechActiveRef = useRef(false);
  const activeSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const nextPlayTimeRef = useRef<number>(0);
  const currentEpochRef = useRef<number>(0);
  const stateRef = useRef<LiveVoiceState>("idle");
  const readySessionRef = useRef<string | null>(null);
  const resumeAttemptsRef = useRef<number>(0);
  const startRef = useRef<(resumeId?: string | null) => Promise<void>>(
    async () => {},
  );

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const stopMicrophone = useCallback(() => {
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
  }, []);

  const flushAudioPlaybackQueue = useCallback(
    (isBargeIn = false, newEpoch?: number) => {
      stopVoiceSpeech();

      if (typeof newEpoch === "number") {
        if (newEpoch < currentEpochRef.current) {
          return; // stale interrupt from an older epoch
        }
        currentEpochRef.current = newEpoch;
      }

      activeSourcesRef.current.forEach((source) => {
        try {
          source.stop();
          source.disconnect();
        } catch {
          // Source may have already ended
        }
      });
      activeSourcesRef.current.clear();

      if (audioContextRef.current) {
        nextPlayTimeRef.current = audioContextRef.current.currentTime;
      }

      if (isBargeIn) {
        setState("interrupted");
        onInterrupt?.();
        // Restore to listening state after transient barge-in flush
        setTimeout(() => {
          setState((prev) => (prev === "interrupted" ? "listening" : prev));
        }, 150);
      }
    },
    [onInterrupt],
  );

  const playPcm24kChunk = useCallback((pcmData: ArrayBuffer) => {
    if (!audioContextRef.current) return;
    const ctx = audioContextRef.current;
    if (ctx.state === "suspended") {
      ctx.resume().catch(() => {});
    }

    const int16Array = new Int16Array(pcmData);
    if (int16Array.length === 0) return;

    let isSilent = true;
    for (let i = 0; i < int16Array.length; i++) {
      if (int16Array[i] !== 0) {
        isSilent = false;
        break;
      }
    }
    if (isSilent) return;

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
    setState("speaking");

    source.onended = () => {
      activeSourcesRef.current.delete(source);
      if (activeSourcesRef.current.size === 0) {
        setState((prev) => (prev === "speaking" ? "listening" : prev));
      }
    };
  }, []);

  const interruptPlayback = useCallback(() => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify(createClientInterruptFrame()));
    }
    flushAudioPlaybackQueue(true);
  }, [flushAudioPlaybackQueue]);

  const sendText = useCallback((text: string) => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify(createUserTextFrame(text)));
    }
  }, []);

  /** Apply the mute choice to the live microphone tracks and to what is sent. */
  const applyMute = useCallback((value: boolean) => {
    mutedRef.current = value;
    setMuted(value);
    mediaStreamRef.current
      ?.getTracks()
      .forEach((track) => (track.enabled = !value));
    // Muting in the middle of a sentence: tell the server the turn has ended, so it does not
    // wait for audio that will never arrive.
    const socket = socketRef.current;
    if (
      value &&
      speechActiveRef.current &&
      socket &&
      socket.readyState === WebSocket.OPEN
    ) {
      socket.send(JSON.stringify({ type: "VAD_SPEECH_STOP" }));
    }
    if (value) speechActiveRef.current = false;
  }, []);

  const toggleMute = useCallback(() => {
    if (stateRef.current === "idle" || stateRef.current === "unavailable") {
      return;
    }
    // A listen-only session has no microphone to open.
    if (listenOnlyRef.current) return;
    const next = !mutedRef.current;
    applyMute(next);
    if (scopeRef.current === "master") writeMasterMute(next);
  }, [applyMute]);

  const speakMessage = useCallback((text: string) => {
    setState("speaking");
    speakVoiceText(text, () => {
      setState((prev) => (prev === "speaking" ? "listening" : prev));
    });
  }, []);

  const stopSession = useCallback(() => {
    flushAudioPlaybackQueue(false);
    stopMicrophone();

    if (socketRef.current) {
      socketRef.current.close();
      socketRef.current = null;
    }

    setState("idle");
    setReadySessionId(null);
    readySessionRef.current = null;
  }, [flushAudioPlaybackQueue, stopMicrophone]);

  const startSession = useCallback(
    async (resumeId?: string | null) => {
      try {
        stopSession();
        setError(null);
        setUnavailableReason(null);
        setState("connecting");

        // Obtain a fresh access token before connection
        const freshToken = await ensureFreshAccessToken();
        const effectiveSessionId = resumeId ?? sessionId ?? null;
        const scope = scopeRef.current;
        const targetWsUrl = wsUrl || getVoiceWebSocketUrl(effectiveSessionId);

        const socket = new WebSocket(targetWsUrl);
        socket.binaryType = "arraybuffer";
        socketRef.current = socket;

        socket.onopen = () => {
          // First frame: send HELLO with fresh access token and protocol: 2
          const helloFrame = createHelloFrame({
            token: freshToken,
            sessionId: effectiveSessionId,
            client: "web",
            scope,
          });
          socket.send(JSON.stringify(helloFrame));
        };

        socket.onmessage = (event: MessageEvent) => {
          if (event.data instanceof ArrayBuffer) {
            playPcm24kChunk(event.data);
            return;
          }

          if (typeof event.data === "string") {
            try {
              const data = JSON.parse(event.data) as Record<string, unknown>;
              const type = data.type;

              if (type === "READY") {
                const readyFrame = data as unknown as ReadyServerFrame;
                setReadySessionId(readyFrame.session_id);
                readySessionRef.current = readyFrame.session_id;
                resumeAttemptsRef.current = 0;
                setState("listening");
              } else if (type === "LIVE_UNAVAILABLE") {
                const unavail = data as unknown as LiveUnavailableServerFrame;
                setUnavailableReason(unavail.reason);
                setState("unavailable");
                stopMicrophone();
              } else if (type === "TURN_STATE") {
                const turn = data as unknown as TurnStateServerFrame;
                setState(turn.state);
              } else if (type === "TRANSCRIPT") {
                const tFrame = data as unknown as TranscriptServerFrame;
                const turnId = tFrame.turn_id;
                const role = tFrame.role;
                const sender = role === "assistant" ? "model" : "user";
                const isFinal = tFrame.final;

                setTranscripts((prev) => {
                  const existingIdx = prev.findIndex(
                    (item) => item.id === turnId,
                  );
                  let fullText = "";

                  if (existingIdx >= 0) {
                    const existing = prev[existingIdx];
                    fullText = isFinal
                      ? (tFrame.text ?? existing.text + (tFrame.delta || ""))
                      : existing.text + (tFrame.delta || "");

                    const updatedItem: TranscriptItem = {
                      id: turnId,
                      turnId,
                      role,
                      sender,
                      text: fullText,
                      isFinal,
                      final: isFinal,
                      timestamp: existing.timestamp,
                      messageId:
                        existing.messageId ??
                        persistedIdsRef.current.get(turnId) ??
                        null,
                    };

                    const next = [...prev];
                    next[existingIdx] = updatedItem;
                    return next;
                  }

                  fullText = isFinal
                    ? (tFrame.text ?? (tFrame.delta || ""))
                    : tFrame.delta || "";

                  const newItem: TranscriptItem = {
                    id: turnId,
                    turnId,
                    role,
                    sender,
                    text: fullText,
                    isFinal,
                    final: isFinal,
                    timestamp: Date.now(),
                    messageId: persistedIdsRef.current.get(turnId) ?? null,
                  };
                  return [...prev, newItem];
                });

                const reportedText =
                  isFinal && tFrame.text ? tFrame.text : tFrame.delta;
                if (reportedText) {
                  onTranscript?.(reportedText);
                }
              } else if (type === "MESSAGE_PERSISTED") {
                const persisted =
                  data as unknown as MessagePersistedServerFrame;
                persistedIdsRef.current.set(
                  persisted.turn_id,
                  persisted.message_id,
                );
                setTranscripts((prev) =>
                  prev.map((item) =>
                    item.id === persisted.turn_id
                      ? { ...item, messageId: persisted.message_id }
                      : item,
                  ),
                );
              } else if (type === "CHAT_MESSAGE") {
                if (isVoiceServerFrame(data)) {
                  const chat = data as unknown as ChatMessageServerFrame;
                  const itemId = `chat:${chat.message_id}`;
                  setTranscripts((prev) =>
                    prev.some(
                      (item) =>
                        item.messageId === chat.message_id ||
                        item.id === itemId,
                    )
                      ? prev
                      : [
                          ...prev,
                          {
                            id: itemId,
                            turnId: itemId,
                            role: "assistant",
                            sender: "model",
                            text: chat.content,
                            isFinal: true,
                            final: true,
                            timestamp: Date.now(),
                            messageId: chat.message_id,
                            markdown: true,
                          },
                        ],
                  );
                }
              } else if (type === "MEMORY_SAVED") {
                const saved = data as unknown as MemorySavedServerFrame;
                setSavedMemories((prev) =>
                  prev.some((m) => m.memoryId === saved.memory_id)
                    ? prev
                    : [
                        ...prev,
                        {
                          memoryId: saved.memory_id,
                          text: saved.text,
                          updated: saved.updated === true,
                          timestamp: Date.now(),
                        },
                      ],
                );
              } else if (type === "SCHEDULE_DRAFT") {
                const frame = data as unknown as ScheduleDraftServerFrame;
                const draftId = scheduleDraftCardId(frame);
                if (draftId !== null) {
                  setScheduleDrafts((prev) =>
                    prev.some((d) => d.draftId === draftId)
                      ? prev
                      : [...prev, { draftId, messageId: frame.message_id }],
                  );
                }
              } else if (type === "TASK_EVENT") {
                setLastTaskEvent(data as unknown as TaskEventServerFrame);
              } else if (type === "TASK_UPDATE_SPOKEN") {
                const spoken = data as unknown as TaskUpdateSpokenServerFrame;
                setTaskUpdates((prev) =>
                  applySpokenUpdate(prev, spoken, Date.now()),
                );
                setLastSpokenUpdate({
                  taskId: spoken.task_id ?? null,
                  milestone: spoken.milestone,
                  text: spoken.text,
                  catchUp: spoken.catch_up === true,
                  timestamp: Date.now(),
                });
              } else if (type === "STEP_REVIEW") {
                const review = data as unknown as StepReviewServerFrame;
                setTaskUpdates((prev) => applyStepReview(prev, review));
              } else if (type === "AUDIO_INTERRUPT") {
                const interruptFrame =
                  data as unknown as AudioInterruptServerFrame;
                flushAudioPlaybackQueue(true, interruptFrame.epoch);
              } else if (type === "GO_AWAY") {
                const goAway = data as unknown as GoAwayServerFrame;
                // Resumable: the server reconnects the provider itself and sends
                // SESSION_RENEWED. Not resumable: ERROR + close 4503 follow and
                // onclose reconnects with the same session_id.
                void goAway;
              } else if (type === "ERROR") {
                const errFrame = data as unknown as ErrorServerFrame;
                const err = new Error(errFrame.message || errFrame.code);
                setError(err);
                onError?.(err);
                if (
                  errFrame.fatal &&
                  errFrame.code === "go_away_not_resumable"
                ) {
                  // handled by onclose (4503): reconnect with same session_id
                } else if (errFrame.fatal) {
                  setState("error");
                  stopMicrophone();
                }
              } else if (type === "TASK_STATUS") {
                const taskFrame = data as unknown as TaskStatusServerFrame;
                const action: LiveActionInfo = {
                  toolName: taskFrame.tool_name || "submit_task",
                  taskId: taskFrame.task_id || undefined,
                  status: taskFrame.status || "running",
                  intent: taskFrame.intent,
                  args: taskFrame.args,
                  result: taskFrame.result,
                };
                setActiveAction(action);
                onToolExecuted?.(action);
                setActiveTask({
                  taskId: taskFrame.task_id,
                  status: taskFrame.status,
                  intent: taskFrame.intent,
                });
              } else if (type === "SESSION_RENEWED") {
                // Session was transparently renewed
              }
            } catch {
              // Non-JSON frame ignored
            }
          }
        };

        socket.onerror = () => {
          if (socketRef.current !== socket) return;
          const err = new Error("Voice WebSocket connection failed.");
          setError(err);
          onError?.(err);
          setState("error");
          stopMicrophone();
        };

        socket.onclose = (event) => {
          if (socketRef.current !== socket) return;
          stopMicrophone();
          const resumeId = readySessionRef.current;
          if (
            event.code === 4503 &&
            resumeId !== null &&
            resumeAttemptsRef.current < 3
          ) {
            resumeAttemptsRef.current += 1;
            setState("connecting");
            setTimeout(() => {
              if (stateRef.current === "connecting") {
                void startRef.current(resumeId);
              }
            }, 500);
          } else if (event.code === 4503) {
            setState("unavailable");
          } else if (
            stateRef.current !== "unavailable" &&
            stateRef.current !== "error"
          ) {
            setState("idle");
          }
        };

        // Set up AudioContext and AudioWorklet microphone capture
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
              audioCtx = new AudioCtx();
            }
            audioContextRef.current = audioCtx;

            if (
              navigator.mediaDevices?.getUserMedia &&
              !listenOnlyRef.current
            ) {
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
              // A reconnect while muted must not reopen the microphone.
              stream
                .getTracks()
                .forEach((track) => (track.enabled = !mutedRef.current));

              if (audioCtx.audioWorklet) {
                await audioCtx.audioWorklet.addModule("/audio-processor.js");
                const workletNode = new AudioWorkletNode(
                  audioCtx,
                  "audio-processor",
                );
                workletNodeRef.current = workletNode;

                workletNode.port.onmessage = (e: MessageEvent) => {
                  if (!e.data) return;
                  // Muted: nothing from the microphone (audio or voice-activity events) is sent.
                  if (mutedRef.current) return;

                  if (
                    typeof e.data === "object" &&
                    !(e.data instanceof ArrayBuffer)
                  ) {
                    const vadEvent = e.data as { type?: string };
                    if (vadEvent.type === "VAD_SPEECH_STOP") {
                      speechActiveRef.current = false;
                      if (socket.readyState === WebSocket.OPEN) {
                        socket.send(
                          JSON.stringify({ type: "VAD_SPEECH_STOP" }),
                        );
                      }
                      return;
                    }
                    if (vadEvent.type === "VAD_SPEECH_START") {
                      speechActiveRef.current = true;
                      if (socket.readyState === WebSocket.OPEN) {
                        socket.send(
                          JSON.stringify({ type: "VAD_SPEECH_START" }),
                        );
                      }
                      if (stateRef.current === "speaking") {
                        interruptPlayback();
                      }
                      return;
                    }
                  }

                  // Send raw mic PCM chunk continuously (server gates stale audio)
                  if (e.data instanceof ArrayBuffer) {
                    if (socket.readyState === WebSocket.OPEN) {
                      socket.send(e.data);
                    }
                  }
                };

                const sourceNode = audioCtx.createMediaStreamSource(stream);
                sourceNode.connect(workletNode);
              }
            }
          }
        }
      } catch (err) {
        const e = err instanceof Error ? err : new Error(String(err));
        setError(e);
        onError?.(e);
        setState("error");
        stopSession();
      }
    },
    [
      flushAudioPlaybackQueue,
      interruptPlayback,
      onError,
      onToolExecuted,
      onTranscript,
      playPcm24kChunk,
      sessionId,
      stopMicrophone,
      stopSession,
      wsUrl,
    ],
  );

  useEffect(() => {
    startRef.current = startSession;
  }, [startSession]);

  const startPublic = useCallback(
    (startOptions?: StartVoiceOptions) => {
      const scope =
        startOptions?.scope === "master" ? "master" : "conversation";
      scopeRef.current = scope;
      // The master microphone choice is remembered on this device; a conversation call starts open.
      const listenOnly = startOptions?.listenOnly === true;
      listenOnlyRef.current = listenOnly;
      const startMuted =
        listenOnly ||
        (startOptions?.muted ?? (scope === "master" && readMasterMute()));
      mutedRef.current = startMuted;
      setMuted(startMuted);
      return startSession();
    },
    [startSession],
  );

  // The user ending the session clears the mute; an automatic reconnect (which calls the
  // internal stopSession) keeps it.
  const stopSessionByUser = useCallback(() => {
    listenOnlyRef.current = false;
    mutedRef.current = false;
    speechActiveRef.current = false;
    setMuted(false);
    stopSession();
  }, [stopSession]);

  useEffect(() => {
    return () => {
      stopSession();
    };
  }, [stopSession]);

  const isSpeaking = state === "speaking";
  const isListening = state === "listening";
  const isInterrupted = state === "interrupted";
  const isConnected =
    state !== "idle" && state !== "unavailable" && state !== "error";
  const interactionStatus: "IN_PROGRESS" | "IDLE" | null =
    state === "thinking" || state === "speaking" || state === "interrupted"
      ? "IN_PROGRESS"
      : state === "listening" || state === "idle"
        ? "IDLE"
        : null;

  return {
    state,
    isConnected,
    isSpeaking,
    isListening,
    isInterrupted,
    interactionStatus,
    sessionId: readySessionId,
    readySessionId,
    unavailableReason,
    transcripts,
    activeTask,
    lastTaskEvent,
    savedMemories,
    scheduleDrafts,
    lastSpokenUpdate,
    taskUpdates,
    activeAction,
    error,
    startSession: startPublic,
    stopSession: stopSessionByUser,
    interruptPlayback,
    muted,
    toggleMute,
    sendText,
    sendUserText: sendText,
    speakMessage,
  };
}
