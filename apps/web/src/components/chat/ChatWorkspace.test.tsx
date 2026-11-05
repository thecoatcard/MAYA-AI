import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken } from "../../lib/auth-session";
import {
  acceptedFrame,
  controllableStream,
  deltaFrame,
  doneFrame,
  errorFrame,
  jsonResponse,
  sseResponse,
} from "./chat-test-utils";
import { ChatWorkspace } from "./ChatWorkspace";
import { THEME_STORAGE_KEY } from "./ThemeToggle";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

function box(): HTMLTextAreaElement {
  return screen.getByRole("textbox", {
    name: "Message",
  }) as HTMLTextAreaElement;
}

async function typeAndSend(text: string) {
  fireEvent.change(box(), { target: { value: text } });
  await act(async () => {
    fireEvent.keyDown(box(), { key: "Enter" });
  });
}

describe("not signed in", () => {
  it("says sign-in is not built yet and offers nothing that pretends otherwise", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<ChatWorkspace />);
    expect(
      screen.getByRole("heading", {
        name: "Sign-in is not built yet",
        level: 2,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Planned")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(
      screen.queryByRole("button", { name: /send|sign in|log in/i }),
    ).toBeNull();
    expect(
      screen.getByRole("link", { name: "Back to overview" }),
    ).toHaveAttribute("href", "/");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("has a page heading and landmarks", () => {
    render(<ChatWorkspace />);
    expect(
      screen.getByRole("heading", { name: "Chat", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
  });

  it("does not call the API when rendered (nothing happens at build or load time)", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("some-token-value");
    render(<ChatWorkspace />);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("signed in", () => {
  beforeEach(() => {
    setAccessToken("workspace-test-token");
  });

  it("shows an honest empty state with the composer", () => {
    render(<ChatWorkspace />);
    expect(
      screen.getByRole("heading", { name: "Start a conversation" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/not available yet/i)).toBeInTheDocument();
    expect(box()).toBeEnabled();
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });

  it("appears when the token is set after the page loaded, and goes away when cleared", async () => {
    setAccessToken(null);
    render(<ChatWorkspace />);
    expect(screen.queryByRole("textbox")).toBeNull();
    act(() => setAccessToken("late-token-value"));
    expect(box()).toBeInTheDocument();
    act(() => setAccessToken(null));
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByText("Sign-in is not built yet")).toBeInTheDocument();
  });

  it("sends a message and streams the reply into the thread", async () => {
    const fetchMock = vi.fn(async () =>
      sseResponse([
        acceptedFrame(),
        deltaFrame("Here is "),
        deltaFrame("**the** answer."),
        doneFrame(),
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<ChatWorkspace />);
    await typeAndSend("What is the plan?");
    await waitFor(() =>
      expect(
        container.querySelector(".maya-msg--assistant strong"),
      ).not.toBeNull(),
    );
    expect(container.querySelector(".maya-msg--user")?.textContent).toContain(
      "What is the plan?",
    );
    expect(
      container.querySelector(".maya-msg--assistant")?.textContent,
    ).toContain("Here is the answer.");
    expect(box().value).toBe("");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeInTheDocument(),
    );
    const [, init] = (
      fetchMock.mock.calls as unknown as Array<[string, RequestInit]>
    )[0];
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer workspace-test-token",
    );
  });

  it("shows Stop while the reply streams and stops the request", async () => {
    const controlled = controllableStream();
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      void init;
      return sseResponse(controlled.stream);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ChatWorkspace />);
    await typeAndSend("Long question");
    controlled.push(acceptedFrame());
    controlled.push(deltaFrame("Writing"));
    const stop = await screen.findByRole("button", { name: "Stop generating" });
    await waitFor(() =>
      expect(screen.getByText(/Writing/)).toBeInTheDocument(),
    );
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(init.signal?.aborted).toBe(false);
    fireEvent.click(stop);
    expect(init.signal?.aborted).toBe(true);
    await waitFor(() => expect(controlled.cancelled).toBe(true));
    expect(
      screen.getByRole("button", { name: "Send message" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Stopped. The text above is what arrived."),
    ).toBeInTheDocument();
    expect(document.activeElement).toBe(box());
  });

  it("offers only Ask again after an error event, and asks again with a new key", async () => {
    const calls: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        calls.push(init ?? {});
        return calls.length === 1
          ? sseResponse([
              acceptedFrame(),
              deltaFrame("Par"),
              errorFrame("stream_interrupted", true),
            ])
          : sseResponse([
              acceptedFrame(),
              deltaFrame("Whole reply"),
              doneFrame(),
            ]);
      }),
    );
    render(<ChatWorkspace />);
    await typeAndSend("Hello");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The reply was interrupted.");
    // The server stored the failure under the key: a same-key Retry is useless.
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    // One Ask again only: the inline one is hidden while the notice offers it.
    const askAgain = screen.getAllByRole("button", { name: "Ask again" });
    expect(askAgain).toHaveLength(1);
    await act(async () => {
      fireEvent.click(askAgain[0]);
    });
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(calls).toHaveLength(2);
    expect(
      (calls[1].headers as Record<string, string>)["Idempotency-Key"],
    ).not.toBe((calls[0].headers as Record<string, string>)["Idempotency-Key"]);
    expect(JSON.parse(String(calls[1].body)).content).toBe("Hello");
    expect(screen.getByText("Whole reply")).toBeInTheDocument();
  });

  it("retries an unknown outcome with the same key and replaces the partial text", async () => {
    const calls: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        calls.push(init ?? {});
        return calls.length === 1
          ? new Response("<html>Bad Gateway</html>", {
              status: 502,
              headers: { "Content-Type": "text/html" },
            })
          : sseResponse([
              acceptedFrame({ replayed: true }),
              deltaFrame("Stored reply"),
              doneFrame(),
            ]);
      }),
    );
    render(<ChatWorkspace />);
    await typeAndSend("Hello");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Try again.");
    // The proxy answered, so nobody knows whether the server has the message.
    expect(
      screen.getByText(
        "Not confirmed. The server may have received this message.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Not sent")).toBeNull();
    const retry = screen.getByRole("button", { name: "Retry" });
    expect(retry).toHaveClass("maya-btn--primary");
    await act(async () => {
      fireEvent.click(retry);
    });
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(calls).toHaveLength(2);
    expect(
      (calls[1].headers as Record<string, string>)["Idempotency-Key"],
    ).toBe((calls[0].headers as Record<string, string>)["Idempotency-Key"]);
    expect(screen.getByText("Stored reply")).toBeInTheDocument();
    expect(screen.queryByText(/Not confirmed/)).toBeNull();
  });

  it("restores the text to the composer when the server refuses a message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ error: "validation_error", fields: ["content"] }, 422),
      ),
    );
    render(<ChatWorkspace />);
    await typeAndSend("Keep my words");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The server rejected this message.");
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
    expect(screen.getByText("Not sent")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(box().value).toBe("Keep my words");
    expect(screen.queryByText("Not sent")).toBeNull();
  });

  it("shows the Retry-After wait for a rate limit and counts it down beside a disabled Retry", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          jsonResponse({ error: "rate_limited" }, 429, { "Retry-After": "3" }),
        ),
      );
      render(<ChatWorkspace />);
      await typeAndSend("Hi");
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(
        "You have reached the message rate limit. Try again in about 3 seconds.",
      );
      // The accessible name never changes; the countdown is visual text.
      const retry = screen.getByRole("button", { name: "Retry" });
      expect(retry).toBeDisabled();
      expect(screen.getByText("Available in 3s")).toHaveAttribute(
        "aria-hidden",
        "true",
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100);
      });
      expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
      expect(screen.queryByText(/Available in/)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("starts a new conversation and focuses the composer", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([acceptedFrame(), deltaFrame("Reply"), doneFrame()]),
      ),
    );
    render(<ChatWorkspace />);
    await typeAndSend("First");
    await screen.findByText("Reply");
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByText("Reply")).toBeNull();
    expect(
      screen.getByRole("heading", { name: "Start a conversation" }),
    ).toBeInTheDocument();
    expect(document.activeElement).toBe(box());
  });

  it("clears the conversation when the token disappears mid-session", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([acceptedFrame(), deltaFrame("Reply"), doneFrame()]),
      ),
    );
    render(<ChatWorkspace />);
    await typeAndSend("First");
    await screen.findByText("Reply");
    act(() => setAccessToken(null));
    expect(screen.queryByText("Reply")).toBeNull();
    act(() => setAccessToken("another-token-value"));
    expect(
      screen.getByRole("heading", { name: "Start a conversation" }),
    ).toBeInTheDocument();
  });
});

