import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../../hooks/useChatStream";
import type { SessionSummary } from "../../lib/api-client";
import { ApiError, isUnsupported } from "../../lib/api-client";
import { clampSelection, selectionPrompt } from "../../lib/menu-actions";
import {
  ConversationContext,
  ConversationRefContext,
  type ConversationActions,
  type ConversationRuntimeValue,
} from "./ConversationContext";
import { ConversationSidebar, groupSessions } from "./ConversationSidebar";
import { MessageItem } from "./MessageItem";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  usePathname: () => "/",
}));

function session(
  id: string,
  title: string,
  extra: Partial<SessionSummary> = {},
): SessionSummary {
  return {
    id,
    title,
    channel: "web",
    status: "active",
    created_at: "2026-11-01T00:00:00Z",
    ...extra,
  };
}

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
  over: Partial<ConversationRuntimeValue> & {
    list?: SessionSummary[];
    showArchived?: boolean;
  } = {},
): ConversationRuntimeValue {
  const { list = [], showArchived = false, ...rest } = over;
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
      list,
      loading: false,
      loaded: true,
      error: null,
      reload: vi.fn(),
      showArchived,
      setShowArchived: vi.fn(),
    },
    historyLoading: false,
    historyError: null,
    refreshApprovals: vi.fn(),
    title: "t",
    ...rest,
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

beforeEach(() => {
  push.mockClear();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
});

describe("groupSessions", () => {
  it("puts pinned first, archived last and sorts A to Z on request", () => {
    const list = [
      session("a", "Zeta"),
      session("b", "Beta", { pinned: true }),
      session("c", "Alpha", { archived: true }),
      session("d", "Eta"),
    ];
    const g = groupSessions(list, "", "recent");
    expect(g.pinned.map((s) => s.id)).toEqual(["b"]);
    expect(g.recent.map((s) => s.id)).toEqual(["a", "d"]);
    expect(g.archived.map((s) => s.id)).toEqual(["c"]);
    expect(groupSessions(list, "", "az").recent.map((s) => s.id)).toEqual([
      "d",
      "a",
    ]);
    expect(groupSessions(list, "alp", "recent").archived).toHaveLength(1);
  });
});

