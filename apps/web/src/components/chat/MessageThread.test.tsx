import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage, ChatStatus } from "../../hooks/useChatStream";
import { MessageThread } from "./MessageThread";

function message(
  overrides: Partial<ChatMessage> & Pick<ChatMessage, "key" | "role">,
): ChatMessage {
  return {
    id: null,
    content: "",
    state: overrides.role === "user" ? "sent" : "complete",
    finishReason: null,
    usage: null,
    ...overrides,
  };
}

const user = (key: string, content: string): ChatMessage =>
  message({ key, role: "user", content });
const assistant = (
  key: string,
  content: string,
  state: ChatMessage["state"] = "complete",
): ChatMessage => message({ key, role: "assistant", content, state });

function statusRegion(): HTMLElement {
  const region = document.querySelector<HTMLElement>(
    '[role="status"][aria-live="polite"]',
  );
  if (region === null) {
    throw new Error("status region not found");
  }
  return region;
}

function log(): HTMLElement {
  return screen.getByRole("log", { name: "Conversation" });
}

/** jsdom has no layout, so give the scroll container real-looking metrics. */
function setMetrics(
  element: HTMLElement,
  scrollHeight: number,
  clientHeight: number,
) {
  Object.defineProperty(element, "scrollHeight", {
    configurable: true,
    value: scrollHeight,
  });
  Object.defineProperty(element, "clientHeight", {
    configurable: true,
    value: clientHeight,
  });
}

afterEach(() => {
  Reflect.deleteProperty(window, "matchMedia");
});

describe("MessageThread structure", () => {
  it("is a labelled, focusable log region that does not announce every change", () => {
    render(<MessageThread messages={[]} status="idle" />);
    const region = log();
    expect(region).toHaveAttribute("aria-live", "off");
    expect(region).toHaveAttribute("tabindex", "0");
  });

  it("renders user text as plain text and the assistant reply as Markdown", () => {
    const { container } = render(
      <MessageThread
        messages={[
          user("u1", "**not bold** <b>x</b>"),
          assistant("a1", "This is **bold**."),
        ]}
        status="idle"
      />,
    );
    const userArticle = container.querySelector(".maya-msg--user");
    expect(userArticle?.textContent).toContain("**not bold** <b>x</b>");
    expect(userArticle?.querySelector("strong, b")).toBeNull();
    expect(
      container.querySelector(".maya-msg--assistant strong")?.textContent,
    ).toBe("bold");
    expect(
      screen.getByRole("heading", { name: "You said", level: 2 }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "MAYA said", level: 2 }),
    ).toBeInTheDocument();
  });

  it("shows the empty state only for an empty, idle conversation", () => {
    const { rerender } = render(
      <MessageThread messages={[]} status="idle" empty={<p>Nothing yet</p>} />,
    );
    expect(screen.getByText("Nothing yet")).toBeInTheDocument();
    rerender(
      <MessageThread
        messages={[]}
        status="sending"
        empty={<p>Nothing yet</p>}
      />,
    );
    expect(screen.queryByText("Nothing yet")).toBeNull();
    rerender(
      <MessageThread
        messages={[user("u1", "hi")]}
        status="idle"
        empty={<p>Nothing yet</p>}
      />,
    );
    expect(screen.queryByText("Nothing yet")).toBeNull();
  });

  it("labels failed, interrupted and stopped messages with text, not colour alone", () => {
    render(
      <MessageThread
        messages={[
          message({ key: "u1", role: "user", content: "a", state: "failed" }),
          assistant("a1", "partial", "interrupted"),
          assistant("a2", "", "stopped"),
          message({
            key: "a3",
            role: "assistant",
            content: "cut",
            state: "complete",
            finishReason: "max_tokens",
          }),
        ]}
        status="idle"
      />,
    );
    expect(screen.getByText("Not sent")).toBeInTheDocument();
    expect(
      screen.getByText("Reply interrupted. The text above is what arrived."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Stopped before a reply started."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("The reply reached the length limit and was cut off."),
    ).toBeInTheDocument();
  });
});