describe("focus after a notice action", () => {
  beforeEach(() => {
    setAccessToken("workspace-test-token");
  });

  function proxyDown() {
    return new Response("<html>Bad Gateway</html>", {
      status: 502,
      headers: { "Content-Type": "text/html" },
    });
  }

  it("moves focus to the composer after Retry", async () => {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        call += 1;
        return call === 1
          ? proxyDown()
          : sseResponse([acceptedFrame(), deltaFrame("ok"), doneFrame()]);
      }),
    );
    render(<ChatWorkspace />);
    await typeAndSend("Hello");
    const retry = await screen.findByRole("button", { name: "Retry" });
    retry.focus();
    expect(document.activeElement).toBe(retry);
    await act(async () => {
      fireEvent.click(retry);
    });
    await screen.findByText("ok");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(document.activeElement).toBe(box());
  });

  it("moves focus to the composer after Ask again in the notice", async () => {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        call += 1;
        return call === 1
          ? sseResponse([acceptedFrame(), errorFrame("internal_error", true)])
          : sseResponse([acceptedFrame(), deltaFrame("fine"), doneFrame()]);
      }),
    );
    render(<ChatWorkspace />);
    await typeAndSend("Hello");
    const askAgain = await screen.findByRole("button", { name: "Ask again" });
    askAgain.focus();
    await act(async () => {
      fireEvent.click(askAgain);
    });
    await screen.findByText("fine");
    expect(document.activeElement).toBe(box());
  });

  it("moves focus to the composer after Dismiss", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ error: "validation_error", fields: ["content"] }, 422),
      ),
    );
    render(<ChatWorkspace />);
    await typeAndSend("Edit me");
    const dismiss = await screen.findByRole("button", { name: "Dismiss" });
    dismiss.focus();
    expect(document.activeElement).toBe(dismiss);
    fireEvent.click(dismiss);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.activeElement).toBe(box());
  });

  it("moves focus to the composer after the inline Ask again", async () => {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        call += 1;
        return call === 1
          ? sseResponse([
              acceptedFrame(),
              deltaFrame("cut"),
              errorFrame("stream_interrupted", true),
            ])
          : sseResponse([acceptedFrame(), deltaFrame("whole"), doneFrame()]);
      }),
    );
    render(<ChatWorkspace />);
    await typeAndSend("Hello");
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    const inline = screen.getByRole("button", { name: "Ask again" });
    inline.focus();
    await act(async () => {
      fireEvent.click(inline);
    });
    await screen.findByText("whole");
    expect(document.activeElement).toBe(box());
  });
});

