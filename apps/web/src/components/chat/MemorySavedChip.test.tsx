import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../lib/api-client";
import { MemorySavedChip } from "./MemorySavedChip";
import { MessageItem } from "./MessageItem";
import type { ChatMessage } from "../../hooks/useChatStream";

const memory = {
  memoryId: "mem-1",
  text: "Favourite colour is blue",
  updated: false,
};

describe("MemorySavedChip", () => {
  it("shows what was saved with an Undo button", () => {
    render(<MemorySavedChip memory={memory} onUndo={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Saved to memory · Favourite colour is blue",
    );
    expect(screen.getByRole("button", { name: /undo/i })).toBeEnabled();
  });

  it("says when an existing memory was updated", () => {
    render(
      <MemorySavedChip
        memory={{ ...memory, updated: true }}
        onUndo={vi.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Memory updated");
  });

  it("Undo deletes the memory by id and then says it was removed", async () => {
    const onUndo = vi.fn().mockResolvedValue(undefined);
    render(<MemorySavedChip memory={memory} onUndo={onUndo} />);
    fireEvent.click(screen.getByRole("button", { name: /undo/i }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Removed from memory.",
      ),
    );
    expect(onUndo).toHaveBeenCalledWith("mem-1");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("treats a 404 as already removed", async () => {
    const onUndo = vi
      .fn()
      .mockRejectedValue(new ApiError(404, "unknown_error"));
    render(<MemorySavedChip memory={memory} onUndo={onUndo} />);
    fireEvent.click(screen.getByRole("button", { name: /undo/i }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Removed from memory.",
      ),
    );
  });

  it("keeps the chip and says so when the delete fails", async () => {
    const onUndo = vi.fn().mockRejectedValue(new Error("network"));
    render(<MemorySavedChip memory={memory} onUndo={onUndo} />);
    fireEvent.click(screen.getByRole("button", { name: /undo/i }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Could not remove it",
      ),
    );
    expect(screen.getByRole("button", { name: /undo/i })).toBeEnabled();
  });
});

describe("MessageItem memory chips", () => {
  const reply: ChatMessage = {
    key: "a1",
    id: "a1",
    role: "assistant",
    content: "Noted.",
    state: "complete",
    finishReason: "stop",
    usage: null,
    memoriesSaved: [memory],
  };

  it("renders a chip for each memory the reply saved", () => {
    render(<MessageItem message={reply} />);
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByText("Favourite colour is blue")).toBeInTheDocument();
  });

  it("renders none when the reply saved nothing", () => {
    render(<MessageItem message={{ ...reply, memoriesSaved: undefined }} />);
    expect(screen.queryByRole("status")).toBeNull();
  });
});