describe("conversation sidebar menus", () => {
  const list = [
    session("p", "Pinned one", { pinned: true }),
    session("r", "Recent one"),
    session("x", "Old one", { archived: true }),
  ];

  it("shows Pinned and Recent sections, and Archived only when asked", () => {
    const { rerender } = render(
      withRuntime(runtimeMock({ list }), <ConversationSidebar />),
    );
    expect(
      screen.getByRole("region", { name: "Pinned conversations" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Archived \(/)).toBeNull();
    rerender(
      withRuntime(
        runtimeMock({ list, showArchived: true }),
        <ConversationSidebar />,
      ),
    );
    const toggle = screen.getByRole("button", { name: /Archived \(1\)/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(screen.queryByText("Old one")).toBeNull();
  });

  it("right-click opens the row menu with every action", () => {
    render(withRuntime(runtimeMock({ list }), <ConversationSidebar />));
    fireEvent.contextMenu(screen.getByRole("button", { name: "Recent one" }));
    const menu = screen.getByRole("menu", { name: "Actions for Recent one" });
    for (const name of [
      /^Open/,
      /^Rename/,
      /^Pin/,
      /^Archive/,
      /^Duplicate/,
      /^Export as Markdown/,
      /^Copy title/,
      /^Delete/,
    ]) {
      expect(within(menu).getByRole("menuitem", { name })).toBeInTheDocument();
    }
  });

  it("Pin and Archive call the actions with the toggled value", () => {
    const rt = runtimeMock({ list });
    render(withRuntime(rt, <ConversationSidebar />));
    fireEvent.contextMenu(screen.getByRole("button", { name: "Recent one" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^Pin/ }));
    expect(rt.conversationActions?.setPinned).toHaveBeenCalledWith("r", true);
    fireEvent.contextMenu(screen.getByRole("button", { name: "Pinned one" }));
    expect(
      screen.getByRole("menuitem", { name: /^Unpin/ }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: /^Archive/ }));
    expect(rt.conversationActions?.setArchived).toHaveBeenCalledWith("p", true);
  });

  it("renames inline: Enter saves, Escape cancels, empty or over 120 chars is refused", () => {
    const rt = runtimeMock({ list });
    render(withRuntime(rt, <ConversationSidebar />));
    fireEvent.contextMenu(screen.getByRole("button", { name: "Recent one" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^Rename/ }));
    const field = screen.getByRole("textbox", { name: "Conversation name" });
    expect(field).toHaveFocus();
    fireEvent.change(field, { target: { value: "   " } });
    expect(field).toHaveAttribute("aria-invalid", "true");
    fireEvent.keyDown(field, { key: "Enter" });
    expect(rt.conversationActions?.rename).not.toHaveBeenCalled();
    fireEvent.change(field, { target: { value: "x".repeat(121) } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(rt.conversationActions?.rename).not.toHaveBeenCalled();
    fireEvent.change(field, { target: { value: "  New name " } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(rt.conversationActions?.rename).toHaveBeenCalledWith(
      "r",
      "New name",
    );
    expect(
      screen.queryByRole("textbox", { name: "Conversation name" }),
    ).toBeNull();

    fireEvent.contextMenu(screen.getByRole("button", { name: "Recent one" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^Rename/ }));
    const again = screen.getByRole("textbox", { name: "Conversation name" });
    fireEvent.change(again, { target: { value: "Nope" } });
    fireEvent.keyDown(again, { key: "Escape" });
    expect(rt.conversationActions?.rename).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole("button", { name: "Recent one" }),
    ).toBeInTheDocument();
  });

  it("Duplicate and Export call the actions; Copy title copies", () => {
    const rt = runtimeMock({ list });
    render(withRuntime(rt, <ConversationSidebar />));
    fireEvent.contextMenu(screen.getByRole("button", { name: "Recent one" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^Duplicate/ }));
    expect(rt.conversationActions?.duplicate).toHaveBeenCalledWith("r");
    fireEvent.contextMenu(screen.getByRole("button", { name: "Recent one" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^Export/ }));
    expect(rt.conversationActions?.exportMarkdown).toHaveBeenCalledWith(
      "r",
      "Recent one",
    );
    fireEvent.contextMenu(screen.getByRole("button", { name: "Recent one" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^Copy title/ }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("Recent one");
  });

  it("Delete asks first through the confirm dialog and only then deletes", async () => {
    const rt = runtimeMock({ list });
    render(withRuntime(rt, <ConversationSidebar />));
    fireEvent.contextMenu(screen.getByRole("button", { name: "Recent one" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^Delete/ }));
    expect(rt.deleteConversation).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete conversation" }),
    );
    expect(rt.deleteConversation).toHaveBeenCalledWith("r", false);
  });

  it("hides actions the server does not have (404 or 501) instead of failing", () => {
    const rt = runtimeMock({
      list,
      conversationActions: actionsMock({
        unsupported: new Set([
          "rename",
          "pin",
          "archive",
          "duplicate",
          "export",
        ]),
      }),
    });
    render(withRuntime(rt, <ConversationSidebar />));
    fireEvent.contextMenu(screen.getByRole("button", { name: "Recent one" }));
    expect(screen.queryByRole("menuitem", { name: /^Rename/ })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: /^Pin/ })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: /^Duplicate/ })).toBeNull();
    expect(
      screen.getByRole("menuitem", { name: /^Copy title/ }),
    ).toBeInTheDocument();
  });

  it("the blank area menu has New conversation, Show archived, Sort and Collapse", () => {
    const rt = runtimeMock({ list });
    render(withRuntime(rt, <ConversationSidebar />));
    fireEvent.contextMenu(screen.getByTestId("conversation-list"));
    fireEvent.click(screen.getByRole("menuitem", { name: /New conversation/ }));
    expect(rt.newConversation).toHaveBeenCalled();
    fireEvent.contextMenu(screen.getByTestId("conversation-list"));
    fireEvent.click(screen.getByRole("menuitem", { name: /Show archived/ }));
    expect(rt.sessions.setShowArchived).toHaveBeenCalledWith(true);
    fireEvent.contextMenu(screen.getByTestId("conversation-list"));
    const sort = screen.getByRole("menuitem", { name: /Sort by/ });
    fireEvent.keyDown(sort, { key: "ArrowRight" });
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "A to Z" }));
    // Choosing a submenu entry closes the menu; reopen for the last entry.
    const folded = vi.fn();
    window.addEventListener("maya:toggle-sidebar", folded);
    fireEvent.contextMenu(screen.getByTestId("conversation-list"));
    fireEvent.click(screen.getByRole("menuitem", { name: /Collapse sidebar/ }));
    expect(folded).toHaveBeenCalled();
    window.removeEventListener("maya:toggle-sidebar", folded);
  });

  it("keeps the browser menu on the search box and suppresses it on rows only", () => {
    render(withRuntime(runtimeMock({ list }), <ConversationSidebar />));
    expect(
      fireEvent.contextMenu(
        screen.getByPlaceholderText("Search conversations"),
      ),
    ).toBe(true);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(
      fireEvent.contextMenu(screen.getByRole("button", { name: "Recent one" })),
    ).toBe(false);
  });

  it("every row has a visible More actions button with aria-haspopup", () => {
    render(withRuntime(runtimeMock({ list }), <ConversationSidebar />));
    const button = screen.getByRole("button", {
      name: "More actions for Recent one",
    });
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    fireEvent.click(button);
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });
});