describe("Ask again after an interrupted reply", () => {
  beforeEach(() => {
    setAccessToken("workspace-test-token");
  });

  it("is offered inline after an automatic retry ended in an interrupted replay", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const calls: RequestInit[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn(async (_url: string, init?: RequestInit) => {
          calls.push(init ?? {});
          if (calls.length === 1) {
            // Ends without done or error: the client retries on its own.
            return sseResponse([acceptedFrame(), deltaFrame("Half")]);
          }
          if (calls.length === 2) {
            return sseResponse([
              acceptedFrame({ replayed: true }),
              deltaFrame("Half an answer"),
              doneFrame("interrupted"),
            ]);
          }
          return sseResponse([
            acceptedFrame(),
            deltaFrame("Full answer"),
            doneFrame(),
          ]);
        }),
      );
      render(<ChatWorkspace />);
      await typeAndSend("Question");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      // No error notice, yet the interrupted reply can be asked again.
      expect(screen.queryByRole("alert")).toBeNull();
      expect(
        screen.getByText("Reply interrupted. The text above is what arrived."),
      ).toBeInTheDocument();
      expect(screen.getByText("Half an answer")).toBeInTheDocument();
      const askAgain = screen.getByRole("button", { name: "Ask again" });
      await act(async () => {
        fireEvent.click(askAgain);
        await vi.advanceTimersByTimeAsync(100);
      });
      expect(calls).toHaveLength(3);
      expect(
        (calls[2].headers as Record<string, string>)["Idempotency-Key"],
      ).not.toBe(
        (calls[0].headers as Record<string, string>)["Idempotency-Key"],
      );
      expect(JSON.parse(String(calls[2].body)).content).toBe("Question");
      expect(screen.getByText("Full answer")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Ask again" })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows model_unavailable with no text as Ask again only, never a same-key Retry", async () => {
    const calls: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        calls.push(init ?? {});
        return sseResponse([
          acceptedFrame(),
          errorFrame("model_unavailable", true),
        ]);
      }),
    );
    render(<ChatWorkspace />);
    await typeAndSend("Hello");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The model is not available right now.");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(
      screen.getByText("Reply interrupted before any text arrived."),
    ).toBeInTheDocument();
    const askAgain = screen.getByRole("button", { name: "Ask again" });
    expect(askAgain).toHaveClass("maya-btn--primary");
    await act(async () => {
      fireEvent.click(askAgain);
    });
    // A new request with a new key; the old one was never repeated.
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(
      (calls[1].headers as Record<string, string>)["Idempotency-Key"],
    ).not.toBe((calls[0].headers as Record<string, string>)["Idempotency-Key"]);
  });

  it("stays available inline after the notice is dismissed, and only then", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          acceptedFrame(),
          deltaFrame("cut"),
          errorFrame("model_unavailable", true),
        ]),
      ),
    );
    render(<ChatWorkspace />);
    await typeAndSend("Hello");
    await screen.findByRole("alert");
    expect(screen.getAllByRole("button", { name: "Ask again" })).toHaveLength(
      1,
    );
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
    const inline = screen.getAllByRole("button", { name: "Ask again" });
    expect(inline).toHaveLength(1);
    // The inline action lives inside the interrupted note.
    expect(inline[0].closest(".maya-msg__note")).not.toBeNull();
  });

  it("is not offered once a new message has been sent", async () => {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        call += 1;
        return call === 1
          ? sseResponse([
              acceptedFrame(),
              deltaFrame("cut"),
              errorFrame("stream_interrupted", false),
            ])
          : sseResponse([acceptedFrame(), deltaFrame("next"), doneFrame()]);
      }),
    );
    render(<ChatWorkspace />);
    await typeAndSend("One");
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    await typeAndSend("Two");
    await screen.findByText("next");
    expect(screen.queryByRole("button", { name: "Ask again" })).toBeNull();
  });
});

