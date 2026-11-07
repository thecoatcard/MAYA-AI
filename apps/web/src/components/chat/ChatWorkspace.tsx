"use client";

import {
  Layers3,
  ListTodo,
  LogOut,
  Menu,
  PanelLeftClose,
  ShieldAlert,
  SquarePen,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent,
} from "react";

import type { LiveActionInfo, TranscriptItem } from "../../hooks/useLiveVoice";
import { useChatStream, type ChatError } from "../../hooks/useChatStream";
import {
  ApiError,
  deleteSession,
  listMessages,
  listSessions,
  NetworkError,
} from "../../lib/api-client";
import {
  getAccessToken,
  getAccessTokenExpiresIn,
  getAuthIdentityVersion,
  refreshSession,
  setAccessToken,
  subscribeAccessToken,
} from "../../lib/auth-session";
import { listApprovals } from "../../lib/approvals-client";
import { listTasks } from "../../lib/tasks-client";
import { signOut } from "../../lib/auth-client";
import type { Page, SessionSummary, StoredMessage } from "../../lib/api-client";
import { ApprovalCenterModal } from "../approvals/ApprovalCenterModal";
import { McpStatusIndicator } from "../mcp/McpStatusIndicator";
import { TaskCenterDrawer } from "../tasks/TaskCenterDrawer";
import { ChatInput } from "./ChatInput";
import { ChatNotice } from "./ChatNotice";
import { MessageThread } from "./MessageThread";
import { SignedOutState } from "./SignedOutState";
import {
  ThemeToggle,
  readStoredTheme,
  storeTheme,
  type ThemePreference,
} from "./ThemeToggle";

import "./chat.css";

function getServerToken(): string | null {
  return null;
}

function getServerTokenVersion(): number {
  return 0;
}

function setRipple(event: PointerEvent<HTMLElement>) {
  const bounds = event.currentTarget.getBoundingClientRect();
  event.currentTarget.style.setProperty(
    "--x",
    `${event.clientX - bounds.left}px`,
  );
  event.currentTarget.style.setProperty(
    "--y",
    `${event.clientY - bounds.top}px`,
  );
}

function groupSession(session: SessionSummary): "today" | "earlier" {
  return new Date(session.created_at).toDateString() ===
    new Date().toDateString()
    ? "today"
    : "earlier";
}

function mergeSessions(
  current: readonly SessionSummary[],
  incoming: readonly SessionSummary[],
  deleted: ReadonlySet<string>,
  replaceExisting: boolean,
): SessionSummary[] {
  const merged = new Map(
    current.filter((session) => !deleted.has(session.id)).map((s) => [s.id, s]),
  );
  for (const session of incoming) {
    if (
      !deleted.has(session.id) &&
      (replaceExisting || !merged.has(session.id))
    ) {
      merged.set(session.id, session);
    }
  }
  return [...merged.values()].sort(
    (left, right) => Date.parse(right.created_at) - Date.parse(left.created_at),
  );
}

function sessionErrorText(error: unknown): string {
  if (error instanceof NetworkError) {
    return "Conversation history could not be reached. Check the connection and try again.";
  }
  if (error instanceof ApiError && error.code === "session_not_found") {
    return "That conversation is no longer available. Choose another or start a new one.";
  }
  return "Conversation history could not be loaded. Try again.";
}

function EmptyConversation() {
  return (
    <section className="maya-empty" aria-labelledby="maya-empty-title">
      <h2 id="maya-empty-title" className="maya-empty__title">
        Start a conversation
      </h2>
      <p className="maya-empty__body">
        Ask a question or describe what you need. Replies appear as they are
        written.
      </p>
      <p className="maya-empty__planned">
        <span className="maya-tag">Planned</span>
        Hardware microphone device switching not available yet.
      </p>
    </section>
  );
}

function SessionSkeleton() {
  return (
    <div className="maya-sidebar__skeleton" aria-label="Loading conversations">
      {Array.from({ length: 5 }, (_, index) => (
        <span className="maya-skeleton" key={index} />
      ))}
    </div>
  );
}

