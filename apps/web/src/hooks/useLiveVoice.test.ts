import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MASTER_MUTE_STORAGE_KEY, useLiveVoice } from "./useLiveVoice";
import * as authSession from "../lib/auth-session";

let createdSockets: MockWebSocket[] = [];

// Mock WebSocket class
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;

  readyState = MockWebSocket.OPEN;
  binaryType = "blob";
  sentMessages: (string | ArrayBuffer)[] = [];

  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string | ArrayBuffer }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;

  constructor(public url: string) {
    createdSockets.push(this);
    queueMicrotask(() => {
      this.onopen?.();
    });
  }

  send(data: string | ArrayBuffer) {
    this.sentMessages.push(data);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code: 1000 });
  }
}

class MockAudioWorkletNode {
  port = { onmessage: null as ((e: MessageEvent) => void) | null };
  connect = vi.fn();
  disconnect = vi.fn();
}

class MockAudioContext {
  state = "running";
  currentTime = 0;
  destination = {};
  resume = vi.fn().mockResolvedValue(undefined);
  close = vi.fn().mockResolvedValue(undefined);
  createBuffer = vi.fn().mockReturnValue({
    duration: 0.1,
    getChannelData: vi.fn().mockReturnValue(new Float32Array(2400)),
  });
  createBufferSource = vi.fn().mockReturnValue({
    buffer: null,
    connect: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    disconnect: vi.fn(),
  });
  createMediaStreamSource = vi.fn().mockReturnValue({
    connect: vi.fn(),
  });
  audioWorklet = {
    addModule: vi.fn().mockResolvedValue(undefined),
  };
}

