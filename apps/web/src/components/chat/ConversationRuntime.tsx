"use client";

import { tidyTranscripts } from "../../lib/voice-task-updates";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { useAttachments } from "../../hooks/useAttachments";
import { useChatStream } from "../../hooks/useChatStream";
import {
  useLiveVoice,
  type LiveVoiceState,
  type VoiceScope,
} from "../../hooks/useLiveVoice";
import {
  createIdempotencyKey,
  deleteSession,
  listMessages,
  listSessions,
  updateSession,
  duplicateSession,
  exportSessionMarkdown,
  sendMessageFeedback,
  isUnsupported,
  type SessionSummary,
  type Page,
  type StoredMessage,
} from "../../lib/api-client";
import { getTask, submitTask } from "../../lib/tasks-client";
import { RESULT_POLL_DELAYS_MS, resultsForTask } from "../../lib/task-result";
import {
  useRegisterWorkspaceExtensions,
  useWorkspace,
  type ConversationExtension,
  type VoiceExtension,
} from "../layout/WorkspaceProvider";
import { VoiceChip } from "../voice/VoiceChip";
import { VOICE_START_NOTICE } from "../voice/voice-copy";
import { describeVoiceUnavailable } from "../../lib/voice-reasons";
import { ORB_LABELS } from "../voice/VoiceOrb";
import {
  ConversationContext,
  ConversationRefContext,
  type ComposerHandle,
  type ConversationActions,
  type ConversationRuntimeValue,
} from "./ConversationContext";
import { downloadText, safeFileName } from "../../lib/menu-actions";
import { ConversationSidebar } from "./ConversationSidebar";
import {
  buildThreadItems,
  collectTaskRefs,
  linkPersistedTurns,
  type SystemNoteInput,
  type TaskRef,
  type TimedChatMessage,
  type VoiceTurnInput,
} from "./threadItems";

const LIVE_STATES: readonly LiveVoiceState[] = [
  "listening",
  "thinking",
  "speaking",
  "interrupted",
];
/** The API's maximum page size for messages (a larger `limit` is rejected with a 422). */
const MESSAGES_PAGE_LIMIT = 100;
const MAX_HISTORY_PAGES = 20;
const PERSIST_LINK_DELAY_MS = 800;
export const VOICE_DIFFERENT_CONVERSATION_NOTICE =
  "Voice ended because you opened a different conversation.";
export const PROMOTED_TASK_NOTICE =
  "This is taking longer, so it moved to the background";

/**
 * Owns the ONE chat stream and the ONE live voice session. It is mounted above
 * the routes (inside WorkspaceProvider), so moving to Tasks never
 * ends a call or empties the thread. It registers the sidebar list, title
 * and voice chip with the shell.
 *
 * Two voice scopes share that one session (never two audio sessions):
 *  - "master": started from the top bar. App-level progress tracker: it survives opening other
 *    conversations, New conversation, and every route change; only the user (chip, dock Stop)
 *    ends it. Typed text in a conversation goes over normal chat so it lands in THAT
 *    conversation. MASTER WINS: while it is on, the composer mic cannot start or stop a second
 *    session, and starting master while a conversation call is live replaces that call.
 *  - "conversation": started from the composer mic. Bound to the conversation it started in:
 *    it survives navigation away and back to that same conversation, and ends (with a short
 *    notice) when a DIFFERENT conversation is opened or a new one is started.
 */
