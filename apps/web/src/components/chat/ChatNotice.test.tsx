import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatError } from "../../hooks/useChatStream";
import { ChatNotice, type ChatNoticeProps } from "./ChatNotice";

function error(overrides: Partial<ChatError> = {}): ChatError {
  return {
    code: "network_error",
    message: "The connection to the server was lost.",
    retryable: true,
    retryAfterSeconds: null,
    ...overrides,
  };
}

function notice(props: Partial<ChatNoticeProps> = {}) {
  const handlers = {
    onRetry: vi.fn(),
    onAskAgain: vi.fn(),
    onDismiss: vi.fn(),
  };
  const view = render(
    <ChatNotice
      error={error()}
      retrying={null}
      canRetry
      canAskAgain={false}
      {...handlers}
      {...props}
    />,
  );
  return { ...view, ...handlers };
}

const rateLimited = (seconds: number | null) =>
  error({
    code: "rate_limited",
    message: "You have reached the message rate limit.",
    retryAfterSeconds: seconds,
  });

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("ChatNotice live region", () => {
  it("makes only the static message an alert, with the actions outside it", () => {
    notice({ error: rateLimited(30), canAskAgain: true });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(
      "You have reached the message rate limit. Try again in about 30 seconds.",
    );
    for (const name of ["Retry", "Ask again", "Dismiss"]) {
      const button = screen.getByRole("button", { name });
      expect(alert.contains(button)).toBe(false);
    }
    expect(alert.querySelector("button")).toBeNull();
    // The visual countdown is not in the alert either.
    expect(alert.textContent).not.toMatch(/Available in/);
  });

  it("changes nothing inside the alert while the wait counts down", () => {
    notice({ error: rateLimited(5) });
    const alert = screen.getByRole("alert");
    const countdown = screen.getByText("Available in 5s");
    let alertMutations = 0;
    const alertObserver = new MutationObserver((records) => {
      alertMutations += records.length;
    });
    alertObserver.observe(alert, {
      attributes: true,
      childList: true,
      characterData: true,
      subtree: true,
    });
    // The same pattern as the thread test, aimed at the visual countdown, to show
    // the observer would notice a change if the alert had one.
    let countdownMutations = 0;
    const countdownObserver = new MutationObserver((records) => {
      countdownMutations += records.length;
    });
    countdownObserver.observe(countdown, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    for (let second = 0; second < 5; second += 1) {
      act(() => {
        vi.advanceTimersByTime(1000);
      });
    }
    alertMutations += alertObserver.takeRecords().length;
    countdownMutations += countdownObserver.takeRecords().length;
    alertObserver.disconnect();
    countdownObserver.disconnect();
    expect(alertMutations).toBe(0);
    expect(countdownMutations).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("groups the notice with a name for assistive technology", () => {
    notice();
    expect(
      screen.getByRole("group", { name: "Message problem" }),
    ).toBeInTheDocument();
  });
});

describe("ChatNotice Retry countdown", () => {
  it("keeps the button name static and shows the countdown as hidden visual text", () => {
    notice({ error: rateLimited(3) });
    const retry = screen.getByRole("button", { name: "Retry" });
    expect(retry).toBeDisabled();
    expect(retry.textContent).toBe("Retry");
    const visual = screen.getByText("Available in 3s");
    expect(visual).toHaveAttribute("aria-hidden", "true");
    expect(retry.contains(visual)).toBe(false);
    for (const expected of ["2s", "1s"]) {
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Retry" }).textContent).toBe(
        "Retry",
      );
      expect(screen.getByText(`Available in ${expected}`)).toBeInTheDocument();
    }
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(screen.queryByText(/Available in/)).toBeNull();
  });

  it("describes the disabled button with static text", () => {
    notice({ error: rateLimited(10) });
    const retry = screen.getByRole("button", { name: "Retry" });
    const described = retry.getAttribute("aria-describedby");
    expect(described).not.toBeNull();
    const description = document.getElementById(described ?? "");
    expect(description?.textContent).toBe("Available when the wait ends.");
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(description?.textContent).toBe("Available when the wait ends.");
  });

  it("calls Retry once the wait is over", () => {
    const { onRetry } = notice({ error: rateLimited(2) });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("works from a deadline, so a throttled background tab is not kept waiting", () => {
    notice({ error: rateLimited(30) });
    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
    // The tab is in the background: no timer fires, but the clock moves on.
    vi.setSystemTime(Date.now() + 31_000);
    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
    // Coming back to the tab recomputes from the deadline at once.
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("counts a late tick against the clock, not by one", () => {
    notice({ error: rateLimited(10) });
    // One throttled timer callback arrives after 7 seconds have really passed.
    vi.setSystemTime(Date.now() + 7000);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByText("Available in 2s")).toBeInTheDocument();
  });

  it("stops listening when the notice goes away", () => {
    const remove = vi.spyOn(document, "removeEventListener");
    const { unmount } = notice({ error: rateLimited(10) });
    unmount();
    expect(remove).toHaveBeenCalledWith(
      "visibilitychange",
      expect.any(Function),
    );
    expect(vi.getTimerCount()).toBe(0);
  });

  it("starts a new wait when a new rate limit replaces the old one", () => {
    const first = rateLimited(2);
    const { rerender, onRetry, onAskAgain, onDismiss } = notice({
      error: first,
    });
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    rerender(
      <ChatNotice
        error={rateLimited(8)}
        retrying={null}
        canRetry
        canAskAgain={false}
        onRetry={onRetry}
        onAskAgain={onAskAgain}
        onDismiss={onDismiss}
      />,
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
    expect(screen.getByText("Available in 8s")).toBeInTheDocument();
  });

  it("shows no countdown when the server gave no wait", () => {
    notice({ error: rateLimited(null) });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Wait a moment, then try again.",
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(screen.queryByText(/Available in/)).toBeNull();
  });

  it("uses the singular for a one-second wait", () => {
    notice({ error: rateLimited(1) });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Try again in about 1 second.",
    );
  });
});

describe("ChatNotice actions", () => {
  it("leads with Retry when it is offered, and Ask again is secondary", () => {
    notice({ canRetry: true, canAskAgain: true });
    expect(screen.getByRole("button", { name: "Retry" })).toHaveClass(
      "maya-btn--primary",
    );
    expect(screen.getByRole("button", { name: "Ask again" })).not.toHaveClass(
      "maya-btn--primary",
    );
  });

  it("leads with Ask again when there is no same-key Retry", () => {
    notice({
      error: error({ code: "stream_interrupted", retryable: false }),
      canRetry: false,
      canAskAgain: true,
    });
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.getByRole("button", { name: "Ask again" })).toHaveClass(
      "maya-btn--primary",
    );
  });

  it("always offers Dismiss and reports each choice", () => {
    const { onRetry, onAskAgain, onDismiss } = notice({
      canRetry: true,
      canAskAgain: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    fireEvent.click(screen.getByRole("button", { name: "Ask again" }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onAskAgain).toHaveBeenCalledTimes(1);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("shows the automatic retry as a polite status, not an alert", () => {
    notice({ error: null, retrying: { attempt: 2, maxAttempts: 3 } });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Retrying automatically, attempt 2 of 3.",
    );
  });

  it("renders nothing when there is nothing to say", () => {
    const { container } = notice({ error: null, retrying: null });
    expect(container).toBeEmptyDOMElement();
  });
});