describe("a message that may have been received", () => {
  beforeEach(() => {
    setAccessToken("workspace-test-token");
  });

  it("keeps the bubble and does not put the text back when the error is dismissed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("<html>Gateway Timeout</html>", {
            status: 504,
            headers: { "Content-Type": "text/html" },
          }),
      ),
    );
    const { container } = render(<ChatWorkspace />);
    await typeAndSend("Send me once");
    await screen.findByRole("alert");
    expect(
      screen.getByText(
        "Not confirmed. The server may have received this message.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
    // Still in the thread, still labelled, and not offered again in the composer.
    expect(container.querySelector(".maya-msg--user")?.textContent).toContain(
      "Send me once",
    );
    expect(
      screen.getByText(
        "Not confirmed. The server may have received this message.",
      ),
    ).toBeInTheDocument();
    expect(box().value).toBe("");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("leads with Retry for a lost connection and labels the message Not confirmed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          throw new TypeError("Failed to fetch");
        }),
      );
      render(<ChatWorkspace />);
      await typeAndSend("Hello");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("The connection to the server was lost.");
      expect(alert).toHaveTextContent("will not send the message twice");
      expect(screen.getByRole("button", { name: "Retry" })).toHaveClass(
        "maya-btn--primary",
      );
      expect(screen.getByText(/Not confirmed/)).toBeInTheDocument();
      expect(screen.queryByText("Not sent")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("a different sign-in", () => {
  it("clears the conversation, the draft and the request when the token changes to another token", async () => {
    setAccessToken("token-of-user-a");
    const controlled = controllableStream();
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      void init;
      return sseResponse(controlled.stream);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ChatWorkspace />);
    await typeAndSend("Private question from A");
    controlled.push(acceptedFrame());
    controlled.push(deltaFrame("Private answer for A"));
    await screen.findByText(/Private answer for A/);
    fireEvent.change(box(), { target: { value: "unsent draft of A" } });
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(init.signal?.aborted).toBe(false);
    // Straight from one token to another: signed in before and after.
    act(() => setAccessToken("token-of-user-b"));
    expect(init.signal?.aborted).toBe(true);
    expect(screen.queryByText(/Private answer for A/)).toBeNull();
    expect(screen.queryByText("Private question from A")).toBeNull();
    expect(
      screen.getByRole("heading", { name: "Start a conversation" }),
    ).toBeInTheDocument();
    expect(box().value).toBe("");
  });

  it("does nothing when the same token is set again", async () => {
    setAccessToken("steady-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([acceptedFrame(), deltaFrame("Kept"), doneFrame()]),
      ),
    );
    render(<ChatWorkspace />);
    await typeAndSend("Hello");
    await screen.findByText("Kept");
    act(() => setAccessToken("steady-token"));
    expect(screen.getByText("Kept")).toBeInTheDocument();
  });
});

