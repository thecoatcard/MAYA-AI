import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useLiveVoice } from "./useLiveVoice";
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
  onclose: (() => void) | null = null;

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
    this.onclose?.();
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

    vi.spyOn(authSession, "getAccessToken").mockReturnValue("test-voice-token");

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

  it("connects to WebSocket and initializes media upon startSession", async () => {
    const { result } = renderHook(() => useLiveVoice());

    await act(async () => {
      await result.current.startSession();
    });

    expect(createdSockets).toHaveLength(1);
    expect(createdSockets[0].url).toContain(
      "/api/v1/voice/live?token=test-voice-token",
    );

    act(() => {
      createdSockets[0].onopen?.();
    });
    expect(result.current.isConnected).toBe(true);
    expect(result.current.isInterrupted).toBe(false);
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

  it("handles incoming user and model transcripts", async () => {
    const { result } = renderHook(() => useLiveVoice());

    await act(async () => {
      await result.current.startSession();
    });

    const socket = createdSockets[0];

    act(() => {
      socket.onmessage?.({
        data: JSON.stringify({
          type: "TRANSCRIPT_USER",
          text: "What is my agenda?",
        }),
      });
      socket.onmessage?.({
        data: JSON.stringify({
          type: "TRANSCRIPT_MODEL",
          text: "Checking your calendar...",
        }),
      });
    });

    expect(result.current.transcripts).toHaveLength(2);
    expect(result.current.transcripts[0].sender).toBe("user");
    expect(result.current.transcripts[0].text).toBe("What is my agenda?");
    expect(result.current.transcripts[1].sender).toBe("model");
    expect(result.current.transcripts[1].text).toBe(
      "Checking your calendar...",
    );
  });

  it("triggers barge-in interruption and flushes state on AUDIO_INTERRUPT", async () => {
    const onInterruptMock = vi.fn();
    const { result } = renderHook(() =>
      useLiveVoice({ onInterrupt: onInterruptMock }),
    );

    await act(async () => {
      await result.current.startSession();
    });

    const socket = createdSockets[0];

    act(() => {
      socket.onmessage?.({
        data: JSON.stringify({ type: "AUDIO_INTERRUPT" }),
      });
    });

    expect(result.current.isSpeaking).toBe(false);
    expect(result.current.isInterrupted).toBe(true);
    expect(onInterruptMock).toHaveBeenCalled();
  });

  it("sends CLIENT_INTERRUPT message when interruptPlayback is called", async () => {
    const { result } = renderHook(() => useLiveVoice());

    await act(async () => {
      await result.current.startSession();
    });

    const socket = createdSockets[0];

    act(() => {
      result.current.interruptPlayback();
    });

    expect(socket.sentMessages).toContain(
      JSON.stringify({ type: "CLIENT_INTERRUPT" }),
    );
    expect(result.current.isInterrupted).toBe(true);
  });

  it("cleans up resources and disconnects upon stopSession", async () => {
    const { result } = renderHook(() => useLiveVoice());

    await act(async () => {
      await result.current.startSession();
    });

    const socket = createdSockets[0];

    act(() => {
      result.current.stopSession();
    });

    expect(socket.readyState).toBe(MockWebSocket.CLOSED);
    expect(result.current.isConnected).toBe(false);
    expect(result.current.isListening).toBe(false);
  });
});