export interface ChatWorkspaceProps {
  /**
   * When true, adapts ChatWorkspace for embedding in AppShell or AmbientWorkspace:
   * hides the redundant outer brand header and converts the outer sidebar into an overlay drawer.
   */
  readonly embedded?: boolean;
  /**
   * Explicitly hides the outer brand header. Defaults to true when embedded is true.
   */
  readonly hideHeader?: boolean;
  /**
   * Explicitly hides the conversation history sidebar drawer entirely.
   */
  readonly hideSidebar?: boolean;
  /**
   * Explicitly hides the inner conversation topbar (useful in AmbientWorkspace where a unified header exists).
   */
  readonly hideConversationTopbar?: boolean;
  /**
   * Optional microphone listening state from voice engine.
   */
  readonly isVoiceListening?: boolean;
  /**
   * Toggle voice microphone from composer.
   */
  readonly onToggleVoice?: () => void;
  /**
   * Active tool or task execution info from voice/agent stream.
   */
  readonly activeAction?: LiveActionInfo | null;
  /**
   * Callback to open Deliverable Canvas.
   */
  readonly onViewDeliverables?: () => void;
  /**
   * Real-time voice transcript items to merge with thread.
   */
  readonly voiceTranscripts?: readonly TranscriptItem[];
}

/**
 * The /chat canvas. The access token stays in memory; the rotating refresh
 * token is an HttpOnly cookie and is never available to this component.
 */