describe("token handling", () => {
  it("never writes the token to localStorage, sessionStorage or cookies", async () => {
    setAccessToken("super-secret-token-value");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([acceptedFrame(), deltaFrame("ok"), doneFrame()]),
      ),
    );
    render(<ChatWorkspace />);
    await typeAndSend("Hi");
    await screen.findByText("ok");
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(
      "super-secret-token-value",
    );
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(
      "super-secret-token-value",
    );
    expect(document.cookie).not.toContain("super-secret-token-value");
    expect(document.body.innerHTML).not.toContain("super-secret-token-value");
  });
});

describe("theme", () => {
  it("follows the system by default and lets the user choose, remembering the choice", () => {
    const { container } = render(<ChatWorkspace />);
    const root = container.querySelector(".maya-chat");
    expect(root).not.toHaveAttribute("data-maya-theme");
    expect(
      screen.getByRole("button", { name: "Follow system theme" }),
    ).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Dark theme" }));
    expect(root).toHaveAttribute("data-maya-theme", "dark");
    expect(screen.getByRole("button", { name: "Dark theme" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: "Light theme" }));
    expect(root).toHaveAttribute("data-maya-theme", "light");
    fireEvent.click(
      screen.getByRole("button", { name: "Follow system theme" }),
    );
    expect(root).not.toHaveAttribute("data-maya-theme");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it("restores a stored choice after load", async () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    const { container } = render(<ChatWorkspace />);
    await waitFor(() =>
      expect(container.querySelector(".maya-chat")).toHaveAttribute(
        "data-maya-theme",
        "light",
      ),
    );
  });

  it("still works when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { container } = render(<ChatWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: "Dark theme" }));
    expect(container.querySelector(".maya-chat")).toHaveAttribute(
      "data-maya-theme",
      "dark",
    );
  });
});