function reply(over: Partial<ChatMessage> = {}): ChatMessage {
  return {
    key: "k1",
    id: "m1",
    role: "assistant",
    content: "The answer is **42**.",
    state: "complete",
    finishReason: "stop",
    usage: null,
    ...over,
  };
}

describe("message menus", () => {
  it("assistant menu sends the file requests with source_message_id", () => {
    const rt = runtimeMock();
    render(withRuntime(rt, <MessageItem message={reply()} />));
    fireEvent.contextMenu(screen.getByText(/The answer is/));
    const file = screen.getByRole("menuitem", { name: /Make a file/ });
    fireEvent.keyDown(file, { key: "ArrowRight" });
    fireEvent.click(screen.getByRole("menuitem", { name: "PDF" }));
    expect(rt.sendText).toHaveBeenCalledWith("Make this a PDF.", {
      sourceMessageId: "m1",
    });
    fireEvent.contextMenu(screen.getByText(/The answer is/));
    fireEvent.click(screen.getByRole("menuitem", { name: /Save to Notion/ }));
    expect(rt.sendText).toHaveBeenCalledWith("Save this to Notion as it is.", {
      sourceMessageId: "m1",
    });
    fireEvent.contextMenu(screen.getByText(/The answer is/));
    fireEvent.click(screen.getByRole("menuitem", { name: /Send by email/ }));
    expect(rt.sendText).toHaveBeenCalledWith("Email this.", {
      sourceMessageId: "m1",
    });
  });

  it("disables the send actions with a reason until the reply is saved and complete", () => {
    render(
      withRuntime(
        runtimeMock(),
        <MessageItem message={reply({ id: null, state: "streaming" })} />,
      ),
    );
    fireEvent.contextMenu(screen.getByRole("article"));
    const notion = screen.getByRole("menuitem", { name: /Save to Notion/ });
    expect(notion).toHaveAttribute("aria-disabled", "true");
    expect(notion).toHaveTextContent(/Available once the reply/);
  });

  it("Good answer toggles, shows its state and rolls back when the server refuses", async () => {
    const feedback = vi
      .fn<(id: string, v: "up" | "down" | null) => Promise<boolean>>()
      .mockResolvedValueOnce(true)
      .mockResolvedValueOnce(true)
      .mockResolvedValueOnce(false);
    const rt = runtimeMock({
      conversationActions: actionsMock({ feedback }),
    });
    render(withRuntime(rt, <MessageItem message={reply()} />));
    const open = (): void => {
      fireEvent.contextMenu(screen.getByText(/The answer is/));
    };
    open();
    fireEvent.click(
      screen.getByRole("menuitemcheckbox", { name: /Good answer/ }),
    );
    expect(feedback).toHaveBeenLastCalledWith("m1", "up");
    expect(screen.getByTestId("feedback-state")).toHaveAccessibleName(
      "Marked as a good answer",
    );
    open();
    expect(
      screen.getByRole("menuitemcheckbox", { name: /Good answer/ }),
    ).toHaveAttribute("aria-checked", "true");
    fireEvent.click(
      screen.getByRole("menuitemcheckbox", { name: /Good answer/ }),
    );
    expect(feedback).toHaveBeenLastCalledWith("m1", null);
    await waitFor(() =>
      expect(screen.queryByTestId("feedback-state")).toBeNull(),
    );
    open();
    fireEvent.click(
      screen.getByRole("menuitemcheckbox", { name: /Bad answer/ }),
    );
    await waitFor(() => expect(feedback).toHaveBeenCalledTimes(3));
    await waitFor(() =>
      expect(screen.queryByTestId("feedback-state")).toBeNull(),
    );
  });

  it("hides feedback when the server has no such endpoint", () => {
    render(
      withRuntime(
        runtimeMock({
          conversationActions: actionsMock({
            unsupported: new Set(["feedback"]),
          }),
        }),
        <MessageItem message={reply()} />,
      ),
    );
    fireEvent.contextMenu(screen.getByText(/The answer is/));
    expect(
      screen.queryByRole("menuitemcheckbox", { name: /Good answer/ }),
    ).toBeNull();
  });

  it("Duplicate from here branches at this message", () => {
    const rt = runtimeMock();
    render(withRuntime(rt, <MessageItem message={reply()} />));
    fireEvent.contextMenu(screen.getByText(/The answer is/));
    fireEvent.click(
      screen.getByRole("menuitem", { name: /Duplicate from here/ }),
    );
    expect(rt.conversationActions?.duplicate).toHaveBeenCalledWith("s1", "m1");
  });

  it("copies the Markdown source", () => {
    render(withRuntime(runtimeMock(), <MessageItem message={reply()} />));
    fireEvent.contextMenu(screen.getByText(/The answer is/));
    fireEvent.click(screen.getByRole("menuitem", { name: /Copy as Markdown/ }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "The answer is **42**.",
    );
  });

  it("user message: Edit and resend fills the composer, Quote prefixes with >", () => {
    const rt = runtimeMock();
    render(
      withRuntime(
        rt,
        <MessageItem
          message={reply({ role: "user", content: "line one\nline two" })}
        />,
      ),
    );
    fireEvent.contextMenu(screen.getByText(/line one/));
    fireEvent.click(screen.getByRole("menuitem", { name: /Edit and resend/ }));
    expect(rt.prefillComposer).toHaveBeenCalledWith("line one\nline two");
    fireEvent.contextMenu(screen.getByText(/line one/));
    fireEvent.click(screen.getByRole("menuitem", { name: /Quote in reply/ }));
    expect(rt.prefillComposer).toHaveBeenLastCalledWith(
      "> line one\n> line two\n\n",
      { append: true },
    );
  });

  it("selected text opens the selection menu and sends a natural sentence, clamped", () => {
    const rt = runtimeMock();
    render(withRuntime(rt, <MessageItem message={reply()} />));
    const text = screen.getByText(/The answer is/);
    const range = document.createRange();
    range.selectNodeContents(text);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    fireEvent.contextMenu(text);
    expect(
      screen.getByRole("menuitem", { name: /Explain/ }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: /Explain/ }));
    expect(rt.sendText).toHaveBeenCalledWith(
      expect.stringContaining("Explain this:"),
    );
    // Not an executed action: only a chat message, and without source_message_id.
    expect(vi.mocked(rt.sendText).mock.calls[0]).toHaveLength(1);
    selection?.removeAllRanges();
  });

  it("Translate has a Hindi and English submenu", () => {
    const rt = runtimeMock();
    render(withRuntime(rt, <MessageItem message={reply()} />));
    const text = screen.getByText(/The answer is/);
    const range = document.createRange();
    range.selectNodeContents(text);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);
    fireEvent.contextMenu(text);
    fireEvent.keyDown(screen.getByRole("menuitem", { name: /Translate/ }), {
      key: "ArrowRight",
    });
    fireEvent.click(screen.getByRole("menuitem", { name: "Hindi" }));
    expect(rt.sendText).toHaveBeenCalledWith(
      expect.stringContaining("Translate this to Hindi:"),
    );
    window.getSelection()?.removeAllRanges();
  });

  it("clamps a long selection to 2000 characters", () => {
    const long = "a".repeat(5000);
    expect(Array.from(clampSelection(long))).toHaveLength(2001);
    expect(selectionPrompt("summarize", long).length).toBeLessThan(2100);
    expect(clampSelection("  hi  ")).toBe("hi");
  });

  it("link and citation menus offer Open and Copy link", () => {
    render(
      withRuntime(
        runtimeMock(),
        <MessageItem
          message={reply({
            content: "See [the docs](https://example.com/a) now.",
          })}
        />,
      ),
    );
    fireEvent.contextMenu(screen.getByRole("link", { name: /the docs/ }));
    expect(
      screen.getByRole("menuitem", { name: /Open link/ }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: /Copy link/ }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "https://example.com/a",
    );
  });

  it("works outside the runtime: Copy stays, send actions are not offered", () => {
    render(<MessageItem message={reply()} />);
    fireEvent.contextMenu(screen.getByText(/The answer is/));
    expect(
      screen.getByRole("menuitem", { name: /^Copy$/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /Make a file/ })).toBeNull();
  });

  it("opens from the visible More reply actions button", () => {
    render(withRuntime(runtimeMock(), <MessageItem message={reply()} />));
    const button = screen.getByRole("button", { name: "More reply actions" });
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    act(() => button.focus());
    fireEvent.click(button);
    expect(
      screen.getByRole("menu", { name: "Reply actions" }),
    ).toBeInTheDocument();
  });
});

describe("api contract helpers", () => {
  it("treats 404 and 501 as unsupported, other errors not", () => {
    expect(isUnsupported(new ApiError(404, "not_found"))).toBe(true);
    expect(isUnsupported(new ApiError(501, "x"))).toBe(true);
    expect(isUnsupported(new ApiError(500, "x"))).toBe(false);
    expect(isUnsupported(new Error("x"))).toBe(false);
  });
});