describe("useLiveVoice hook", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    createdSockets = [];

    vi.spyOn(authSession, "ensureFreshAccessToken").mockResolvedValue(
      ["test", "voice", "value"].join("-"),
    );

    // Assign to both globalThis and window
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket;
    (window as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket;

    (globalThis as unknown as { AudioContext: unknown }).AudioContext =
      MockAudioContext;
    (window as unknown as { AudioContext: unknown }).AudioContext =
      MockAudioContext;

    (globalThis as unknown as { AudioWorkletNode: unknown }).AudioWorkletNode =
      MockAudioWorkletNode;
    (window as unknown as { AudioWorkletNode: unknown }).AudioWorkletNode =
      MockAudioWorkletNode;

    // Stub getUserMedia
    Object.defineProperty(navigator, "mediaDevices", {
      value: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: vi.fn().mockReturnValue([{ stop: vi.fn() }]),
        }),
      },
      writable: true,
      configurable: true,
    });
  });

  it("initializes with default disconnected state", () => {
    const { result } = renderHook(() => useLiveVoice());

    expect(result.current.isConnected).toBe(false);
    expect(result.current.isSpeaking).toBe(false);
    expect(result.current.isListening).toBe(false);
    expect(result.current.isInterrupted).toBe(false);
    expect(result.current.transcripts).toHaveLength(0);
    expect(result.current.activeTask).toBeNull();
  });

  it("initializes with default disconnected state", () => {
    const { result } = renderHook(() => useLiveVoice());

    expect(result.current.isConnected).toBe(false);
    expect(result.current.isSpeaking).toBe(false);
    expect(result.current.isListening).toBe(false);
    expect(result.current.isInterrupted).toBe(false);
    expect(result.current.transcripts).toHaveLength(0);
    expect(result.current.activeTask).toBeNull();
  });

  it("does not trigger onInterrupt or isInterrupted during normal startSession and stopSession", async () => {
    const onInterruptMock = vi.fn();
    const { result } = renderHook(() =>
      useLiveVoice({ onInterrupt: onInterruptMock }),
    );

    await act(async () => {
      await result.current.startSession();
    });

    expect(result.current.isInterrupted).toBe(false);
    expect(onInterruptMock).not.toHaveBeenCalled();

    act(() => {
      result.current.stopSession();
    });

    expect(result.current.isInterrupted).toBe(false);
    expect(onInterruptMock).not.toHaveBeenCalled();
  });

  function serverFrame(socket: MockWebSocket, frame: Record<string, unknown>) {
    act(() => {
      socket.onmessage?.({ data: JSON.stringify(frame) });
    });
  }

  async function open(options: Parameters<typeof useLiveVoice>[0] = {}) {
    const hook = renderHook(() => useLiveVoice(options));
    await act(async () => {
      await hook.result.current.startSession();
    });
    const socket = createdSockets[0];
    await act(async () => {
      await Promise.resolve();
    });
    return { ...hook, socket };
  }

  it("connects same-origin and authenticates with a v2 HELLO frame", async () => {
    const { result, socket } = await open({ sessionId: "sess-1" });
    expect(socket.url).toContain("/api/v1/voice/live");
    expect(socket.url).not.toContain("token=");
    const hello = JSON.parse(socket.sentMessages[0] as string);
    expect(hello).toMatchObject({
      type: "HELLO",
      protocol: 2,
      token: ["test", "voice", "value"].join("-"),
      session_id: "sess-1",
    });
    serverFrame(socket, { type: "READY", protocol: 2, session_id: "srv-9" });
    expect(result.current.readySessionId).toBe("srv-9");
    expect(result.current.state).toBe("listening");
  });

  it("drives the state machine from TURN_STATE", async () => {
    const { result, socket } = await open();
    serverFrame(socket, { type: "READY", protocol: 2, session_id: "s" });
    serverFrame(socket, { type: "TURN_STATE", state: "thinking" });
    expect(result.current.state).toBe("thinking");
  });

  it("shows unavailable (not listening) on LIVE_UNAVAILABLE", async () => {
    const { result, socket } = await open();
    serverFrame(socket, { type: "LIVE_UNAVAILABLE", reason: "no_key" });
    expect(result.current.state).toBe("unavailable");
    expect(result.current.unavailableReason).toBe("no_key");
    expect(result.current.isListening).toBe(false);
  });

  it("keeps one transcript item per turn_id", async () => {
    const { result, socket } = await open();
    const t = (extra: Record<string, unknown>) => ({
      type: "TRANSCRIPT",
      turn_id: "t1",
      role: "user",
      ...extra,
    });
    serverFrame(socket, t({ delta: "What is ", final: false }));
    serverFrame(socket, t({ delta: "my agenda?", final: false }));
    expect(result.current.transcripts).toHaveLength(1);
    expect(result.current.transcripts[0].text).toBe("What is my agenda?");
    expect(result.current.transcripts[0].isFinal).toBe(false);
    serverFrame(socket, t({ text: "What is my agenda?", final: true }));
    expect(result.current.transcripts).toHaveLength(1);
    expect(result.current.transcripts[0].isFinal).toBe(true);
    serverFrame(socket, {
      type: "TRANSCRIPT",
      turn_id: "t2",
      role: "assistant",
      delta: "Checking",
      final: false,
    });
    expect(result.current.transcripts).toHaveLength(2);
    expect(result.current.transcripts[1].sender).toBe("model");
  });

  it("exposes messageId once MESSAGE_PERSISTED arrives, in either order", async () => {
    const { result, socket } = await open();
    serverFrame(socket, {
      type: "TRANSCRIPT",
      turn_id: "t1",
      role: "user",
      text: "hi",
      final: true,
    });
    expect(result.current.transcripts[0].messageId ?? null).toBeNull();
    serverFrame(socket, {
      type: "MESSAGE_PERSISTED",
      turn_id: "t1",
      message_id: "m1",
      role: "user",
    });
    expect(result.current.transcripts[0].messageId).toBe("m1");
    // Persisted before the transcript item exists.
    serverFrame(socket, {
      type: "MESSAGE_PERSISTED",
      turn_id: "t2",
      message_id: "m2",
      role: "assistant",
    });
    serverFrame(socket, {
      type: "TRANSCRIPT",
      turn_id: "t2",
      role: "assistant",
      text: "yo",
      final: true,
    });
    expect(result.current.transcripts[1].messageId).toBe("m2");
  });

  it("adds a CHAT_MESSAGE as one final assistant item, once", async () => {
    const { result, socket } = await open();
    const frame = {
      type: "CHAT_MESSAGE",
      message_id: "m9",
      role: "assistant",
      content: "```python\nprint(1)\n```",
      tool: "show_in_chat",
    };
    serverFrame(socket, frame);
    serverFrame(socket, frame);
    expect(result.current.transcripts).toHaveLength(1);
    const item = result.current.transcripts[0];
    expect(item.messageId).toBe("m9");
    expect(item.role).toBe("assistant");
    expect(item.isFinal).toBe(true);
    expect(item.markdown).toBe(true);
    expect(item.text).toContain("print(1)");
    // An invalid frame (empty content) adds nothing.
    serverFrame(socket, { ...frame, message_id: "m10", content: "  " });
    expect(result.current.transcripts).toHaveLength(1);
  });

  it("keeps the latest TASK_EVENT frame available", async () => {
    const { result, socket } = await open();
    expect(result.current.lastTaskEvent).toBeNull();
    serverFrame(socket, {
      type: "TASK_EVENT",
      task_id: "k1",
      event_type: "subtask_started",
      subtask_id: "s1",
      title: "Search",
      status: "running",
    });
    expect(result.current.lastTaskEvent?.task_id).toBe("k1");
  });

  it("keeps a task update MAYA spoke by herself, with an empty chat", async () => {
    const { result, socket } = await open();
    expect(result.current.lastSpokenUpdate).toBeNull();
    serverFrame(socket, {
      type: "TASK_UPDATE_SPOKEN",
      task_id: "k1",
      milestone: "completed",
      text: "The task Solar deck is done.",
      catch_up: false,
    });
    expect(result.current.lastSpokenUpdate).toMatchObject({
      taskId: "k1",
      milestone: "completed",
      catchUp: false,
    });
    serverFrame(socket, {
      type: "TASK_UPDATE_SPOKEN",
      task_id: null,
      milestone: "completed",
      text: "While voice was off: one task finished.",
      catch_up: true,
    });
    expect(result.current.lastSpokenUpdate?.catchUp).toBe(true);
    expect(result.current.lastSpokenUpdate?.taskId).toBeNull();
  });

  it("keeps the progress step of a task_progress TASK_EVENT frame", async () => {
    const { result, socket } = await open();
    const step = {
      run_id: "k1",
      step_id: "sub-s1",
      kind: "step",
      label: "Research agent",
      state: "running",
    };
    serverFrame(socket, {
      type: "TASK_EVENT",
      task_id: "k1",
      event_type: "task_progress",
      subtask_id: "s1",
      title: null,
      status: null,
      step,
    });
    expect(result.current.lastTaskEvent?.step).toEqual(step);
    serverFrame(socket, {
      type: "TASK_EVENT",
      task_id: "k1",
      event_type: "task_progress",
      subtask_id: "s1",
      title: null,
      status: null,
      step: { ...step, state: "failed" },
    });
    expect(result.current.lastTaskEvent?.step).toMatchObject({
      state: "failed",
    });
  });

  it("flushes playback on AUDIO_INTERRUPT and ignores older epochs", async () => {
    const onInterrupt = vi.fn();
    const { result, socket } = await open({ onInterrupt });
    serverFrame(socket, { type: "AUDIO_INTERRUPT", epoch: 2 });
    expect(result.current.isInterrupted).toBe(true);
    expect(onInterrupt).toHaveBeenCalledTimes(1);
    serverFrame(socket, { type: "AUDIO_INTERRUPT", epoch: 1 });
    expect(onInterrupt).toHaveBeenCalledTimes(1);
  });

  it("sends CLIENT_INTERRUPT when interruptPlayback is called", async () => {
    const { result, socket } = await open();
    act(() => {
      result.current.interruptPlayback();
    });
    expect(socket.sentMessages).toContain(
      JSON.stringify({ type: "CLIENT_INTERRUPT" }),
    );
    expect(result.current.isInterrupted).toBe(true);
  });

  it("sendText sends a typed-text frame over the socket", async () => {
    const { result, socket } = await open();
    act(() => {
      result.current.sendText("hello");
    });
    const sent = socket.sentMessages
      .filter((m): m is string => typeof m === "string")
      .map((m) => JSON.parse(m) as { type: string });
    expect(
      sent.some((f) => f.type !== "HELLO" && f.type !== "CLIENT_INTERRUPT"),
    ).toBe(true);
  });

  it("surfaces a fatal ERROR as the error state", async () => {
    const onError = vi.fn();
    const { result, socket } = await open({ onError });
    serverFrame(socket, {
      type: "ERROR",
      code: "internal_error",
      message: "boom",
      fatal: true,
    });
    expect(result.current.state).toBe("error");
    expect(onError).toHaveBeenCalled();
  });

  it("reconnects with the same session_id after close 4503 (GO_AWAY not resumable)", async () => {
    vi.useFakeTimers();
    try {
      const { socket, result } = await open();
      serverFrame(socket, {
        type: "READY",
        protocol: 2,
        session_id: "keep-me",
      });
      act(() => {
        (socket.onclose as unknown as (e: { code: number }) => void)({
          code: 4503,
        });
      });
      expect(result.current.state).toBe("connecting");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(600);
      });
      expect(createdSockets).toHaveLength(2);
      expect(createdSockets[1].url).toContain("session_id=keep-me");
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops the microphone when the socket drops", async () => {
    const stop = vi.fn();
    (
      navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>
    ).mockResolvedValue({ getTracks: () => [{ stop }] });
    const { socket } = await open();
    act(() => {
      (socket.onclose as unknown as (e: { code: number }) => void)({
        code: 1006,
      });
    });
    expect(stop).toHaveBeenCalled();
  });

  it("cleans up resources and disconnects upon stopSession", async () => {
    const { result, socket } = await open();
    act(() => {
      result.current.stopSession();
    });
    expect(socket.readyState).toBe(MockWebSocket.CLOSED);
    expect(result.current.isConnected).toBe(false);
    expect(result.current.isListening).toBe(false);
  });

  describe("muting the user's own microphone", () => {
    async function openMuteable() {
      const track = { stop: vi.fn(), enabled: true };
      (
        navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>
      ).mockResolvedValue({ getTracks: () => [track] });
      const nodes: MockAudioWorkletNode[] = [];
      class CapturingNode extends MockAudioWorkletNode {
        constructor() {
          super();
          nodes.push(this);
        }
      }
      (
        globalThis as unknown as { AudioWorkletNode: unknown }
      ).AudioWorkletNode = CapturingNode;
      (window as unknown as { AudioWorkletNode: unknown }).AudioWorkletNode =
        CapturingNode;
      const opened = await open();
      const emit = (data: unknown) =>
        nodes[0].port.onmessage?.({ data } as MessageEvent);
      return { ...opened, track, emit };
    }

    it("starts unmuted and sends microphone audio", async () => {
      const { result, socket, emit } = await openMuteable();
      expect(result.current.muted).toBe(false);
      const before = socket.sentMessages.length;
      emit(new ArrayBuffer(8));
      expect(socket.sentMessages.length).toBe(before + 1);
    });

    it("muting turns the track off and sends no audio or speech events, then unmuting resumes", async () => {
      const { result, socket, track, emit } = await openMuteable();
      act(() => result.current.toggleMute?.());
      expect(result.current.muted).toBe(true);
      expect(track.enabled).toBe(false);

      const muted = socket.sentMessages.length;
      emit(new ArrayBuffer(8));
      emit({ type: "VAD_SPEECH_START" });
      emit({ type: "VAD_SPEECH_STOP" });
      expect(socket.sentMessages.length).toBe(muted);

      act(() => result.current.toggleMute?.());
      expect(result.current.muted).toBe(false);
      expect(track.enabled).toBe(true);
      emit(new ArrayBuffer(8));
      expect(socket.sentMessages.length).toBe(muted + 1);
    });

    it("muting in the middle of a sentence ends that turn for the server", async () => {
      const { result, socket, emit } = await openMuteable();
      emit({ type: "VAD_SPEECH_START" });
      act(() => result.current.toggleMute?.());
      const last = JSON.parse(
        socket.sentMessages[socket.sentMessages.length - 1] as string,
      );
      expect(last).toEqual({ type: "VAD_SPEECH_STOP" });
    });

    it("keeps the session open while muted, and the user ending it clears the mute", async () => {
      const { result, socket } = await openMuteable();
      act(() => result.current.toggleMute?.());
      expect(socket.readyState).toBe(MockWebSocket.OPEN);
      act(() => {
        result.current.stopSession();
      });
      expect(result.current.muted).toBe(false);
    });

    it("does nothing while no session exists", () => {
      const { result } = renderHook(() => useLiveVoice());
      act(() => result.current.toggleMute?.());
      expect(result.current.muted).toBe(false);
    });
  });

  describe("master scope (top-bar voice)", () => {
    beforeEach(() => {
      window.localStorage.removeItem(MASTER_MUTE_STORAGE_KEY);
    });

    async function openScoped(scope: "master" | "conversation") {
      const hook = renderHook(() => useLiveVoice({ sessionId: "sess-1" }));
      await act(async () => {
        await hook.result.current.startSession({ scope });
      });
      await act(async () => {
        await Promise.resolve();
      });
      return { ...hook, socket: createdSockets[createdSockets.length - 1] };
    }

    it("asks for the master scope in HELLO, and the default HELLO has none", async () => {
      const master = await openScoped("master");
      expect(JSON.parse(master.socket.sentMessages[0] as string)).toMatchObject(
        { type: "HELLO", scope: "master" },
      );
      const conversation = await openScoped("conversation");
      expect(
        JSON.parse(conversation.socket.sentMessages[0] as string),
      ).not.toHaveProperty("scope");
    });

    it("remembers the master mute on this device and starts muted next time", async () => {
      const first = await openScoped("master");
      act(() => first.result.current.toggleMute?.());
      expect(window.localStorage.getItem(MASTER_MUTE_STORAGE_KEY)).toBe("1");
      act(() => first.result.current.stopSession());
      expect(first.result.current.muted).toBe(false);

      const second = await openScoped("master");
      expect(second.result.current.muted).toBe(true);
      act(() => second.result.current.toggleMute?.());
      expect(window.localStorage.getItem(MASTER_MUTE_STORAGE_KEY)).toBe("0");
    });

    it("a conversation call ignores the remembered master mute and does not write it", async () => {
      window.localStorage.setItem(MASTER_MUTE_STORAGE_KEY, "1");
      const hook = await openScoped("conversation");
      expect(hook.result.current.muted).toBe(false);
      act(() => hook.result.current.toggleMute?.());
      expect(window.localStorage.getItem(MASTER_MUTE_STORAGE_KEY)).toBe("1");
    });

    it("works when storage throws", async () => {
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("blocked");
      });
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new Error("blocked");
      });
      const hook = await openScoped("master");
      expect(hook.result.current.muted).toBe(false);
      act(() => hook.result.current.toggleMute?.());
      expect(hook.result.current.muted).toBe(true);
    });
  });

  it("listen-only start never requests the microphone and starts muted", async () => {
    const getUserMedia = navigator.mediaDevices.getUserMedia as ReturnType<
      typeof vi.fn
    >;
    getUserMedia.mockClear();
    const { result } = renderHook(() => useLiveVoice());
    await act(async () => {
      await result.current.startSession({ scope: "master", listenOnly: true });
    });
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(result.current.muted).toBe(true);
    // There is no microphone to open: toggling does not unmute.
    act(() => result.current.toggleMute?.());
    expect(result.current.muted).toBe(true);
  });

  it("a normal master start still opens the microphone", async () => {
    const getUserMedia = navigator.mediaDevices.getUserMedia as ReturnType<
      typeof vi.fn
    >;
    getUserMedia.mockClear();
    const { result } = renderHook(() => useLiveVoice());
    await act(async () => {
      await result.current.startSession({ scope: "master", muted: false });
    });
    expect(getUserMedia).toHaveBeenCalled();
    expect(result.current.muted).toBe(false);
  });
});
