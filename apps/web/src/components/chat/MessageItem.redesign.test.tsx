import { act, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../../hooks/useChatStream";
import {
  ConversationContext,
  ConversationRefContext,
  type ConversationActions,
  type ConversationRuntimeValue,
} from "./ConversationContext";
import { MessageItem } from "./MessageItem";
import { ThreadList } from "./ThreadList";
import type { ThreadItem } from "./threadItems";

function actionsMock(
  over: Partial<ConversationActions> = {},
): ConversationActions {
  return {
    unsupported: new Set<string>(),
    rename: vi.fn().mockResolvedValue(true),
    setPinned: vi.fn().mockResolvedValue(true),
    setArchived: vi.fn().mockResolvedValue(true),
    duplicate: vi.fn().mockResolvedValue(true),
    exportMarkdown: vi.fn().mockResolvedValue(true),
    feedback: vi.fn().mockResolvedValue(true),
    ...over,
  };
}

function runtimeMock(
  over: Partial<ConversationRuntimeValue> = {},
): ConversationRuntimeValue {
  return {
    chat: { sessionId: "s1" },
    voice: {},
    voiceLive: false,
    items: [],
    toggleVoice: vi.fn(),
    sendText: vi.fn().mockResolvedValue(undefined),
    runAsTask: vi.fn(),
    trackTask: vi.fn(),
    newConversation: vi.fn(),
    openConversation: vi.fn().mockResolvedValue(undefined),
    deleteConversation: vi.fn().mockResolvedValue(undefined),
    conversationActions: actionsMock(),
    prefillComposer: vi.fn(),
    sessions: {
      list: [],
      loading: false,
      loaded: true,
      error: null,
      reload: vi.fn(),
      showArchived: false,
      setShowArchived: vi.fn(),
    },
    historyLoading: false,
    historyError: null,
    refreshApprovals: vi.fn(),
    title: "Test Session",
    ...over,
  } as unknown as ConversationRuntimeValue;
}

function withRuntime(
  runtime: ConversationRuntimeValue,
  ui: React.ReactNode,
): React.JSX.Element {
  return (
    <ConversationContext.Provider value={runtime}>
      <ConversationRefContext.Provider value={{ current: runtime }}>
        {ui}
      </ConversationRefContext.Provider>
    </ConversationContext.Provider>
  );
}

const writeTextMock = vi.fn().mockResolvedValue(undefined);

beforeEach(() => {
  writeTextMock.mockClear();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: writeTextMock },
    configurable: true,
  });
});

describe("MessageItem redesign: User messages", () => {
  it("renders a polished user bubble with tailored container, metadata timestamp, and action toolbar", async () => {
    const runtime = runtimeMock();
    const userMsg: ChatMessage & { createdAt: number } = {
      key: "user-1",
      id: "u1",
      role: "user",
      content: "Can you analyze this project?",
      state: "sent",
      finishReason: null,
      usage: null,
      createdAt: new Date("2026-11-14T10:30:00Z").getTime(),
    };

    render(withRuntime(runtime, <MessageItem message={userMsg} />));

    const bubble = screen.getByRole("article");
    expect(bubble.className).toContain("maya-msg--user");
    expect(bubble.className).toContain("motion-message");

    // Clean metadata timestamp
    const timeEl = screen.getByText(/10:30|AM|PM/i);
    expect(timeEl.tagName.toLowerCase()).toBe("time");
    expect(timeEl.className).toContain("maya-msg__time");

    // Sleek action toolbar pills
    const copyBtn = screen.getByRole("button", { name: "Copy message" });
    const quoteBtn = screen.getByRole("button", { name: "Quote in reply" });
    expect(copyBtn).toBeTruthy();
    expect(quoteBtn).toBeTruthy();

    // Copy micro-interaction
    await act(async () => {
      fireEvent.click(copyBtn);
    });
    expect(writeTextMock).toHaveBeenCalledWith("Can you analyze this project?");

    // Quote in reply micro-interaction
    fireEvent.click(quoteBtn);
    expect(runtime.prefillComposer).toHaveBeenCalledWith(
      expect.stringContaining("Can you analyze this project?"),
      { append: true },
    );
  });
});

