"use client";

import { Layers3, SquarePen } from "lucide-react";
import Link from "next/link";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { useChatStream } from "../../hooks/useChatStream";
import {
  getAccessToken,
  getAccessTokenVersion,
  subscribeAccessToken,
} from "../../lib/auth-session";
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

/**
 * The /chat canvas. It holds the access token only in memory (see
 * lib/auth-session) and shows an honest "not built" state without one.
 */
export function ChatWorkspace() {
  const token = useSyncExternalStore(
    subscribeAccessToken,
    getAccessToken,
    getServerToken,
  );
  // Which sign-in this is, not just whether there is one: it changes whenever the
  // token changes, including from one token straight to another.
  const identity = useSyncExternalStore(
    subscribeAccessToken,
    getAccessTokenVersion,
    getServerTokenVersion,
  );
  const signedIn = token !== null;
  const chat = useChatStream();
  const { reset } = chat;
  const [draft, setDraft] = useState("");
  const [theme, setTheme] = useState<ThemePreference>("system");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setTheme(readStoredTheme());
  }, []);

  // A different sign-in ends any request and clears what was on screen, the
  // unsent draft included. Before paint, so one account's conversation is never
  // shown under another's token.
  const seenIdentity = useRef(identity);
  useLayoutEffect(() => {
    if (seenIdentity.current !== identity) {
      seenIdentity.current = identity;
      reset();
      setDraft("");
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

  // The button that was pressed leaves the page when its notice goes away, so
  // focus moves to the composer, as it does for Stop and New chat.
  const focusComposer = () => {
    inputRef.current?.focus();
  };

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
    inputRef.current?.focus();
  };

  // While an error notice is showing it offers Ask again itself; the inline one
  // in the interrupted note covers every other time (a replayed interrupted
  // reply, or after the notice was dismissed).
  const inlineAskAgain =
    chat.canAskAgain && chat.error === null ? handleAskAgain : undefined;

  return (
    <div
      className="maya-chat"
      data-maya-theme={theme === "system" ? undefined : theme}
    >
      <header className="maya-chat__header">
        <div className="maya-chat__header-inner">
          <Link href="/" className="maya-brand">
            <Layers3 aria-hidden="true" size={18} />
            MAYA AI
          </Link>
          <span className="maya-chat__divider" aria-hidden="true" />
          <h1 className="maya-chat__title">Chat</h1>
          <div className="maya-chat__tools">
            {signedIn ? (
              <button
                type="button"
                className="maya-btn maya-btn--quiet"
                onClick={handleNewConversation}
              >
                <SquarePen aria-hidden="true" size={14} />
                <span className="maya-btn__label">New chat</span>
              </button>
            ) : null}
            <ThemeToggle value={theme} onChange={changeTheme} />
          </div>
        </div>
      </header>
      <main className="maya-chat__main">
        {signedIn ? (
          <>
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
          </>
        ) : (
          <SignedOutState />
        )}
      </main>
    </div>
  );
}