describe("MessageThread unconfirmed and Ask again", () => {
  it("says a message may have been received when its delivery is unknown", () => {
    render(
      <MessageThread
        messages={[
          message({
            key: "u1",
            role: "user",
            content: "hello",
            state: "unconfirmed",
          }),
        ]}
        status="idle"
      />,
    );
    expect(
      screen.getByText(
        "Not confirmed. The server may have received this message.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Not sent")).toBeNull();
  });

  it("puts a small Ask again inside the interrupted note of the last message", () => {
    const onAskAgain = vi.fn();
    render(
      <MessageThread
        messages={[user("u1", "hi"), assistant("a1", "part", "interrupted")]}
        status="idle"
        onAskAgain={onAskAgain}
      />,
    );
    const button = screen.getByRole("button", { name: "Ask again" });
    expect(button.closest(".maya-msg__note")).toHaveTextContent(
      "Reply interrupted. The text above is what arrived.",
    );
    fireEvent.click(button);
    expect(onAskAgain).toHaveBeenCalledTimes(1);
  });

  it("offers no Ask again unless asked to, and never on an older message", () => {
    const onAskAgain = vi.fn();
    const { rerender } = render(
      <MessageThread
        messages={[user("u1", "hi"), assistant("a1", "part", "interrupted")]}
        status="idle"
      />,
    );
    expect(screen.queryByRole("button", { name: "Ask again" })).toBeNull();
    rerender(
      <MessageThread
        messages={[
          user("u1", "hi"),
          assistant("a1", "part", "interrupted"),
          user("u2", "again"),
          assistant("a2", "ok", "complete"),
        ]}
        status="idle"
        onAskAgain={onAskAgain}
      />,
    );
    expect(screen.queryByRole("button", { name: "Ask again" })).toBeNull();
  });

  it("also covers an interrupted reply with no text", () => {
    render(
      <MessageThread
        messages={[user("u1", "hi"), assistant("a1", "", "interrupted")]}
        status="idle"
        onAskAgain={() => undefined}
      />,
    );
    expect(
      screen.getByText("Reply interrupted before any text arrived."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask again" })).toBeVisible();
  });
});

describe("MessageThread waiting states", () => {
  it("shows Sending with a text label while the request is in flight", () => {
    render(<MessageThread messages={[user("u1", "hi")]} status="sending" />);
    expect(screen.getByText("Sending")).toBeInTheDocument();
  });

  it("shows the retry attempt", () => {
    render(
      <MessageThread
        messages={[user("u1", "hi")]}
        status="sending"
        retrying={{ attempt: 2, maxAttempts: 3 }}
      />,
    );
    expect(screen.getByText("Retrying, attempt 2 of 3")).toBeInTheDocument();
  });

  it("shows Thinking while a reply has started but has no text yet", () => {
    const { container } = render(
      <MessageThread
        messages={[user("u1", "hi"), assistant("a1", "", "streaming")]}
        status="streaming"
      />,
    );
    expect(screen.getByText("Thinking")).toBeInTheDocument();
    expect(container.querySelector("article[aria-busy='true']")).not.toBeNull();
  });

  it("shows the streaming caret once text arrives and removes it when finished", () => {
    const { container, rerender } = render(
      <MessageThread
        messages={[user("u1", "hi"), assistant("a1", "Hel", "streaming")]}
        status="streaming"
      />,
    );
    expect(screen.queryByText("Thinking")).toBeNull();
    expect(container.querySelector(".maya-caret")).not.toBeNull();
    rerender(
      <MessageThread
        messages={[user("u1", "hi"), assistant("a1", "Hello", "complete")]}
        status="idle"
      />,
    );
    expect(container.querySelector(".maya-caret")).toBeNull();
    expect(container.querySelector("article[aria-busy='true']")).toBeNull();
  });
});

describe("MessageThread screen reader announcements", () => {
  it("speaks per state change, never per streamed token", () => {
    const { rerender } = render(
      <MessageThread
        messages={[user("u1", "hi"), assistant("a1", "a", "streaming")]}
        status="streaming"
      />,
    );
    const region = statusRegion();
    expect(region).toHaveTextContent("MAYA is replying.");
    let mutations = 0;
    const observer = new MutationObserver((records) => {
      mutations += records.length;
    });
    observer.observe(region, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    let text = "a";
    for (let i = 0; i < 60; i += 1) {
      text += " token";
      rerender(
        <MessageThread
          messages={[user("u1", "hi"), assistant("a1", text, "streaming")]}
          status="streaming"
        />,
      );
    }
    // MutationObserver callbacks are asynchronous; take what is pending.
    mutations += observer.takeRecords().length;
    observer.disconnect();
    expect(mutations).toBe(0);
    expect(region).toHaveTextContent("MAYA is replying.");
    expect(log()).toHaveAttribute("aria-live", "off");
  });

  it.each<[string, ChatMessage[], ChatStatus, string]>([
    ["sending", [user("u1", "hi")], "sending", "Sending message."],
    [
      "complete",
      [user("u1", "hi"), assistant("a1", "ok", "complete")],
      "idle",
      "Reply complete.",
    ],
    [
      "interrupted",
      [user("u1", "hi"), assistant("a1", "ok", "interrupted")],
      "error",
      "Reply interrupted.",
    ],
    [
      "stopped",
      [user("u1", "hi"), assistant("a1", "ok", "stopped")],
      "idle",
      "Reply stopped.",
    ],
    ["nothing yet", [], "idle", ""],
  ])("announces %s", (_name, messages, status, expected) => {
    render(<MessageThread messages={messages} status={status} />);
    expect(statusRegion().textContent).toBe(expected);
  });
});

describe("MessageThread scrolling", () => {
  function setup(messages: ChatMessage[]) {
    const view = render(<MessageThread messages={messages} status="idle" />);
    const element = log();
    setMetrics(element, 1000, 400);
    return { ...view, element };
  }

  it("follows new text while the reader is at the end", () => {
    const base = [user("u1", "hi"), assistant("a1", "one", "streaming")];
    const { element, rerender } = setup(base);
    rerender(
      <MessageThread
        messages={[user("u1", "hi"), assistant("a1", "one two", "streaming")]}
        status="streaming"
      />,
    );
    expect(element.scrollTop).toBe(1000);
    expect(
      screen.queryByRole("button", { name: /jump to latest/i }),
    ).toBeNull();
  });

  it("stops following when the reader scrolls up and offers Jump to latest", () => {
    const { element, rerender } = setup([
      user("u1", "hi"),
      assistant("a1", "one", "streaming"),
    ]);
    element.scrollTop = 100;
    fireEvent.scroll(element);
    expect(
      screen.getByRole("button", { name: /jump to latest/i }),
    ).toBeInTheDocument();
    rerender(
      <MessageThread
        messages={[
          user("u1", "hi"),
          assistant("a1", "one two three", "streaming"),
        ]}
        status="streaming"
      />,
    );
    expect(element.scrollTop).toBe(100);
    expect(
      screen.getByRole("button", { name: /jump to latest/i }),
    ).toBeInTheDocument();
  });

  it("jumps smoothly to the end and resumes following", () => {
    const { element, rerender } = setup([
      user("u1", "hi"),
      assistant("a1", "one", "streaming"),
    ]);
    const scrollTo = vi.fn();
    element.scrollTo = scrollTo as unknown as typeof element.scrollTo;
    element.scrollTop = 100;
    fireEvent.scroll(element);
    fireEvent.click(screen.getByRole("button", { name: /jump to latest/i }));
    expect(scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: "smooth" });
    expect(
      screen.queryByRole("button", { name: /jump to latest/i }),
    ).toBeNull();
    element.scrollTop = 1000;
    rerender(
      <MessageThread
        messages={[user("u1", "hi"), assistant("a1", "one two", "streaming")]}
        status="streaming"
      />,
    );
    expect(element.scrollTop).toBe(1000);
  });

  it("jumps without smooth scrolling when the user prefers reduced motion", () => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("reduce"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia;
    const { element } = setup([
      user("u1", "hi"),
      assistant("a1", "one", "streaming"),
    ]);
    const scrollTo = vi.fn();
    element.scrollTo = scrollTo as unknown as typeof element.scrollTo;
    element.scrollTop = 100;
    fireEvent.scroll(element);
    fireEvent.click(screen.getByRole("button", { name: /jump to latest/i }));
    expect(scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: "auto" });
  });

  it("brings the view back to the end when the user sends a message", () => {
    const { element, rerender } = setup([
      user("u1", "hi"),
      assistant("a1", "reply", "complete"),
    ]);
    element.scrollTop = 100;
    fireEvent.scroll(element);
    expect(element.scrollTop).toBe(100);
    act(() => {
      rerender(
        <MessageThread
          messages={[
            user("u1", "hi"),
            assistant("a1", "reply", "complete"),
            user("u2", "next"),
          ]}
          status="sending"
        />,
      );
    });
    expect(element.scrollTop).toBe(1000);
  });

  it("falls back to setting scrollTop when scrollTo is unavailable", () => {
    const { element } = setup([
      user("u1", "hi"),
      assistant("a1", "one", "streaming"),
    ]);
    Object.defineProperty(element, "scrollTo", {
      configurable: true,
      value: undefined,
    });
    element.scrollTop = 100;
    fireEvent.scroll(element);
    fireEvent.click(screen.getByRole("button", { name: /jump to latest/i }));
    expect(element.scrollTop).toBe(1000);
  });
});