describe("MessageItem redesign: MAYA replies", () => {
  it("renders a calm, airy layout with brand accent pip, who label, and rich action toolbar", async () => {
    const feedbackFn = vi.fn().mockResolvedValue(true);
    const runtime = runtimeMock({
      conversationActions: actionsMock({ feedback: feedbackFn }),
    });

    const mayaMsg: ChatMessage = {
      key: "maya-1",
      id: "m1",
      role: "assistant",
      content: "Here is your plan:\n\n1. Design\n2. Build\n3. Verify",
      state: "complete",
      finishReason: "stop",
      usage: null,
    };

    const onAskAgain = vi.fn();

    render(
      withRuntime(
        runtime,
        <MessageItem message={mayaMsg} onAskAgain={onAskAgain} />,
      ),
    );

    const article = screen.getByRole("article");
    expect(article.className).toContain("maya-msg--assistant");
    expect(article.className).toContain("motion-message");

    // Brand accent pip & who label
    expect(article.querySelector(".maya-msg__mark")).toBeTruthy();
    expect(screen.getByText("MAYA")).toBeTruthy();

    // Action pills
    const copyPill = screen.getByRole("button", { name: "Copy reply" });
    const quotePill = screen.getByRole("button", { name: "Quote in reply" });
    const retryPill = screen.getByRole("button", { name: "Ask again" });
    const goodPill = screen.getByRole("button", { name: "Good answer" });
    const badPill = screen.getByRole("button", { name: "Bad answer" });

    expect(copyPill).toBeTruthy();
    expect(quotePill).toBeTruthy();
    expect(retryPill).toBeTruthy();
    expect(goodPill).toBeTruthy();
    expect(badPill).toBeTruthy();

    // Copy micro-interaction with feedback transition
    await act(async () => {
      fireEvent.click(copyPill);
    });
    expect(writeTextMock).toHaveBeenCalledWith(
      expect.stringContaining("Here is your plan:"),
    );

    // Quote micro-interaction
    fireEvent.click(quotePill);
    expect(runtime.prefillComposer).toHaveBeenCalledWith(
      expect.stringContaining("Here is your plan:"),
      { append: true },
    );

    // Retry micro-interaction
    fireEvent.click(retryPill);
    expect(onAskAgain).toHaveBeenCalled();

    // Feedback rating micro-interaction
    await act(async () => {
      fireEvent.click(goodPill);
    });
    expect(feedbackFn).toHaveBeenCalledWith("m1", "up");
    expect(goodPill).toHaveAttribute("aria-pressed", "true");
  });

  it("shows smooth pending indicator when streaming with no text yet", () => {
    const runtime = runtimeMock();
    const streamingMsg: ChatMessage = {
      key: "maya-stream",
      id: "m2",
      role: "assistant",
      content: "",
      state: "streaming",
      finishReason: null,
      usage: null,
    };

    render(withRuntime(runtime, <MessageItem message={streamingMsg} />));

    expect(screen.getByText("Thinking")).toBeTruthy();
    const dots = screen
      .getByText("Thinking")
      .parentElement?.querySelector(".maya-dots");
    expect(dots).toBeTruthy();
  });
});

describe("ThreadList redesign: Scroll-to-bottom and unread badge", () => {
  it("shows floating jump button with unread count badge when scrolled up and new messages arrive", async () => {
    const initialItem: ThreadItem = {
      kind: "message",
      id: "msg-1",
      ts: 1,
      spoken: false,
      message: {
        key: "k1",
        id: "m1",
        role: "user",
        content: "First question",
        state: "sent",
        finishReason: null,
        usage: null,
        createdAt: 1000,
      },
    };

    const { rerender } = render(
      <ThreadList items={[initialItem]} status="idle" />,
    );

    const scrollPane = screen.getByRole("log", { name: "Conversation" });
    const scrollToMock = vi.fn();
    scrollPane.scrollTo = scrollToMock;

    // Simulate user scrolled up
    Object.defineProperty(scrollPane, "scrollHeight", {
      value: 1000,
      configurable: true,
    });
    Object.defineProperty(scrollPane, "scrollTop", {
      value: 200,
      configurable: true,
    });
    Object.defineProperty(scrollPane, "clientHeight", {
      value: 400,
      configurable: true,
    });

    fireEvent.scroll(scrollPane);

    // Jump button is visible
    expect(
      screen.getByRole("button", { name: /jump to latest/i }),
    ).toBeTruthy();

    // A second message arrives while user is scrolled up
    const secondItem: ThreadItem = {
      kind: "message",
      id: "msg-2",
      ts: 2,
      spoken: false,
      message: {
        key: "k2",
        id: "m2",
        role: "assistant",
        content: "Second reply",
        state: "complete",
        finishReason: "stop",
        usage: null,
        createdAt: 2000,
      },
    };

    rerender(<ThreadList items={[initialItem, secondItem]} status="idle" />);

    // Unread count badge is displayed!
    const badge = screen.getByTestId("jump-unread-badge");
    expect(badge).toBeTruthy();
    expect(badge.textContent).toBe("1");
    expect(
      screen.getByRole("button", { name: /jump to latest \(1 unread\)/i }),
    ).toBeTruthy();

    // Clicking jump scrolls smoothly and clears unread count
    fireEvent.click(screen.getByRole("button", { name: /jump to latest/i }));
    expect(scrollToMock).toHaveBeenCalledWith(
      expect.objectContaining({ top: 1000, behavior: "smooth" }),
    );
  });
});