export function ChatWorkspace({
  embedded = false,
  hideHeader,
  hideSidebar = false,
  hideConversationTopbar = false,
  isVoiceListening = false,
  onToggleVoice,
  activeAction = null,
  onViewDeliverables,
  voiceTranscripts,
}: ChatWorkspaceProps = {}) {
  const shouldHideHeader = hideHeader ?? embedded;
  const shouldHideSidebar = hideSidebar;

  const token = useSyncExternalStore(
    subscribeAccessToken,
    getAccessToken,
    getServerToken,
  );
  const identity = useSyncExternalStore(
    subscribeAccessToken,
    getAuthIdentityVersion,
    getServerTokenVersion,
  );
  const signedIn = token !== null;
  const chat = useChatStream();
  const { reset } = chat;

  const displayedMessages = useMemo(() => {
    const list = [...chat.messages];

    if (voiceTranscripts && voiceTranscripts.length > 0) {
      const existingTexts = new Set(list.map((m) => m.content.trim()));
      voiceTranscripts.forEach((vt, idx) => {
        if (vt.text && !existingTexts.has(vt.text.trim())) {
          existingTexts.add(vt.text.trim());
          list.push({
            key: `voice-tx-${vt.timestamp}-${idx}`,
            id: null,
            role: vt.sender === "model" ? "assistant" : "user",
            content: vt.text,
            state: "complete",
            finishReason: null,
            usage: null,
            modality: "voice",
          });
        }
      });
    }

    if (activeAction) {
      let foundAssistant = false;
      for (let i = list.length - 1; i >= 0; i--) {
        if (list[i].role === "assistant") {
          list[i] = {
            ...list[i],
            toolAction: activeAction,
          };
          foundAssistant = true;
          break;
        }
      }
      if (!foundAssistant) {
        list.push({
          key: `live-action-${activeAction.taskId || activeAction.toolName}`,
          id: null,
          role: "assistant",
          content: `I am executing: ${activeAction.intent || activeAction.toolName}`,
          state: "complete",
          finishReason: null,
          usage: null,
          toolAction: activeAction,
        });
      }
    }

    return list;
  }, [chat.messages, voiceTranscripts, activeAction]);
  const [draft, setDraft] = useState("");
  const [theme, setTheme] = useState<ThemePreference>("system");
  const [authStatus, setAuthStatus] = useState<
    "checking" | "ready" | "offline"
  >("checking");
  const [authWarning, setAuthWarning] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutWarning, setLogoutWarning] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionCursor, setSessionCursor] = useState<string | null>(null);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(
    null,
  );
  const [hiddenError, setHiddenError] = useState<ChatError | null>(null);
  const [listRefreshVersion, setListRefreshVersion] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const isDrawerMode = isMobile || (embedded && !shouldHideSidebar);
  const [approvalModalOpen, setApprovalModalOpen] = useState(false);
  const [taskDrawerOpen, setTaskDrawerOpen] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [pendingApprovalsCount, setPendingApprovalsCount] = useState<number>(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const refreshTimerRef = useRef<number | null>(null);
  const wasDrawerOpen = useRef(false);
  const wasIdentity = useRef(identity);
  const syncedSessionId = useRef<string | null>(null);
  const sessionsRef = useRef(sessions);
  sessionsRef.current = sessions;
  const mountedRef = useRef(true);
  const conversationGenerationRef = useRef(0);
  const historyRequestRef = useRef<AbortController | null>(null);
  const moreRequestRef = useRef<AbortController | null>(null);
  const deleteRequestRef = useRef<object | null>(null);
  const deletedSessionIdsRef = useRef(new Set<string>());
  const firstPageRequestRef = useRef(0);
  const appliedFirstPageRef = useRef(0);
  const paginationAdvancedRef = useRef(false);
  const runRefreshRef = useRef<(initial: boolean) => Promise<void>>(
    async () => {},
  );

  const invalidateHistory = useCallback(() => {
    conversationGenerationRef.current += 1;
    historyRequestRef.current?.abort();
    historyRequestRef.current = null;
    setHistoryLoading(false);
  }, []);

  const applyFirstPage = useCallback(
    (page: Page<SessionSummary>, request: number): boolean => {
      if (request < appliedFirstPageRef.current) {
        return false;
      }
      appliedFirstPageRef.current = request;
      setSessions((current) =>
        mergeSessions(current, page.items, deletedSessionIdsRef.current, true),
      );
      if (!paginationAdvancedRef.current) {
        setSessionCursor(page.next_cursor);
      }
      return true;
    },
    [],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      conversationGenerationRef.current += 1;
      historyRequestRef.current?.abort();
      historyRequestRef.current = null;
      moreRequestRef.current?.abort();
      moreRequestRef.current = null;
      deleteRequestRef.current = null;
    };
  }, []);

  const clearRefreshTimer = useCallback(() => {
    if (refreshTimerRef.current !== null) {
      window.clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
  }, []);

  const scheduleRefresh = useCallback(
    (expiresIn: number) => {
      clearRefreshTimer();
      const delay = Math.max(1000, (expiresIn - 60) * 1000);
      refreshTimerRef.current = window.setTimeout(
        () => void runRefreshRef.current(false),
        delay,
      );
    },
    [clearRefreshTimer],
  );

  const runRefresh = useCallback(
    async (initial: boolean) => {
      const identityAtStart = getAuthIdentityVersion();
      try {
        const refreshed = await refreshSession();
        if (identityAtStart !== getAuthIdentityVersion()) {
          setAuthStatus("ready");
          setAuthWarning(null);
          clearRefreshTimer();
          const currentExpiry = getAccessTokenExpiresIn();
          if (currentExpiry !== null) {
            scheduleRefresh(currentExpiry);
          }
          return;
        }
        if (refreshed === null) {
          setAuthStatus("ready");
          setAuthWarning(null);
          clearRefreshTimer();
          return;
        }
        setAuthStatus("ready");
        setAuthWarning(null);
        scheduleRefresh(refreshed.expiresIn);
      } catch {
        if (identityAtStart !== getAuthIdentityVersion()) {
          return;
        }
        if (initial) {
          setAuthStatus("offline");
        } else {
          setAuthWarning(
            "Your sign-in could not be refreshed. Retrying shortly.",
          );
          scheduleRefresh(30);
        }
      }
    },
    [clearRefreshTimer, scheduleRefresh],
  );
  runRefreshRef.current = runRefresh;

  useEffect(() => {
    const stored = readStoredTheme();
    setTheme(stored);
    storeTheme(stored);
    const existing = getAccessToken();
    if (existing !== null) {
      setAuthStatus("ready");
      scheduleRefresh(getAccessTokenExpiresIn() ?? 900);
    } else {
      void runRefresh(true);
    }
    return clearRefreshTimer;
  }, [clearRefreshTimer, runRefresh, scheduleRefresh]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia("(max-width: 760px)");
    const update = () => setIsMobile(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    if (authStatus !== "ready" || !signedIn) {
      return;
    }
    let active = true;
    const controller = new AbortController();
    const request = ++firstPageRequestRef.current;
    setSessionsLoading(true);
    setSessionError(null);
    void listSessions({ limit: 50, signal: controller.signal })
      .then((page) => {
        if (
          active &&
          !controller.signal.aborted &&
          getAuthIdentityVersion() === identity
        ) {
          applyFirstPage(page, request);
        }
      })
      .catch((error: unknown) => {
        if (
          active &&
          !controller.signal.aborted &&
          getAuthIdentityVersion() === identity &&
          request >= appliedFirstPageRef.current
        ) {
          setSessionError(sessionErrorText(error));
        }
      })
      .finally(() => {
        if (
          active &&
          !controller.signal.aborted &&
          getAuthIdentityVersion() === identity
        ) {
          setSessionsLoading(false);
        }
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [applyFirstPage, authStatus, identity, signedIn]);

  useEffect(() => {
    if (authStatus !== "ready" || !signedIn || chat.sessionId === null) {
      syncedSessionId.current = null;
      return;
    }
    if (syncedSessionId.current === chat.sessionId) {
      return;
    }
    if (sessionsRef.current.some((item) => item.id === chat.sessionId)) {
      syncedSessionId.current = chat.sessionId;
      return;
    }
    const sessionId = chat.sessionId;
    const generation = conversationGenerationRef.current;
    const request = ++firstPageRequestRef.current;
    const controller = new AbortController();
    let active = true;
    setSessionError(null);
    void listSessions({ limit: 50, signal: controller.signal })
      .then((page) => {
        if (
          active &&
          !controller.signal.aborted &&
          getAuthIdentityVersion() === identity &&
          conversationGenerationRef.current === generation &&
          applyFirstPage(page, request) &&
          page.items.some((item) => item.id === sessionId) &&
          !deletedSessionIdsRef.current.has(sessionId)
        ) {
          syncedSessionId.current = sessionId;
        }
      })
      .catch((error: unknown) => {
        if (
          active &&
          !controller.signal.aborted &&
          getAuthIdentityVersion() === identity &&
          conversationGenerationRef.current === generation &&
          request >= appliedFirstPageRef.current
        ) {
          setSessionError(sessionErrorText(error));
        }
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [
    applyFirstPage,
    authStatus,
    chat.sessionId,
    identity,
    listRefreshVersion,
    signedIn,
  ]);

  useEffect(() => {
    if (!sidebarOpen || !isDrawerMode) {
      if (wasDrawerOpen.current) {
        menuButtonRef.current?.focus();
      }
      wasDrawerOpen.current = false;
      return;
    }
    wasDrawerOpen.current = true;
    const first = sidebarRef.current?.querySelector<HTMLElement>(
      "button:not(:disabled), a[href]",
    );
    first?.focus();
  }, [isDrawerMode, sidebarOpen]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        invalidateHistory();
        reset();
        setDraft("");
        setHiddenError(null);
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [invalidateHistory, reset]);

  useLayoutEffect(() => {
    if (wasIdentity.current !== identity) {
      wasIdentity.current = identity;
      invalidateHistory();
      moreRequestRef.current?.abort();
      moreRequestRef.current = null;
      deleteRequestRef.current = null;
      deletedSessionIdsRef.current.clear();
      appliedFirstPageRef.current = 0;
      paginationAdvancedRef.current = false;
      syncedSessionId.current = null;
      reset();
      setDraft("");
      setHiddenError(null);
      setSessions([]);
      setSessionCursor(null);
      setSessionsLoading(false);
      setLoadingMore(false);
      setDeletingSessionId(null);
      setSessionError(null);
    }
  }, [identity, invalidateHistory, reset]);

  const changeTheme = (next: ThemePreference) => {
    setTheme(next);
    storeTheme(next);
  };

  const handleSubmit = (text: string) => {
    invalidateHistory();
    setListRefreshVersion((version) => version + 1);
    setHiddenError(null);
    setDraft("");
    void chat.send(text);
  };

  const focusComposer = () => inputRef.current?.focus();

  const handleRetry = () => {
    invalidateHistory();
    setListRefreshVersion((version) => version + 1);
    void chat.retry();
    focusComposer();
  };

  const handleAskAgain = () => {
    invalidateHistory();
    setListRefreshVersion((version) => version + 1);
    void chat.askAgain();
    focusComposer();
  };

  const handleDismiss = () => {
    const last = chat.messages[chat.messages.length - 1];
    if (
      draft !== "" &&
      chat.error !== null &&
      last?.role === "user" &&
      last.state === "failed"
    ) {
      // Do not remove the failed bubble when another draft occupies the input.
      setHiddenError(chat.error);
      focusComposer();
      return;
    }
    const restored = chat.dismissError();
    setHiddenError(null);
    if (restored !== null && draft === "") {
      setDraft(restored);
    }
    focusComposer();
  };

  const handleNewConversation = () => {
    invalidateHistory();
    reset();
    setDraft("");
    setHiddenError(null);
    setSessionError(null);
    focusComposer();
    if (isDrawerMode) {
      setSidebarOpen(false);
    }
  };

  const handleDeleteSession = async (sessionId: string) => {
    if (
      chat.isBusy ||
      deletingSessionId !== null ||
      deleteRequestRef.current !== null ||
      getAuthIdentityVersion() !== identity
    ) {
      return;
    }
    const request = {};
    const generation = conversationGenerationRef.current;
    const wasSelected = chat.sessionId === sessionId;
    deleteRequestRef.current = request;
    const isCurrent = () =>
      mountedRef.current &&
      deleteRequestRef.current === request &&
      getAuthIdentityVersion() === identity;
    setDeletingSessionId(sessionId);
    setSessionError(null);
    try {
      await deleteSession(sessionId);
      if (!isCurrent()) {
        return;
      }
      deletedSessionIdsRef.current.add(sessionId);
      setSessions((current) => current.filter((s) => s.id !== sessionId));
      if (syncedSessionId.current === sessionId) {
        syncedSessionId.current = null;
      }
      if (wasSelected && conversationGenerationRef.current === generation) {
        handleNewConversation();
      }
    } catch {
      if (isCurrent()) {
        setSessionError("Conversation could not be deleted. Try again.");
      }
    } finally {
      if (isCurrent()) {
        deleteRequestRef.current = null;
        setDeletingSessionId(null);
      }
    }
  };

  const handleLoadMore = async () => {
    if (
      sessionCursor === null ||
      loadingMore ||
      moreRequestRef.current !== null ||
      getAuthIdentityVersion() !== identity
    ) {
      return;
    }
    const controller = new AbortController();
    moreRequestRef.current = controller;
    const isCurrent = () =>
      mountedRef.current &&
      moreRequestRef.current === controller &&
      !controller.signal.aborted &&
      getAuthIdentityVersion() === identity;
    setLoadingMore(true);
    setSessionError(null);
    try {
      const page = await listSessions({
        limit: 50,
        cursor: sessionCursor,
        signal: controller.signal,
      });
      if (!isCurrent()) {
        return;
      }
      setSessions((current) =>
        mergeSessions(current, page.items, deletedSessionIdsRef.current, false),
      );
      paginationAdvancedRef.current = true;
      setSessionCursor(page.next_cursor);
    } catch (error) {
      if (isCurrent()) {
        setSessionError(sessionErrorText(error));
      }
    } finally {
      if (isCurrent()) {
        moreRequestRef.current = null;
        setLoadingMore(false);
      }
    }
  };

  const handleOpenSession = async (session: SessionSummary) => {
    if (
      historyLoading ||
      historyRequestRef.current !== null ||
      chat.isBusy ||
      getAuthIdentityVersion() !== identity
    ) {
      return;
    }
    invalidateHistory();
    const generation = conversationGenerationRef.current;
    const controller = new AbortController();
    historyRequestRef.current = controller;
    const isCurrent = () =>
      mountedRef.current &&
      historyRequestRef.current === controller &&
      !controller.signal.aborted &&
      conversationGenerationRef.current === generation &&
      getAuthIdentityVersion() === identity;
    setHistoryLoading(true);
    setSessionError(null);
    try {
      const messages: StoredMessage[] = [];
      let cursor: string | null = null;
      do {
        const page = await listMessages(session.id, {
          limit: 100,
          cursor,
          signal: controller.signal,
        });
        if (!isCurrent()) {
          return;
        }
        messages.push(...page.items);
        cursor = page.next_cursor;
      } while (cursor !== null);
      chat.openSession(session.id, messages);
      setSidebarOpen(false);
    } catch (error) {
      if (isCurrent()) {
        setSessionError(sessionErrorText(error));
      }
    } finally {
      if (isCurrent()) {
        historyRequestRef.current = null;
        setHistoryLoading(false);
      }
    }
  };

  const handleSignOut = async () => {
    if (loggingOut) {
      return;
    }
    setLoggingOut(true);
    setLogoutWarning(null);
    try {
      await signOut();
    } catch {
      setLogoutWarning(
        "You are signed out on this page, but the server could not revoke the session.",
      );
    } finally {
      clearRefreshTimer();
      setAccessToken(null);
      reset();
      setLoggingOut(false);
    }
  };

  const handleDrawerKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setSidebarOpen(false);
      return;
    }
    if (event.key !== "Tab" || !isDrawerMode || !sidebarOpen) {
      return;
    }
    const focusable = Array.from(
      sidebarRef.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), a[href], input:not(:disabled), [tabindex]:not([tabindex="-1"])',
      ) ?? [],
    );
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  };

  const displayedError = chat.error === hiddenError ? null : chat.error;
  const inlineAskAgain =
    chat.canAskAgain && displayedError === null ? handleAskAgain : undefined;
  const todaySessions = sessions.filter(
    (session) => groupSession(session) === "today",
  );
  const earlierSessions = sessions.filter(
    (session) => groupSession(session) === "earlier",
  );
  const sessionTitle =
    sessions.find((session) => session.id === chat.sessionId)?.title ??
    (chat.sessionId === null ? "New conversation" : "Conversation");

  return (
    <div className={`maya-chat ${embedded ? "maya-chat--embedded" : ""}`}>
      {!shouldHideHeader && (
        <header className="maya-chat__header">
          <div className="maya-chat__header-inner">
            <Link href="/" className="maya-brand">
              <Layers3 aria-hidden="true" size={18} />
              MAYA AI
            </Link>
            <span className="maya-chat__divider" aria-hidden="true" />
            <h1 className="maya-chat__title">Chat</h1>
            <div className="maya-chat__tools">
              <ThemeToggle value={theme} onChange={changeTheme} />
            </div>
          </div>
        </header>
      )}
      <main className="maya-chat__main">
        {authStatus === "checking" ? (
          <section className="maya-auth-check" aria-busy="true">
            <h2 className="maya-auth-check__title">Checking your sign-in</h2>
            <span className="maya-skeleton" />
            <span className="maya-skeleton" />
          </section>
        ) : authStatus === "offline" ? (
          <section
            className="maya-auth-check"
            aria-labelledby="maya-auth-error"
          >
            <h2 id="maya-auth-error" className="maya-auth-check__title">
              Sign-in could not be checked
            </h2>
            <p role="alert">
              MAYA could not reach the account service. Check your connection
              and try again.
            </p>
            <button
              className="maya-btn maya-btn--primary maya-ripple"
              onClick={() => void runRefresh(true)}
              type="button"
            >
              Try again
            </button>
          </section>
        ) : signedIn ? (
          <div
            className={`maya-workspace ${
              shouldHideSidebar
                ? "maya-workspace--no-sidebar"
                : isDrawerMode && !isMobile
                  ? "maya-workspace--drawer"
                  : ""
            }`}
            style={
              shouldHideSidebar || (isDrawerMode && !isMobile)
                ? { gridTemplateColumns: "minmax(0, 1fr)" }
                : undefined
            }
          >
            {!shouldHideSidebar && isDrawerMode && sidebarOpen ? (
              <button
                type="button"
                className="maya-sidebar__scrim"
                aria-label="Dismiss conversation overlay"
                onClick={() => setSidebarOpen(false)}
              />
            ) : null}
            {!shouldHideSidebar && (
              <aside
                className={`maya-sidebar${sidebarOpen ? " is-open" : ""}`}
                id="maya-conversation-drawer"
                aria-label="Conversation history"
                aria-hidden={isDrawerMode && !sidebarOpen}
                aria-modal={isDrawerMode && sidebarOpen ? true : undefined}
                role={isDrawerMode && sidebarOpen ? "dialog" : undefined}
                inert={isDrawerMode && !sidebarOpen}
                onKeyDown={handleDrawerKeyDown}
                ref={sidebarRef}
              >
                <div className="maya-sidebar__head">
                  <span className="maya-sidebar__wordmark">Conversations</span>
                  <button
                    type="button"
                    className="maya-btn maya-btn--quiet maya-sidebar__close"
                    aria-label="Close conversation drawer"
                    onClick={() => setSidebarOpen(false)}
                  >
                    <PanelLeftClose aria-hidden="true" size={16} />
                  </button>
                </div>
                <button
                  type="button"
                  className="maya-btn maya-btn--primary maya-sidebar__new maya-ripple"
                  onPointerDown={setRipple}
                  onClick={handleNewConversation}
                >
                  <SquarePen aria-hidden="true" size={16} />
                  <span>New conversation</span>
                  <kbd>Ctrl K</kbd>
                </button>
                <nav
                  className="maya-sidebar__sessions"
                  aria-label="Your conversations"
                  aria-busy={sessionsLoading || historyLoading}
                >
                  {sessionsLoading ? <SessionSkeleton /> : null}
                  {!sessionsLoading && sessions.length === 0 ? (
                    <p className="maya-sidebar__empty">
                      Your saved conversations will appear here.
                    </p>
                  ) : null}
                  {todaySessions.length > 0 ? (
                    <section aria-labelledby="maya-sessions-today">
                      <h2
                        id="maya-sessions-today"
                        className="maya-sidebar__group"
                      >
                        Today
                      </h2>
                      <ul>
                        {todaySessions.map((session) => (
                          <li key={session.id} className="maya-session__item">
                            <button
                              type="button"
                              className="maya-session maya-ripple"
                              disabled={
                                historyLoading ||
                                chat.isBusy ||
                                deletingSessionId !== null
                              }
                              aria-current={
                                session.id === chat.sessionId
                                  ? "page"
                                  : undefined
                              }
                              onPointerDown={setRipple}
                              onClick={() => void handleOpenSession(session)}
                            >
                              {session.title}
                            </button>
                            <button
                              type="button"
                              className="maya-session__delete"
                              aria-label={`Delete conversation ${session.title}`}
                              title="Delete conversation"
                              disabled={
                                historyLoading ||
                                chat.isBusy ||
                                deletingSessionId !== null
                              }
                              onClick={(event) => {
                                event.stopPropagation();
                                void handleDeleteSession(session.id);
                              }}
                            >
                              <Trash2 aria-hidden="true" size={14} />
                            </button>
                          </li>
                        ))}
                      </ul>
                    </section>
                  ) : null}
                  {earlierSessions.length > 0 ? (
                    <section aria-labelledby="maya-sessions-earlier">
                      <h2
                        id="maya-sessions-earlier"
                        className="maya-sidebar__group"
                      >
                        Earlier
                      </h2>
                      <ul>
                        {earlierSessions.map((session) => (
                          <li key={session.id} className="maya-session__item">
                            <button
                              type="button"
                              className="maya-session maya-ripple"
                              disabled={
                                historyLoading ||
                                chat.isBusy ||
                                deletingSessionId !== null
                              }
                              aria-current={
                                session.id === chat.sessionId
                                  ? "page"
                                  : undefined
                              }
                              onPointerDown={setRipple}
                              onClick={() => void handleOpenSession(session)}
                            >
                              {session.title}
                            </button>
                            <button
                              type="button"
                              className="maya-session__delete"
                              aria-label={`Delete conversation ${session.title}`}
                              title="Delete conversation"
                              disabled={
                                historyLoading ||
                                chat.isBusy ||
                                deletingSessionId !== null
                              }
                              onClick={(event) => {
                                event.stopPropagation();
                                void handleDeleteSession(session.id);
                              }}
                            >
                              <Trash2 aria-hidden="true" size={14} />
                            </button>
                          </li>
                        ))}
                      </ul>
                    </section>
                  ) : null}
                  {sessionCursor !== null ? (
                    <button
                      type="button"
                      className="maya-btn maya-btn--quiet maya-sidebar__more"
                      onClick={() => void handleLoadMore()}
                      disabled={loadingMore}
                    >
                      {loadingMore ? "Loading…" : "Load more"}
                    </button>
                  ) : null}
                </nav>
                {sessionError ? (
                  <p className="maya-sidebar__error" role="alert">
                    {sessionError}
                  </p>
                ) : null}
                {!embedded && (
                  <div className="maya-sidebar__account">
                    <span>Signed in</span>
                    <button
                      type="button"
                      className="maya-btn maya-btn--quiet"
                      onClick={() => void handleSignOut()}
                      disabled={loggingOut}
                    >
                      <LogOut aria-hidden="true" size={15} />
                      {loggingOut ? "Signing out…" : "Sign out"}
                    </button>
                  </div>
                )}
              </aside>
            )}
            <section
              className="maya-conversation"
              aria-labelledby="maya-current-title"
            >
              {!hideConversationTopbar && (
                <div className="maya-conversation__topbar">
                  {!shouldHideSidebar && (
                    <button
                      type="button"
                      className="maya-btn maya-btn--quiet maya-sidebar__trigger"
                      aria-label="Open conversation history"
                      aria-controls="maya-conversation-drawer"
                      aria-expanded={sidebarOpen}
                      onClick={() => setSidebarOpen((prev) => !prev)}
                      ref={menuButtonRef}
                      style={
                        isDrawerMode && !isMobile
                          ? { display: "inline-flex" }
                          : undefined
                      }
                    >
                      <Menu aria-hidden="true" size={18} />
                    </button>
                  )}
                  <h2
                    id="maya-current-title"
                    className="maya-conversation__title"
                    tabIndex={-1}
                  >
                    {sessionTitle}
                  </h2>
                  {chat.sessionId !== null && (
                    <button
                      type="button"
                      className="maya-btn maya-btn--quiet maya-conversation__delete"
                      aria-label="Delete this conversation"
                      title="Delete this conversation"
                      disabled={chat.isBusy || deletingSessionId !== null}
                      onClick={() => void handleDeleteSession(chat.sessionId!)}
                    >
                      <Trash2 aria-hidden="true" size={16} />
                    </button>
                  )}
                  <div className="flex items-center gap-2 ml-auto">
                    <McpStatusIndicator />
                    <button
                      type="button"
                      className="maya-btn maya-btn--quiet flex items-center gap-1.5 text-xs font-mono font-medium px-2 py-1 rounded border border-zinc-800 hover:border-zinc-700"
                      onClick={() => setApprovalModalOpen(true)}
                      title="Open Approval Center"
                    >
                      <ShieldAlert
                        className={`w-3.5 h-3.5 ${
                          pendingApprovalsCount > 0
                            ? "text-amber-400"
                            : "text-zinc-400"
                        }`}
                      />
                      <span>Approvals</span>
                      {pendingApprovalsCount > 0 && (
                        <span className="px-1.5 py-0.2 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-400 border border-amber-500/30">
                          {pendingApprovalsCount}
                        </span>
                      )}
                    </button>
                    <button
                      type="button"
                      className="maya-btn maya-btn--quiet flex items-center gap-1.5 text-xs font-mono font-medium px-2 py-1 rounded border border-zinc-800 hover:border-zinc-700"
                      onClick={async () => {
                        if (!selectedTaskId) {
                          try {
                            const res = await listTasks(undefined, 1);
                            if (
                              res.items &&
                              res.items.length > 0 &&
                              res.items[0]
                            ) {
                              setSelectedTaskId(res.items[0].id);
                            }
                          } catch {
                            // If none found or error, keep null (drawer shows clean empty state)
                          }
                        }
                        setTaskDrawerOpen(true);
                      }}
                      title="Open Task Center"
                    >
                      <ListTodo className="w-3.5 h-3.5 text-zinc-400" />
                      <span>Tasks</span>
                    </button>
                    {!embedded && (
                      <Link
                        href="/workspace"
                        className="flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded bg-teal-600 hover:bg-teal-500 text-zinc-950 transition-colors shadow"
                        title="Open Ambient Voice & Deliverables Workspace"
                      >
                        <span>Ambient Workspace →</span>
                      </Link>
                    )}
                    <span className="maya-conversation__status">Text chat</span>
                  </div>
                </div>
              )}
              {authWarning ? (
                <p className="maya-auth-warning" role="status">
                  {authWarning}
                </p>
              ) : null}
              {logoutWarning ? (
                <p className="maya-auth-warning" role="status">
                  {logoutWarning}
                </p>
              ) : null}
              <MessageThread
                messages={displayedMessages}
                status={chat.status}
                retrying={chat.retrying}
                empty={<EmptyConversation />}
                onAskAgain={inlineAskAgain}
                onViewDeliverables={onViewDeliverables}
                onOpenTaskDetails={(taskId) => {
                  setSelectedTaskId(taskId);
                  setTaskDrawerOpen(true);
                }}
              />
              <div className="maya-chat__dock">
                <ChatNotice
                  error={displayedError}
                  retrying={chat.retrying}
                  canRetry={chat.canRetry}
                  canAskAgain={chat.canAskAgain}
                  onRetry={handleRetry}
                  onAskAgain={handleAskAgain}
                  onDismiss={handleDismiss}
                />
                <ChatInput
                  value={draft}
                  onValueChange={setDraft}
                  onSubmit={handleSubmit}
                  onStop={chat.stop}
                  busy={chat.isBusy}
                  inputRef={inputRef}
                  isListening={isVoiceListening}
                  onToggleVoice={onToggleVoice}
                />
              </div>
            </section>
            <ApprovalCenterModal
              isOpen={approvalModalOpen}
              onClose={() => {
                setApprovalModalOpen(false);
                void listApprovals("pending")
                  .then((list) => setPendingApprovalsCount(list.length))
                  .catch(() => {});
              }}
              onDecisionMade={() => {
                void listApprovals("pending")
                  .then((list) => setPendingApprovalsCount(list.length))
                  .catch(() => {});
              }}
            />
            <TaskCenterDrawer
              taskId={selectedTaskId}
              isOpen={taskDrawerOpen}
              onClose={() => setTaskDrawerOpen(false)}
            />
          </div>
        ) : (
          <SignedOutState logoutWarning={logoutWarning} />
        )}
      </main>
    </div>
  );
}