export function ConversationRuntime({
  children,
}: {
  readonly children: React.ReactNode;
}): React.JSX.Element {
  const { auth, refreshPendingApprovals } = useWorkspace();
  const authed = auth.isAuthenticated;
  const onTaskCreatedRef = useRef<
    (task: { taskId: string; title: string; promoted: boolean }) => void
  >(() => undefined);
  const chat = useChatStream({
    onTaskCreated: (task) => onTaskCreatedRef.current(task),
  });
  const voice = useLiveVoice({ sessionId: chat.sessionId });
  const attachments = useAttachments();
  const takeAttachments = attachments.take;

  const [sessions, setSessions] = useState<readonly SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [unsupported, setUnsupported] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const composerRef = useRef<ComposerHandle | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [tasks, setTasks] = useState<readonly TaskRef[]>([]);
  const [notes, setNotes] = useState<readonly SystemNoteInput[]>([]);
  const [persisted, setPersisted] = useState<Readonly<Record<string, string>>>(
    {},
  );

  const timesRef = useRef(new Map<string, number>());
  const typedLiveRef = useRef<string[]>([]);
  const noteSeq = useRef(0);

  const state = voice.state;
  const voiceLive = LIVE_STATES.includes(state);
  const [voiceScope, setVoiceScope] = useState<VoiceScope | null>(null);
  const voiceScopeRef = useRef<VoiceScope | null>(null);
  // The conversation the voice turns belong to. Kept after the session ends so those turns do not
  // appear in a different conversation that is opened later.
  const [voiceTurnsConv, setVoiceTurnsConv] = useState<string | null>(null);
  const liveConvRef = useRef<string | null>(null);

  const addNote = useCallback((tone: "info" | "error", text: string) => {
    noteSeq.current += 1;
    const id = String(noteSeq.current);
    setNotes((prev) => [...prev, { id, createdAt: Date.now(), tone, text }]);
  }, []);

  // ---- sessions list ------------------------------------------------------
  const reloadSessions = useCallback(async (): Promise<void> => {
    setSessionsLoading(true);
    setSessionsError(null);
    try {
      const page = await listSessions({
        limit: 50,
        ...(showArchived ? { archived: "all" as const } : {}),
      });
      setSessions(page.items);
    } catch {
      setSessionsError("Conversation history could not be loaded.");
    } finally {
      setSessionsLoading(false);
      setSessionsLoaded(true);
    }
  }, [showArchived]);

  useEffect(() => {
    if (authed) void reloadSessions();
    else {
      setSessions([]);
      setSessionsLoaded(false);
    }
  }, [authed, reloadSessions]);

  // The list is refreshed when a reply finishes: the first send creates the conversation and the
  // server names it, so the left rail must not keep saying "No conversations yet".
  const chatStatus = chat.status;
  const prevStatusRef = useRef(chatStatus);
  useEffect(() => {
    const was = prevStatusRef.current;
    prevStatusRef.current = chatStatus;
    if (authed && was !== "idle" && chatStatus === "idle") {
      void reloadSessions();
    }
  }, [chatStatus, authed, reloadSessions]);

  // A session created by the first send shows up in the list.
  const sessionId = chat.sessionId;
  useEffect(() => {
    if (
      authed &&
      sessionId !== null &&
      !sessions.some((s) => s.id === sessionId)
    ) {
      void reloadSessions();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, authed]);

  // ---- voice <-> chat session adoption -----------------------------------
  const readyId = voice.readySessionId;
  const adopt = chat.adoptSession;
  useEffect(() => {
    if (readyId !== null) {
      liveConvRef.current = readyId;
      setVoiceTurnsConv(readyId);
      if (readyId !== sessionId) adopt(readyId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readyId]);

  // ---- notes from voice ---------------------------------------------------
  useEffect(() => {
    // One-time notice (no permanent panel): voice cannot approve anything.
    if (readyId !== null) addNote("info", VOICE_START_NOTICE);
  }, [readyId, addNote]);
  const unavailable = voice.unavailableReason;
  useEffect(() => {
    if (unavailable !== null) {
      addNote("error", describeVoiceUnavailable(unavailable));
    }
  }, [unavailable, addNote]);
  const voiceError = voice.error;
  useEffect(() => {
    if (voiceError !== null) addNote("error", voiceError.message);
  }, [voiceError, addNote]);

  // ---- tasks from voice ---------------------------------------------------
  const addTask = useCallback((taskId: string, title?: string) => {
    setTasks((prev) =>
      prev.some((t) => t.taskId === taskId)
        ? prev
        : [...prev, { taskId, title, createdAt: Date.now() }],
    );
  }, []);
  const voiceTaskId = voice.activeAction?.taskId ?? voice.activeTask?.taskId;
  const voiceTaskTitle = voice.activeAction?.intent ?? voice.activeTask?.intent;
  useEffect(() => {
    if (voiceTaskId) addTask(voiceTaskId, voiceTaskTitle);
  }, [voiceTaskId, voiceTaskTitle, addTask]);

  // A task the model (or the optional classifier) started from chat: add the
  // card (deduped by task id, so a stream replay never doubles it). Its progress, approvals and
  // files all show in the card, inside the thread.
  useEffect(() => {
    onTaskCreatedRef.current = (task) => {
      addTask(task.taskId, task.title || undefined);
      if (task.promoted) {
        // The answer went over the inline budget and continues as a background task.
        addNote("info", PROMOTED_TASK_NOTICE);
      }
    };
  }, [addTask, addNote]);

  // ---- persisted link for voice turns (legacy fallback; the hook now exposes ids)
  const finalUnlinked = voice.transcripts.filter(
    (t) => t.isFinal && !t.messageId && persisted[t.id] === undefined,
  ).length;
  useEffect(() => {
    if (finalUnlinked === 0 || sessionId === null || !authed) return undefined;
    const timer = setTimeout(() => {
      void listMessages(sessionId, { limit: 100 })
        .then((page) => {
          const turns: VoiceTurnInput[] = voice.transcripts.map((t) => ({
            id: t.id,
            role: t.role,
            text: t.text,
            isFinal: t.isFinal,
            timestamp: t.timestamp,
            messageId: t.messageId ?? persisted[t.id] ?? null,
          }));
          const links = linkPersistedTurns(turns, page.items);
          if (Object.keys(links).length > 0) {
            setPersisted((prev) => ({ ...prev, ...links }));
          }
        })
        .catch(() => undefined);
    }, PERSIST_LINK_DELAY_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finalUnlinked, sessionId, authed]);

  // ---- the thread ---------------------------------------------------------
  const timedMessages = useMemo<TimedChatMessage[]>(
    () =>
      chat.messages.map((m) => {
        let at = timesRef.current.get(m.key);
        if (at === undefined) {
          at = Date.now();
          timesRef.current.set(m.key, at);
        }
        return { ...m, createdAt: at };
      }),
    [chat.messages],
  );

  const items = useMemo(() => {
    // Stray fragments ("de") and repeated identical assistant lines never reach the thread.
    // Voice turns belong to the conversation the session started in.
    const shown =
      voiceTurnsConv === null || voiceTurnsConv === sessionId
        ? voice.transcripts
        : [];
    const turns: VoiceTurnInput[] = tidyTranscripts(shown).map((t) => ({
      id: t.id,
      role: t.role,
      text: t.text,
      isFinal: t.isFinal,
      timestamp: t.timestamp,
      messageId: t.messageId ?? persisted[t.id] ?? null,
      markdown: t.markdown === true,
      source:
        t.role === "user" && typedLiveRef.current.includes(t.text.trim())
          ? "typed"
          : "spoken",
    }));
    return buildThreadItems(
      timedMessages,
      turns,
      tasks,
      notes,
      voice.savedMemories ?? [],
    );
  }, [
    timedMessages,
    voice.transcripts,
    voice.savedMemories,
    tasks,
    notes,
    persisted,
    voiceTurnsConv,
    sessionId,
  ]);

  // ---- actions ------------------------------------------------------------
  const attachmentsClear = attachments.clear;
  const clearThreadState = useCallback(() => {
    setTasks([]);
    setNotes([]);
    setPersisted({});
    setHistoryError(null);
    attachmentsClear();
    typedLiveRef.current = [];
    timesRef.current.clear();
  }, [attachmentsClear]);

  const stopVoice = voice.stopSession;
  const startVoice = voice.startSession;
  const voiceOff =
    state === "idle" || state === "unavailable" || state === "error";
  const beginVoice = useCallback(
    (
      scope: VoiceScope,
      options?: { listenOnly?: boolean; muted?: boolean },
    ) => {
      voiceScopeRef.current = scope;
      setVoiceScope(scope);
      void startVoice({ scope, ...options });
    },
    [startVoice],
  );
  const endVoice = useCallback(() => {
    stopVoice();
    voiceScopeRef.current = null;
    setVoiceScope(null);
  }, [stopVoice]);

  /** Composer mic (conversation-bound). A running master session wins: this does nothing then. */
  const toggleVoice = useCallback(() => {
    if (voiceOff) {
      beginVoice("conversation");
    } else if (voiceScopeRef.current !== "master") {
      endVoice();
      addNote("info", "Voice ended.");
    }
  }, [voiceOff, beginVoice, endVoice, addNote]);

  /** Top-bar voice (master). Starts it, replaces a conversation call, or stops it. */
  const toggleMasterVoice = useCallback(() => {
    if (voiceOff) {
      beginVoice("master");
    } else if (voiceScopeRef.current === "master") {
      endVoice();
      addNote("info", "Voice ended.");
    } else {
      endVoice();
      beginVoice("master");
    }
  }, [voiceOff, beginVoice, endVoice, addNote]);

  /**
   * Scheduled-update voice: a master session that only listens (no microphone is requested), so
   * MAYA reads the update. Does nothing when a voice session is already running.
   */
  const startListenOnlyMaster = useCallback(() => {
    if (voiceOff) beginVoice("master", { listenOnly: true });
  }, [voiceOff, beginVoice]);

  /** From a listen-only session to a normal master session with the microphone open. */
  const unmuteMaster = useCallback(() => {
    endVoice();
    beginVoice("master", { muted: false });
  }, [endVoice, beginVoice]);

  /** Always ends the session, whichever scope started it (the dock's Stop). */
  const stopVoiceNow = useCallback(() => {
    endVoice();
    addNote("info", "Voice ended.");
  }, [endVoice, addNote]);

  /** True when opening `target` must end the call (a conversation call, different conversation). */
  const endsConversationVoice = useCallback(
    (target: string | null): boolean =>
      voiceScopeRef.current === "conversation" &&
      liveConvRef.current !== null &&
      liveConvRef.current !== target,
    [],
  );

  const sendText = useCallback(
    async (
      text: string,
      options?: { mode?: "deep_research"; sourceMessageId?: string },
    ): Promise<void> => {
      // The live socket carries typed text only for the conversation the voice turns belong to.
      // A master session that is tracking from another conversation leaves this one to chat.
      const voiceHere =
        voiceLive &&
        (liveConvRef.current === null || liveConvRef.current === sessionId);
      if (voiceHere) {
        typedLiveRef.current.push(text.trim());
        voice.sendText(text);
        return;
      }
      // Files go with this message and the chips clear; a live voice session has no attachments.
      const files = takeAttachments();
      const mode = options?.mode;
      const sourceMessageId = options?.sourceMessageId;
      if (
        files.length > 0 ||
        mode !== undefined ||
        sourceMessageId !== undefined
      ) {
        await chat.send(text, {
          ...(files.length > 0 ? { attachments: files } : {}),
          ...(mode !== undefined ? { mode } : {}),
          ...(sourceMessageId !== undefined ? { sourceMessageId } : {}),
        });
      } else await chat.send(text);
    },
    [voiceLive, sessionId, voice, chat, takeAttachments],
  );

  const runAsTask = useCallback(
    async (text: string): Promise<void> => {
      try {
        const response = await submitTask({
          instruction: text,
          idempotency_key: createIdempotencyKey(),
          session_id: sessionId ?? undefined,
        });
        addTask(response.task_id, text.slice(0, 80));
      } catch {
        addNote("error", "The task could not be created. Try again.");
      }
    },
    [sessionId, addTask, addNote],
  );

  const resetChat = chat.reset;
  const openSeqRef = useRef<number>(0);
  const newConversation = useCallback(() => {
    openSeqRef.current += 1;
    // A new conversation is a different conversation: it ends a conversation call, not master.
    const ended = endsConversationVoice(null);
    if (ended) endVoice();
    resetChat();
    clearThreadState();
    if (ended) addNote("info", VOICE_DIFFERENT_CONVERSATION_NOTICE);
    void reloadSessions();
  }, [
    endsConversationVoice,
    endVoice,
    resetChat,
    clearThreadState,
    addNote,
    reloadSessions,
  ]);

  const openSession = chat.openSession;
  const appendStored = chat.appendStored;

  // A tracked task ended: the server writes "what the task got" into this conversation a few
  // seconds later (a model call). Check for it now and then and add it live, no reload needed.
  const currentSessionRef = useRef<string | null>(null);
  currentSessionRef.current = sessionId;
  const resultTimers = useRef(new Set<ReturnType<typeof setTimeout>>());
  useEffect(() => {
    const timers = resultTimers.current;
    return () => {
      timers.forEach((timer) => clearTimeout(timer));
      timers.clear();
    };
  }, []);
  const taskFinished = useCallback(
    (taskId: string): void => {
      const forSession = currentSessionRef.current;
      if (forSession === null) return;
      const check = async (attempt: number): Promise<void> => {
        // Another conversation is open now: its history load shows the message.
        if (currentSessionRef.current !== forSession) return;
        try {
          const all: StoredMessage[] = [];
          let cursor: string | null = null;
          for (let pages = 0; pages < MAX_HISTORY_PAGES; pages += 1) {
            const page: Page<StoredMessage> = await listMessages(forSession, {
              limit: MESSAGES_PAGE_LIMIT,
              cursor,
            });
            all.push(...page.items);
            cursor = page.next_cursor;
            if (cursor === null) break;
          }
          const found = resultsForTask(all, taskId);
          if (found.length > 0) {
            appendStored(forSession, found);
            return;
          }
        } catch {
          // try again at the next delay
        }
        const delay = RESULT_POLL_DELAYS_MS[attempt];
        if (delay === undefined) return;
        const timer = setTimeout(() => {
          resultTimers.current.delete(timer);
          void check(attempt + 1);
        }, delay);
        resultTimers.current.add(timer);
      };
      const first = setTimeout(() => {
        resultTimers.current.delete(first);
        void check(0);
      }, 0);
      resultTimers.current.add(first);
    },
    [appendStored],
  );
  // Rebuild task cards from message metadata.task (real task detail only;
  // failures are ignored quietly). Capped to keep the reload cheap.
  const rehydrateTasks = useCallback(
    async (messages: readonly StoredMessage[]): Promise<void> => {
      const refs = collectTaskRefs(messages);
      const found = await Promise.all(
        refs.map((ref) =>
          getTask(ref.taskId)
            .then((task): TaskRef => ({
              taskId: task.id,
              title: task.title ?? ref.title,
              createdAt: Date.parse(task.created_at) || Date.now(),
            }))
            .catch(() => null),
        ),
      );
      const cards = found.filter((c): c is TaskRef => c !== null);
      if (cards.length === 0) return;
      setTasks((prev) => [
        ...prev,
        ...cards.filter((c) => !prev.some((t) => t.taskId === c.taskId)),
      ]);
    },
    [],
  );
  const openConversation = useCallback(
    async (id: string): Promise<void> => {
      // Same conversation as the live conversation call (for example after a visit to Tasks):
      // nothing to reload, the call keeps going.
      if (
        voiceScopeRef.current === "conversation" &&
        liveConvRef.current === id &&
        id === sessionId
      ) {
        return;
      }
      const seq = ++openSeqRef.current;
      const ended = endsConversationVoice(id);
      if (ended) endVoice();
      setHistoryLoading(true);
      clearThreadState();
      if (ended) addNote("info", VOICE_DIFFERENT_CONVERSATION_NOTICE);
      try {
        // The API caps a page at MESSAGES_PAGE_LIMIT (asking for more is a 422), oldest first;
        // follow the cursor so a long conversation is not cut off.
        const items: StoredMessage[] = [];
        let cursor: string | null = null;
        for (let pages = 0; pages < MAX_HISTORY_PAGES; pages += 1) {
          const page: Page<StoredMessage> = await listMessages(id, {
            limit: MESSAGES_PAGE_LIMIT,
            cursor,
          });
          if (openSeqRef.current !== seq) return;
          items.push(...page.items);
          cursor = page.next_cursor;
          if (cursor === null) break;
        }
        if (openSeqRef.current !== seq) return;
        for (const m of items) {
          timesRef.current.set(`history-${m.id}`, Date.parse(m.created_at));
        }
        openSession(id, items);
        void rehydrateTasks(items);
      } catch {
        if (openSeqRef.current !== seq) return;
        setHistoryError(
          "That conversation could not be loaded. Choose another or start a new one.",
        );
      } finally {
        if (openSeqRef.current === seq) {
          setHistoryLoading(false);
        }
      }
    },
    [
      sessionId,
      endsConversationVoice,
      endVoice,
      addNote,
      clearThreadState,
      openSession,
      rehydrateTasks,
    ],
  );

  const deleteConversation = useCallback(
    async (id: string, alsoDeleteContent = false): Promise<void> => {
      try {
        await deleteSession(id, { alsoDeleteContent });
        setSessions((prev) => prev.filter((s) => s.id !== id));
        if (id === sessionId) newConversation();
      } catch {
        setSessionsError("The conversation could not be deleted.");
      }
    },
    [sessionId, newConversation],
  );

  const markUnsupported = useCallback((name: string): void => {
    setUnsupported((prev) => new Set(prev).add(name));
  }, []);

  /** Runs a server action; 404 or 501 hides the action, other errors show the list error line. */
  const attempt = useCallback(
    async (name: string, run: () => Promise<void>): Promise<boolean> => {
      try {
        await run();
        return true;
      } catch (error) {
        if (isUnsupported(error)) markUnsupported(name);
        else setSessionsError("That action could not be completed.");
        return false;
      }
    },
    [markUnsupported],
  );

  const conversationActions = useMemo<ConversationActions>(
    () => ({
      unsupported,
      rename: async (id, nextTitle) => {
        const before = sessions;
        // Optimistic: the row shows the new name at once and rolls back when the server refuses.
        setSessions((prev) =>
          prev.map((s) => (s.id === id ? { ...s, title: nextTitle } : s)),
        );
        const ok = await attempt("rename", async () => {
          const saved = await updateSession(id, { title: nextTitle });
          setSessions((prev) => prev.map((s) => (s.id === id ? saved : s)));
        });
        if (!ok) setSessions(before);
        return ok;
      },
      setPinned: async (id, pinned) => {
        const before = sessions;
        setSessions((prev) =>
          prev.map((s) => (s.id === id ? { ...s, pinned } : s)),
        );
        const ok = await attempt("pin", async () => {
          const saved = await updateSession(id, { pinned });
          setSessions((prev) => prev.map((s) => (s.id === id ? saved : s)));
        });
        if (!ok) setSessions(before);
        return ok;
      },
      setArchived: async (id, archived) => {
        const before = sessions;
        setSessions((prev) =>
          prev.map((s) => (s.id === id ? { ...s, archived } : s)),
        );
        const ok = await attempt("archive", async () => {
          const saved = await updateSession(id, { archived });
          setSessions((prev) => prev.map((s) => (s.id === id ? saved : s)));
        });
        if (!ok) setSessions(before);
        return ok;
      },
      duplicate: async (id, fromMessageId) => {
        const made: { copy?: SessionSummary } = {};
        const ok = await attempt("duplicate", async () => {
          made.copy = await duplicateSession(id, fromMessageId);
        });
        if (ok && made.copy !== undefined) {
          const copy = made.copy;
          setSessions((prev) => [copy, ...prev]);
          await openConversation(copy.id);
        }
        return ok;
      },
      exportMarkdown: async (id, convTitle) => {
        return attempt("export", async () => {
          const text = await exportSessionMarkdown(id);
          downloadText(
            safeFileName(convTitle, "md"),
            text,
            "text/markdown;charset=utf-8",
          );
        });
      },
      feedback: (messageId, value) =>
        attempt("feedback", () => sendMessageFeedback(messageId, value)),
    }),
    [sessions, unsupported, attempt, openConversation],
  );

  const registerComposer = useCallback((handle: ComposerHandle | null) => {
    composerRef.current = handle;
  }, []);
  const prefillComposer = useCallback(
    (text: string, options?: { append?: boolean }) => {
      const composer = composerRef.current;
      if (composer === null) return;
      const current = composer.getDraft();
      composer.setDraft(
        options?.append === true && current !== ""
          ? `${current}\n${text}`
          : text,
      );
      composer.focus();
    },
    [],
  );

  const title = useMemo(() => {
    const current = sessions.find((s) => s.id === sessionId);
    return current?.title ?? "New conversation";
  }, [sessions, sessionId]);

  const value = useMemo<ConversationRuntimeValue>(
    () => ({
      chat,
      voice,
      voiceLive,
      voiceScope,
      items,
      toggleVoice,
      toggleMasterVoice,
      startListenOnlyMaster,
      unmuteMaster,
      stopVoice: stopVoiceNow,
      sendText,
      runAsTask,
      trackTask: addTask,
      taskFinished,
      newConversation,
      openConversation,
      deleteConversation,
      conversationActions,
      registerComposer,
      prefillComposer,
      sessions: {
        showArchived,
        setShowArchived,
        list: sessions,
        loading: sessionsLoading,
        loaded: sessionsLoaded,
        error: sessionsError,
        reload: reloadSessions,
      },
      historyLoading,
      historyError,
      attachments,
      refreshApprovals: refreshPendingApprovals,
      title,
    }),
    [
      chat,
      voice,
      voiceLive,
      voiceScope,
      items,
      toggleVoice,
      toggleMasterVoice,
      startListenOnlyMaster,
      unmuteMaster,
      stopVoiceNow,
      sendText,
      runAsTask,
      addTask,
      taskFinished,
      newConversation,
      openConversation,
      deleteConversation,
      conversationActions,
      registerComposer,
      prefillComposer,
      showArchived,
      sessions,
      sessionsLoading,
      sessionsLoaded,
      sessionsError,
      reloadSessions,
      historyLoading,
      historyError,
      attachments,
      refreshPendingApprovals,
      title,
    ],
  );

  // ---- shell extension points --------------------------------------------
  // Elements are created once: they read the runtime through context.
  const sidebarList = useMemo(() => <ConversationSidebar />, []);
  const chip = useMemo(() => <VoiceChip />, []);

  const conversationExt = useMemo<ConversationExtension>(
    () => ({
      newConversation,
      sidebarList,
      title,
    }),
    [newConversation, sidebarList, title],
  );
  const voiceExt = useMemo<VoiceExtension>(
    () => ({
      chip,
      statusLabel:
        state === "idle"
          ? null
          : `Voice${voiceScope === "master" ? " (all conversations)" : ""}: ${ORB_LABELS[state]}`,
      live: voiceLive,
    }),
    [chip, state, voiceLive, voiceScope],
  );
  useRegisterWorkspaceExtensions(conversationExt, voiceExt);

  const valueRef = useRef<ConversationRuntimeValue | null>(value);
  valueRef.current = value;
  return (
    <ConversationContext.Provider value={value}>
      <ConversationRefContext.Provider value={valueRef}>
        {children}
      </ConversationRefContext.Provider>
    </ConversationContext.Provider>
  );
}
