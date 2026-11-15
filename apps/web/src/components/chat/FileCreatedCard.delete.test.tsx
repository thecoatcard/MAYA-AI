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
import { ApiError } from "../../lib/api-client";
import { FileCreatedCard } from "./FileCreatedCard";

const files = vi.hoisted(() => ({
  downloadChatFile: vi.fn(),
  deleteChatFile: vi.fn(),
  fetchChatFileBlob: vi.fn(),
}));
vi.mock("../../lib/files-client", async (original) => ({
  ...(await original<typeof import("../../lib/files-client")>()),
  ...files,
}));

const card: ChatFileCard = {
  fileId: "f1",
  name: "notes.md",
  fileType: "md",
  mimeType: "text/markdown",
  sizeBytes: 2048,
  action: "created",
  sessionId: "s1",
  messageId: "m1",
};

const renderCard = () =>
  render(
    <ul>
      <FileCreatedCard file={card} />
    </ul>,
  );

describe("FileCreatedCard deleted states", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("shows the deleted note instead of a Download that fails when the server says 410", async () => {
    files.downloadChatFile.mockRejectedValue(new ApiError(410, "file_deleted"));
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Download notes.md" }));
    const note = await screen.findByTestId("chat-file-card-deleted");
    expect(note).toHaveTextContent("This file was deleted.");
    expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("deletes after a confirmation that keeps the working-folder copy, then reads as deleted", async () => {
    files.deleteChatFile.mockResolvedValue(undefined);
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Delete notes.md" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Delete this file?",
    });
    expect(within(dialog).getByTestId("delete-keeps")).toHaveTextContent(
      "A copy saved in your working folder is not deleted.",
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete file" }),
    );
    await waitFor(() =>
      expect(files.deleteChatFile).toHaveBeenCalledWith("s1", "m1", "f1"),
    );
    expect(
      await screen.findByTestId("chat-file-card-deleted"),
    ).toBeInTheDocument();
  });

  it("keeps the card and says so when the delete fails", async () => {
    files.deleteChatFile.mockRejectedValue(new ApiError(500, "server_error"));
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Delete notes.md" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete file" }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "could not be deleted",
    );
    expect(screen.queryByTestId("chat-file-card-deleted")).toBeNull();
  });
});
