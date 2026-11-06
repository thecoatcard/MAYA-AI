"use client";

import { Layers3, LogOut, Menu, PanelLeftClose, SquarePen } from "lucide-react";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent,
} from "react";

import { useChatStream } from "../../hooks/useChatStream";
import {
  ApiError,
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
import { signOut } from "../../lib/auth-client";
import type { SessionSummary, StoredMessage } from "../../lib/api-client";
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
        Voice input and task execution are not available yet.
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

/**
 * The /chat canvas. The access token stays in memory; the rotating refresh
 * token is an HttpOnly cookie and is never available to this component.
 */
export function ChatWorkspace() {
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
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const refreshTimerRef = useRef<number | null>(null);
  const wasDrawerOpen = useRef(false);
  const wasIdentity = useRef(identity);
  const syncedSessionId = useRef<string | null>(null);
  const runRefreshRef = useRef<(initial: boolean) => Promise<void>>(
    async () => {},
  );

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
      try {
        const refreshed = await refreshSession();
        if (refreshed === null) {
          setAccessToken(null);
          setAuthStatus("ready");
          setAuthWarning(null);
          clearRefreshTimer();
          return;
        }
        setAuthStatus("ready");
        setAuthWarning(null);
        scheduleRefresh(refreshed.expiresIn);
      } catch {
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
    setSessionsLoading(true);
    setSessionError(null);
    void listSessions({ limit: 50 })
      .then((page) => {
        if (active) {
          setSessions(page.items);
          setSessionCursor(page.next_cursor);
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setSessionError(sessionErrorText(error));
        }
      })
      .finally(() => {
        if (active) {
          setSessionsLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [authStatus, identity, signedIn]);

  useEffect(() => {
    if (chat.sessionId === null) {
      syncedSessionId.current = null;
      return;
    }
    if (syncedSessionId.current === chat.sessionId) {
      return;
    }
    if (sessions.some((item) => item.id === chat.sessionId)) {
      syncedSessionId.current = chat.sessionId;
      return;
    }
    syncedSessionId.current = chat.sessionId;
    let active = true;
    void listSessions({ limit: 50 })
      .then((page) => {
        if (active) {
          setSessions((current) => {
            const merged = new Map(current.map((item) => [item.id, item]));
            for (const item of page.items) {
              merged.set(item.id, item);
            }
            return [...merged.values()].sort(
              (left, right) =>
                Date.parse(right.created_at) - Date.parse(left.created_at),
            );
          });
          setSessionCursor(page.next_cursor);
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setSessionError(sessionErrorText(error));
        }
      });
    return () => {
      active = false;
    };
  }, [chat.sessionId, sessions]);

  useEffect(() => {
    if (!sidebarOpen || !isMobile) {
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
  }, [isMobile, sidebarOpen]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        reset();
        setDraft("");
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [reset]);

  useLayoutEffect(() => {
    if (wasIdentity.current !== identity) {
      wasIdentity.current = identity;
      reset();
      setDraft("");
      setSessions([]);
      setSessionCursor(null);
    }
  }, [identity, reset]);

  const changeTheme = (next: ThemePreference) => {
    setTheme(next);
    storeTheme(next);
  };

  const handleSubmit = (text: string) => {
    setDraft("");
    void chat.send(text);
  };

  const focusComposer = () => inputRef.current?.focus();

  const handleRetry = () => {
    void chat.retry();
    focusComposer();
  };

  const handleAskAgain = () => {
    void chat.askAgain();
    focusComposer();
  };

  const handleDismiss = () => {
    const restored = chat.dismissError();
    if (restored !== null && draft === "") {
      setDraft(restored);
    }
    focusComposer();
  };

  const handleNewConversation = () => {
    reset();
    setDraft("");
    setSessionError(null);
    focusComposer();
  };

  const handleLoadMore = async () => {
    if (sessionCursor === null || loadingMore) {
      return;
    }
    setLoadingMore(true);
    setSessionError(null);
    try {
      const page = await listSessions({ limit: 50, cursor: sessionCursor });
      setSessions((current) => [...current, ...page.items]);
      setSessionCursor(page.next_cursor);
    } catch (error) {
      setSessionError(sessionErrorText(error));
    } finally {
      setLoadingMore(false);
    }
  };

  const handleOpenSession = async (session: SessionSummary) => {
    if (historyLoading || chat.isBusy) {
      return;
    }
    setHistoryLoading(true);
    setSessionError(null);
    try {
      const messages: StoredMessage[] = [];
      let cursor: string | null = null;
      do {
        const page = await listMessages(session.id, { limit: 100, cursor });
        messages.push(...page.items);
        cursor = page.next_cursor;
      } while (cursor !== null);
      chat.openSession(session.id, messages);
      setSidebarOpen(false);
    } catch (error) {
      setSessionError(sessionErrorText(error));
    } finally {
      setHistoryLoading(false);
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
    if (event.key !== "Tab" || !isMobile || !sidebarOpen) {
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

  const inlineAskAgain =
    chat.canAskAgain && chat.error === null ? handleAskAgain : undefined;
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
    <div className="maya-chat">
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
          <div className="maya-workspace">
            {isMobile && sidebarOpen ? (
              <button
                type="button"
                className="maya-sidebar__scrim"
                aria-label="Close conversation drawer"
                onClick={() => setSidebarOpen(false)}
              />
            ) : null}
            <aside
              className={`maya-sidebar${sidebarOpen ? " is-open" : ""}`}
              id="maya-conversation-drawer"
              aria-label="Conversation history"
              aria-hidden={isMobile && !sidebarOpen}
              aria-modal={isMobile && sidebarOpen ? true : undefined}
              role={isMobile && sidebarOpen ? "dialog" : undefined}
              inert={isMobile && !sidebarOpen}
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
                        <li key={session.id}>
                          <button
                            type="button"
                            className="maya-session maya-ripple"
                            disabled={historyLoading || chat.isBusy}
                            aria-current={
                              session.id === chat.sessionId ? "page" : undefined
                            }
                            onPointerDown={setRipple}
                            onClick={() => void handleOpenSession(session)}
                          >
                            {session.title}
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
                        <li key={session.id}>
                          <button
                            type="button"
                            className="maya-session maya-ripple"
                            disabled={historyLoading || chat.isBusy}
                            aria-current={
                              session.id === chat.sessionId ? "page" : undefined
                            }
                            onPointerDown={setRipple}
                            onClick={() => void handleOpenSession(session)}
                          >
                            {session.title}
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
            </aside>
            <section
              className="maya-conversation"
              aria-labelledby="maya-current-title"
            >
              <div className="maya-conversation__topbar">
                <button
                  type="button"
                  className="maya-btn maya-btn--quiet maya-sidebar__trigger"
                  aria-label="Open conversation history"
                  aria-controls="maya-conversation-drawer"
                  aria-expanded={sidebarOpen}
                  onClick={() => setSidebarOpen(true)}
                  ref={menuButtonRef}
                >
                  <Menu aria-hidden="true" size={18} />
                </button>
                <h2
                  id="maya-current-title"
                  className="maya-conversation__title"
                  tabIndex={-1}
                >
                  {sessionTitle}
                </h2>
                <span className="maya-conversation__status">Text chat</span>
              </div>
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
                messages={chat.messages}
                status={chat.status}
                retrying={chat.retrying}
                empty={<EmptyConversation />}
                onAskAgain={inlineAskAgain}
              />
              <div className="maya-chat__dock">
                <ChatNotice
                  error={chat.error}
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
                />
              </div>
            </section>
          </div>
        ) : (
          <SignedOutState logoutWarning={logoutWarning} />
        )}
      </main>
    </div>
  );
}
