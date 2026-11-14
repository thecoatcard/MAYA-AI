import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatFileCard } from "../../hooks/useChatStream";
import { setAccessToken } from "../../lib/auth-session";
import { MessageItem } from "./MessageItem";
import { FileCreatedCard } from "./FileCreatedCard";
import { ToolProgressChip, toolProgressLabel } from "./ToolProgressChip";

const card: ChatFileCard = {
  fileId: "f1",
  name: "notes.md",
  fileType: "md",
  mimeType: "text/markdown",
  sizeBytes: 2048,
  action: "created",
  sessionId: "s/1",
  messageId: "m1",
};

function renderCard(file: ChatFileCard = card) {
  return render(
    <ul>
      <FileCreatedCard file={file} />
    </ul>,
  );
}

describe("FileCreatedCard", () => {
  beforeEach(() => {
    setAccessToken("test-token");
    Object.defineProperty(window.URL, "createObjectURL", {
      value: vi.fn(() => "blob:test"),
      configurable: true,
    });
    Object.defineProperty(window.URL, "revokeObjectURL", {
      value: vi.fn(),
      configurable: true,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    setAccessToken(null);
  });

  it("shows the real name, a type badge and the size", () => {
    renderCard();
    expect(screen.getByText("notes.md")).toBeInTheDocument();
    expect(screen.getByTestId("file-type-badge")).toHaveTextContent("MD");
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Download notes.md" }),
    ).toBeEnabled();
  });

  it("downloads through the authenticated chat file route and keeps the extension", async () => {
    const fetchMock = vi.fn(
      async () => new Response(new Blob(["# hi"]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    renderCard();
    let savedName = "";
    const append = document.body.appendChild.bind(document.body);
    vi.spyOn(document.body, "appendChild").mockImplementation((node) => {
      savedName = (node as HTMLAnchorElement).download;
      return append(node);
    });
    fireEvent.click(screen.getByRole("button", { name: "Download notes.md" }));

    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("/api/v1/chat/sessions/s%2F1/messages/m1/files/f1");
    expect(new Headers(init.headers).get("Authorization")).toBe(
      "Bearer test-token",
    );
    expect(savedName).toBe("notes.md");
  });

  it("shows a specific alert when the download fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "forbidden" }), {
            status: 403,
            headers: { "content-type": "application/json" },
          }),
      ),
    );
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Download notes.md" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You do not have permission to do that.",
    );
  });

  describe("a 404 while the reply is still being saved", () => {
    const notFound = () =>
      new Response(JSON.stringify({ error: "file_not_found" }), {
        status: 404,
        headers: { "content-type": "application/json" },
      });

    it("retries once and downloads without showing any error", async () => {
      const fetchMock = vi
        .fn()
        .mockImplementationOnce(async () => notFound())
        .mockImplementation(
          async () => new Response(new Blob(["# hi"]), { status: 200 }),
        );
      vi.stubGlobal("fetch", fetchMock);
      const click = vi
        .spyOn(HTMLAnchorElement.prototype, "click")
        .mockImplementation(() => undefined);
      renderCard();
      fireEvent.click(
        screen.getByRole("button", { name: "Download notes.md" }),
      );
      await waitFor(() => expect(click).toHaveBeenCalledTimes(1), {
        timeout: 4000,
      });
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.queryByTestId("chat-file-not-ready")).toBeNull();
    });

    it("then shows a calm message with a Retry button, never the red error", async () => {
      const fetchMock = vi.fn(async () => notFound());
      vi.stubGlobal("fetch", fetchMock);
      renderCard();
      fireEvent.click(
        screen.getByRole("button", { name: "Download notes.md" }),
      );
      const note = await screen.findByTestId(
        "chat-file-not-ready",
        {},
        { timeout: 4000 },
      );
      expect(note).toHaveTextContent("not ready yet");
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.queryByText("That item no longer exists.")).toBeNull();
      expect(within(note).getByRole("button", { name: "Retry" })).toBeEnabled();
    });
  });

  it("shows an authenticated inline preview for an image file", async () => {
    const fetchMock = vi.fn(
      async () => new Response(new Blob(["png"]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderCard({
      ...card,
      fileId: "img1",
      name: "chart.png",
      fileType: "png",
      mimeType: "image/png",
    });
    const img = await screen.findByTestId("chat-image-preview");
    expect(img).toHaveAttribute("alt", "Image: chart.png");
    expect(img).toHaveAttribute("src", "blob:test");
    const [imageUrl] = fetchMock.mock.calls[0] as unknown as [string];
    expect(imageUrl).toBe("/api/v1/chat/sessions/s%2F1/messages/m1/files/img1");
    expect(
      screen.getByRole("button", { name: "Download chart.png" }),
    ).toBeEnabled();
  });

  it("renders inside a reloaded assistant message", () => {
    const message = {
      key: "history-a1",
      id: "m1",
      role: "assistant",
      content: "Saved.",
      state: "complete",
      finishReason: "stop",
      usage: null,
      files: [card],
    };
    render(<MessageItem message={message as never} />);
    expect(screen.getByTestId("chat-file-card")).toBeInTheDocument();
    expect(
      screen.getByRole("list", { name: "Files made in this reply" }),
    ).toBeInTheDocument();
  });
});

describe("ToolProgressChip", () => {
  it("shows a label per running tool and nothing when none run", () => {
    const { rerender } = render(
      <ToolProgressChip
        tools={[{ callId: "c1", name: "create_file_inline" }]}
      />,
    );
    expect(screen.getByTestId("tool-progress-chip")).toHaveTextContent(
      "Creating a file",
    );
    rerender(<ToolProgressChip tools={[]} />);
    expect(screen.queryByTestId("tool-progress-chip")).toBeNull();
  });

  it("uses plain labels for known tools and a generic one otherwise", () => {
    expect(toolProgressLabel("edit_attached_file")).toBe("Editing a file");
    expect(toolProgressLabel("something_else")).toBe("Working on a tool");
  });
});
