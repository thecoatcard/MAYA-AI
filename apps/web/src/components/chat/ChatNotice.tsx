"use client";

import { RefreshCw, TriangleAlert, X } from "lucide-react";
import { useEffect, useId, useState } from "react";

import type { ChatError, ChatRetryInfo } from "../../hooks/useChatStream";

export interface ChatNoticeProps {
  error: ChatError | null;
  retrying: ChatRetryInfo | null;
  /** The same-key Retry is offered: the server's outcome is unknown. */
  canRetry: boolean;
  /** Offered after a reply was interrupted: a new message with a new key. */
  canAskAgain: boolean;
  onRetry: () => void;
  onAskAgain: () => void;
  onDismiss: () => void;
}

/**
 * Seconds left until `deadline`, rounded up. Computed from the clock instead of
 * counted down tick by tick, so a throttled background tab cannot leave Retry
 * disabled for longer than the server asked.
 */
function secondsUntil(deadline: number): number {
  return Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
}

function ErrorNotice({
  error,
  canRetry,
  canAskAgain,
  onRetry,
  onAskAgain,
  onDismiss,
}: Omit<ChatNoticeProps, "retrying" | "error"> & { error: ChatError }) {
  const initialWait = error.retryAfterSeconds ?? 0;
  const [remaining, setRemaining] = useState(initialWait);
  const waitId = useId();

  // Count down the Retry-After wait against a fixed deadline. The deadline is
  // checked on every tick and again when the tab becomes visible.
  useEffect(() => {
    if (initialWait <= 0) {
      setRemaining(0);
      return;
    }
    const deadline = Date.now() + initialWait * 1000;
    const update = () => setRemaining(secondsUntil(deadline));
    update();
    const id = setInterval(update, 1000);
    document.addEventListener("visibilitychange", update);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", update);
    };
  }, [error, initialWait]);

  const waiting = remaining > 0;
  const waitText =
    error.code === "rate_limited" || error.code === "concurrent_stream_limit"
      ? initialWait > 0
        ? ` Try again in about ${initialWait} ${initialWait === 1 ? "second" : "seconds"}.`
        : " Wait a moment, then try again."
      : "";

  // Retry (same key) leads whenever it is offered; otherwise Ask again does.
  const askAgainClass = canRetry ? "maya-btn" : "maya-btn maya-btn--primary";

  return (
    <div
      role="group"
      aria-label="Message problem"
      className="maya-cnotice maya-cnotice--error"
    >
      <TriangleAlert
        aria-hidden="true"
        size={18}
        className="maya-cnotice__icon"
      />
      {/* Only this static text is a live region. The countdown and the buttons
          sit outside it, so a screen reader is not interrupted every second. */}
      <p role="alert" className="maya-cnotice__text">
        {error.message}
        {waitText}
      </p>
      <div className="maya-cnotice__actions">
        {canRetry ? (
          <>
            <button
              type="button"
              className="maya-btn maya-btn--primary"
              onClick={onRetry}
              disabled={waiting}
              aria-describedby={waiting ? waitId : undefined}
            >
              <RefreshCw aria-hidden="true" size={14} />
              Retry
            </button>
            {waiting ? (
              <>
                <span className="maya-cnotice__countdown" aria-hidden="true">
                  Available in {remaining}s
                </span>
                <span id={waitId} className="maya-sr-only">
                  Available when the wait ends.
                </span>
              </>
            ) : null}
          </>
        ) : null}
        {canAskAgain ? (
          <button type="button" className={askAgainClass} onClick={onAskAgain}>
            Ask again
          </button>
        ) : null}
        <button
          type="button"
          className="maya-btn maya-btn--quiet"
          onClick={onDismiss}
        >
          <X aria-hidden="true" size={14} />
          Dismiss
        </button>
      </div>
    </div>
  );
}

export function ChatNotice({ error, retrying, ...rest }: ChatNoticeProps) {
  if (error !== null) {
    return <ErrorNotice error={error} {...rest} />;
  }
  if (retrying !== null) {
    return (
      <div role="status" className="maya-cnotice maya-cnotice--info">
        <RefreshCw
          aria-hidden="true"
          size={16}
          className="maya-cnotice__icon"
        />
        <p className="maya-cnotice__text">
          The reply did not arrive. Retrying automatically, attempt{" "}
          {retrying.attempt} of {retrying.maxAttempts}.
        </p>
      </div>
    );
  }
  return null;
}
