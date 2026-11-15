import { act, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const chat = {
  messages: [] as unknown[],
  status: "idle",
  sessionId: null as string | null,
  error: null,
  retrying: null,
  isBusy: false,
  canRetry: false,
  canAskAgain: false,
  send: vi.fn(async () => undefined),
  retry: vi.fn(),
  askAgain: vi.fn(),
  stop: vi.fn(),
  dismissError: vi.fn(() => null),
  reset: vi.fn(),
  openSession: vi.fn(),
  adoptSession: vi.fn(),
};
const voice = {
  state: "idle" as string,
  readySessionId: null as string | null,
  unavailableReason: null as string | null,
  transcripts: [] as unknown[],
  activeTask: null,
  activeAction: null,
  error: null,
  startSession: vi.fn<(options?: unknown) => Promise<void>>(
    async () => undefined,
  ),
  stopSession: vi.fn(),
  interruptPlayback: vi.fn(),
  sendText: vi.fn(),
};
const hookOptions = vi.hoisted(() => ({
  current: null as null | {
    onTaskCreated?: (t: {
      taskId: string;
      title: string;
      status: string;
      promoted?: boolean;
    }) => void;
  },
}));
vi.mock("../../hooks/useChatStream", () => ({
  useChatStream: (options: typeof hookOptions.current) => {
    hookOptions.current = options;
    return chat;
  },
}));
vi.mock("../../hooks/useLiveVoice", () => ({ useLiveVoice: () => voice }));
const listMessages = vi.hoisted(() =>
  vi.fn<
    (...args: unknown[]) => Promise<{
      items: unknown[];
      next_cursor: string | null;
    }>
  >(async () => ({ items: [], next_cursor: null })),
);
vi.mock("../../lib/api-client", () => ({
  createIdempotencyKey: () => "key-1",
  listSessions: async () => ({ items: [], next_cursor: null }),
  listMessages: (...args: unknown[]) => listMessages(...args),
  deleteSession: async () => undefined,
}));
vi.mock("../../lib/approvals-client", () => ({
  listApprovals: async () => [],
}));
const submitTask = vi.fn(async (...args: unknown[]) => ({
  task_id: String(args.length && "task-9"),
}));
const getTask = vi.hoisted(() => vi.fn());
vi.mock("../../lib/tasks-client", () => ({
  getTask: (id: string) => getTask(id),
  submitTask: (request: unknown) => submitTask(request),
}));
interface Registered {
  conv: { title: string };
  voice: { statusLabel: string | null; chip: unknown };
}
const registered: Registered[] = [];
vi.mock("../layout/WorkspaceProvider", () => ({
  useWorkspace: () => ({
    auth: { isAuthenticated: true },
    refreshPendingApprovals: async () => undefined,
  }),
  useRegisterWorkspaceExtensions: (conv: unknown, v: unknown) => {
    registered.push({ conv, voice: v } as Registered);
  },
}));
vi.mock("./ConversationSidebar", () => ({ ConversationSidebar: () => <i /> }));
vi.mock("../voice/VoiceChip", () => ({ VoiceChip: () => <i /> }));

import {
  ConversationRuntime,
  VOICE_DIFFERENT_CONVERSATION_NOTICE,
} from "./ConversationRuntime";
import { useConversationRuntime } from "./ConversationContext";
import type { ConversationRuntimeValue } from "./ConversationContext";

let latest: ConversationRuntimeValue;
function Probe({ name }: { name: string }) {
  latest = useConversationRuntime();
  return <p>{name}</p>;
}
function tree(name: string) {
  return (
    <ConversationRuntime>
      <Probe name={name} />
    </ConversationRuntime>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  registered.length = 0;
  chat.messages = [];
  chat.sessionId = null;
  voice.state = "idle";
  voice.readySessionId = null;
  voice.unavailableReason = null;
  voice.transcripts = [];
});

describe("ConversationRuntime", () => {
  it("adds one task card and selects the task on task.created, deduped on replay", () => {
    render(tree("x"));
    act(() =>
      hookOptions.current?.onTaskCreated?.({
        taskId: "t-1",
        title: "Build report",
        status: "executing",
      }),
    );
    act(() =>
      hookOptions.current?.onTaskCreated?.({
        taskId: "t-1",
        title: "Build report",
        status: "executing",
      }),
    );
    const cards = latest.items.filter((i) => i.kind === "taskCard");
    expect(cards).toHaveLength(1);
    expect(cards[0]).toMatchObject({ taskId: "t-1", title: "Build report" });
  });

  it("adds the promoted notice and the task card when the reply moved to the background", () => {
    render(tree("x"));
    act(() =>
      hookOptions.current?.onTaskCreated?.({
        taskId: "t-2",
        title: "Long one",
        status: "executing",
        promoted: true,
      }),
    );
    const notes = latest.items.filter((i) => i.kind === "systemNote");
    expect(notes.map((n) => n.text)).toContain(
      "This is taking longer, so it moved to the background",
    );
    expect(latest.items.filter((i) => i.kind === "taskCard")).toHaveLength(1);
  });

  it("rebuilds task cards from message metadata when a conversation is opened", async () => {
    listMessages.mockResolvedValueOnce({
      items: [
        {
          id: "m1",
          role: "assistant",
          content: "ok",
          created_at: "2026-11-08T10:00:00Z",
          metadata: { task: { task_id: "t-a", title: "A" } },
        },
        {
          id: "m2",
          role: "assistant",
          content: "ok",
          created_at: "2026-11-08T10:01:00Z",
          metadata: { task: { task_id: "t-gone", title: "B" } },
        },
      ],
      next_cursor: null,
    });
    getTask.mockImplementation(async (id: string) => {
      if (id === "t-gone") throw new Error("404");
      return { id, title: "A real", created_at: "2026-11-08T10:00:30Z" };
    });
    render(tree("x"));
    await act(() => latest.openConversation("s-1"));
    await waitFor(() =>
      expect(latest.items.filter((i) => i.kind === "taskCard")).toHaveLength(1),
    );
    expect(latest.items.find((i) => i.kind === "taskCard")).toMatchObject({
      taskId: "t-a",
      title: "A real",
    });
  });

  it("opens a conversation with pages the API accepts and follows the cursor", async () => {
    // The API rejects a page larger than 100 messages with a 422 (shown as "could not be loaded").
    const message = (id: string, content: string) => ({
      id,
      role: "user",
      content,
      created_at: "2026-11-08T10:00:00Z",
    });
    listMessages
      .mockResolvedValueOnce({
        items: [message("m1", "first")],
        next_cursor: "c2",
      })
      .mockResolvedValueOnce({
        items: [message("m2", "second")],
        next_cursor: null,
      });
    render(tree("x"));
    await act(() => latest.openConversation("s-1"));

    expect(listMessages).toHaveBeenCalledTimes(2);
    const [, firstOptions] = listMessages.mock.calls[0] as [
      string,
      { limit: number },
    ];
    expect(firstOptions.limit).toBeLessThanOrEqual(100);
    expect(listMessages.mock.calls[1]?.[1]).toMatchObject({ cursor: "c2" });
    expect(latest.historyError).toBeNull();
    expect(chat.openSession).toHaveBeenCalledTimes(1);
    const [sessionId, opened] = chat.openSession.mock.calls[0] as [
      string,
      { id: string }[],
    ];
    expect(sessionId).toBe("s-1");
    expect(opened.map((m) => m.id)).toEqual(["m1", "m2"]);
  });

  it("ignores slower earlier conversation loads so they do not overwrite the last selected conversation", async () => {
    let resolveFirst!: (value: {
      items: unknown[];
      next_cursor: string | null;
    }) => void;
    const firstPromise = new Promise<{
      items: unknown[];
      next_cursor: string | null;
    }>((res) => {
      resolveFirst = res;
    });

    listMessages.mockImplementation((id: unknown) => {
      if (id === "s-slow") return firstPromise;
      return Promise.resolve({
        items: [
          {
            id: "m-fast",
            role: "user",
            content: "fast content",
            created_at: "2026-11-08T10:00:00Z",
          },
        ],
        next_cursor: null,
      });
    });

    render(tree("x"));

    // User selects s-slow first (slow network)
    let slowOpenPromise!: Promise<void>;
    act(() => {
      slowOpenPromise = latest.openConversation("s-slow");
    });

    // User quickly selects s-fast next
    await act(async () => {
      await latest.openConversation("s-fast");
    });

    // s-fast has completed and opened
    expect(chat.openSession).toHaveBeenCalledTimes(1);
    expect(chat.openSession).toHaveBeenCalledWith("s-fast", expect.anything());

    // Now the slow load for s-slow finally resolves
    await act(async () => {
      resolveFirst({
        items: [
          {
            id: "m-slow",
            role: "user",
            content: "slow content",
            created_at: "2026-11-08T09:00:00Z",
          },
        ],
        next_cursor: null,
      });
      await slowOpenPromise;
    });

    // chat.openSession must NOT have been called with s-slow
    expect(chat.openSession).toHaveBeenCalledTimes(1);
  });

  it("keeps the same voice session and thread when the route children change", () => {
    voice.state = "listening";
    voice.transcripts = [
      { id: "t1", role: "user", text: "hi", isFinal: false, timestamp: 1 },
    ];
    const { rerender } = render(tree("conversation"));
    expect(latest.items).toHaveLength(1);
    rerender(tree("tasks"));
    expect(screen.getByText("tasks")).toBeTruthy();
    expect(latest.items).toHaveLength(1);
    expect(voice.stopSession).not.toHaveBeenCalled();
    expect(chat.reset).not.toHaveBeenCalled();
  });

  it("adopts the session id reported by READY", async () => {
    voice.readySessionId = "s-new";
    render(tree("x"));
    await waitFor(() =>
      expect(chat.adoptSession).toHaveBeenCalledWith("s-new"),
    );
  });

  it("sends over the voice socket while live and over REST otherwise", async () => {
    voice.state = "listening";
    const { rerender } = render(tree("x"));
    await act(() => latest.sendText("typed while live"));
    expect(voice.sendText).toHaveBeenCalledWith("typed while live");
    expect(chat.send).not.toHaveBeenCalled();

    voice.state = "idle";
    rerender(tree("x"));
    await act(() => latest.sendText("typed"));
    expect(chat.send).toHaveBeenCalledWith("typed");
  });

  it("marks a user turn that was typed while live as typed, not spoken", async () => {
    voice.state = "listening";
    const { rerender } = render(tree("x"));
    await act(() => latest.sendText("typed while live"));
    voice.transcripts = [
      {
        id: "t1",
        role: "user",
        text: "typed while live",
        isFinal: true,
        timestamp: 1,
      },
    ];
    rerender(tree("x"));
    const turn = latest.items.find((i) => i.kind === "voiceTurn");
    expect(turn).toMatchObject({ source: "typed" });
  });

  it("runs typed text as a task with an idempotency key and adds a task card", async () => {
    chat.sessionId = "s1";
    render(tree("x"));
    await act(() => latest.runAsTask("do the thing"));
    expect(submitTask).toHaveBeenCalledWith({
      instruction: "do the thing",
      idempotency_key: "key-1",
      session_id: "s1",
    });
    expect(
      latest.items.some((i) => i.kind === "taskCard" && i.taskId === "task-9"),
    ).toBe(true);
  });

  it("shows the unavailable reason as a system note and keeps text chat", async () => {
    voice.state = "unavailable";
    voice.unavailableReason = "no_api_key";
    render(tree("x"));
    await waitFor(() =>
      expect(
        latest.items.some(
          (i) => i.kind === "systemNote" && i.text.includes("no_api_key"),
        ),
      ).toBe(true),
    );
    await act(() => latest.sendText("still works"));
    expect(chat.send).toHaveBeenCalledWith("still works");
  });

  it("registers the voice chip and status label, and no side panel", () => {
    voice.state = "listening";
    render(tree("x"));
    const first = registered[registered.length - 1];
    expect(first.conv).not.toHaveProperty("rightPanel");
    expect(first.voice.statusLabel).toBe("Voice: Listening");
    expect(first.voice.chip).not.toBeNull();
  });

  it("trackTask adds one task card and never doubles it", () => {
    render(tree("x"));
    act(() => latest.trackTask("t-9", "Again"));
    act(() => latest.trackTask("t-9", "Again"));
    const cards = latest.items.filter((i) => i.kind === "taskCard");
    expect(cards).toHaveLength(1);
    expect(cards[0]).toMatchObject({ taskId: "t-9", title: "Again" });
  });

  // Deliberate change (master voice): "New conversation" ends a CONVERSATION-bound call only, and
  // only when one is live. It used to stop voice unconditionally; the master voice survives it.
  it("new conversation resets chat and ends a live conversation-bound call", () => {
    const view = render(tree("x"));
    act(() => latest.toggleVoice());
    voice.state = "listening";
    voice.readySessionId = "s1";
    chat.sessionId = "s1";
    view.rerender(tree("x"));
    act(() => latest.newConversation());
    expect(voice.stopSession).toHaveBeenCalled();
    expect(chat.reset).toHaveBeenCalled();
  });
});

describe("voice scopes (master tracker vs conversation-bound)", () => {
  function goLive(view: ReturnType<typeof render>, id: string) {
    voice.state = "listening";
    voice.readySessionId = id;
    chat.sessionId = id;
    view.rerender(tree("x"));
  }

  it("a route change (children swapped) neither stops nor restarts the session", () => {
    const view = render(tree("tasks-page"));
    act(() => latest.toggleMasterVoice?.());
    goLive(view, "s1");
    voice.startSession.mockClear();
    view.rerender(tree("artifacts-page"));
    view.rerender(tree("chat-page"));
    expect(voice.stopSession).not.toHaveBeenCalled();
    expect(voice.startSession).not.toHaveBeenCalled();
    expect(latest.voiceScope).toBe("master");
  });

  it("the composer mic starts the conversation-bound scope", () => {
    render(tree("x"));
    act(() => latest.toggleVoice());
    expect(voice.startSession).toHaveBeenCalledWith({ scope: "conversation" });
    expect(latest.voiceScope).toBe("conversation");
  });

  it("the top bar starts the master scope and labels the status", () => {
    const view = render(tree("x"));
    act(() => latest.toggleMasterVoice?.());
    expect(voice.startSession).toHaveBeenCalledWith({ scope: "master" });
    goLive(view, "s1");
    const last = registered[registered.length - 1];
    expect(last.voice.statusLabel).toBe("Voice (all conversations): Listening");
  });

  it("conversation voice survives returning to the SAME conversation", async () => {
    const view = render(tree("x"));
    act(() => latest.toggleVoice());
    goLive(view, "s1");
    await act(() => latest.openConversation("s1"));
    expect(voice.stopSession).not.toHaveBeenCalled();
    expect(listMessages).not.toHaveBeenCalled();
  });

  it("conversation voice stops, with a notice, when a DIFFERENT conversation opens", async () => {
    const view = render(tree("x"));
    act(() => latest.toggleVoice());
    goLive(view, "s1");
    await act(() => latest.openConversation("s2"));
    expect(voice.stopSession).toHaveBeenCalledTimes(1);
    expect(
      latest.items.some(
        (i) =>
          i.kind === "systemNote" &&
          i.text === VOICE_DIFFERENT_CONVERSATION_NOTICE,
      ),
    ).toBe(true);
  });

  it("master voice is NOT stopped by opening another conversation or starting a new one", async () => {
    const view = render(tree("x"));
    act(() => latest.toggleMasterVoice?.());
    goLive(view, "s1");
    await act(() => latest.openConversation("s2"));
    act(() => latest.newConversation());
    expect(voice.stopSession).not.toHaveBeenCalled();
    expect(latest.voiceScope).toBe("master");
  });

  it("master wins: the composer mic neither stops it nor starts a second session", () => {
    const view = render(tree("x"));
    act(() => latest.toggleMasterVoice?.());
    goLive(view, "s1");
    voice.startSession.mockClear();
    act(() => latest.toggleVoice());
    expect(voice.stopSession).not.toHaveBeenCalled();
    expect(voice.startSession).not.toHaveBeenCalled();
  });

  it("starting master over a conversation call replaces it; the chip toggles it off", () => {
    const view = render(tree("x"));
    act(() => latest.toggleVoice());
    goLive(view, "s1");
    act(() => latest.toggleMasterVoice?.());
    expect(voice.stopSession).toHaveBeenCalledTimes(1);
    expect(voice.startSession).toHaveBeenLastCalledWith({ scope: "master" });
    voice.stopSession.mockClear();
    act(() => latest.toggleMasterVoice?.());
    expect(voice.stopSession).toHaveBeenCalledTimes(1);
    expect(latest.voiceScope).toBeNull();
  });

  it("the dock stop ends the session whichever scope started it", () => {
    const view = render(tree("x"));
    act(() => latest.toggleMasterVoice?.());
    goLive(view, "s1");
    act(() => latest.stopVoice?.());
    expect(voice.stopSession).toHaveBeenCalledTimes(1);
  });

  it("typed text goes over chat when master is tracking from a different conversation", async () => {
    const view = render(tree("x"));
    act(() => latest.toggleMasterVoice?.());
    goLive(view, "s1");
    await act(() => latest.sendText("here"));
    expect(voice.sendText).toHaveBeenCalledWith("here");
    chat.sessionId = "s2";
    view.rerender(tree("x"));
    await act(() => latest.sendText("elsewhere"));
    expect(chat.send).toHaveBeenCalledWith("elsewhere");
  });
});
