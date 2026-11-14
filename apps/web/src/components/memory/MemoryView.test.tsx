import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryView } from "./MemoryView";
import * as memoryClient from "../../lib/memory-client";
import * as systemClient from "../../lib/system-client";

vi.mock("../../lib/system-client", () => ({
  getPrincipalMe: vi.fn(),
  checkSystemHealth: vi.fn(),
}));

vi.mock("../../lib/memory-client", () => ({
  listMemoryItems: vi.fn(),
  createMemoryItem: vi.fn(),
  searchMemory: vi.fn(),
  deleteMemoryItem: vi.fn(),
  purgeMemory: vi.fn(),
}));

describe("MemoryView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(systemClient.getPrincipalMe).mockResolvedValue({
      user_id: "u-999",
      workspace_id: "ws-888",
      role: "owner",
    });
  });

  it("renders memory repository and empty state when no items exist", async () => {
    vi.mocked(memoryClient.listMemoryItems).mockResolvedValueOnce([]);

    render(<MemoryView />);

    expect(screen.getByRole("heading", { name: "Memory" })).toBeInTheDocument();
    // No invented implementation copy.
    expect(screen.queryByText(/Redis|LangGraph|pgvector|Cosine/i)).toBeNull();
    expect(screen.queryByText(/Working Memory/i)).toBeNull();

    await waitFor(() => {
      expect(screen.getByText("No memory items recorded")).toBeInTheDocument();
    });
  });

  it("renders real memory items loaded from backend", async () => {
    const mockItems: memoryClient.MemoryItem[] = [
      {
        id: "mem-real-01",
        workspace_id: "ws-888",
        user_id: "u-999",
        category: "preference",
        content: "Always format code snippets with explicit language tags.",
        metadata: {
          title: "Code Formatting Preference",
          source: "User Settings",
        },
        permanent: true,
        access_count: 5,
        last_accessed_at: "2026-11-08T10:00:00Z",
        expires_at: null,
        created_at: "2026-11-08T09:00:00Z",
      },
    ];

    vi.mocked(memoryClient.listMemoryItems).mockResolvedValueOnce(mockItems);

    render(<MemoryView />);

    await waitFor(() => {
      expect(
        screen.getByText("Code Formatting Preference"),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          "Always format code snippets with explicit language tags.",
        ),
      ).toBeInTheDocument();
      expect(screen.getByText("User Settings")).toBeInTheDocument();
    });
  });

  it("filters memory entries by category calling backend API", async () => {
    vi.mocked(memoryClient.listMemoryItems).mockResolvedValue([]);

    render(<MemoryView />);

    const userPrefTab = screen.getByRole("button", {
      name: "Preferences",
    });
    fireEvent.click(userPrefTab);

    await waitFor(() => {
      expect(memoryClient.listMemoryItems).toHaveBeenCalledWith("preference");
    });
  });

  it("searches memories using searchMemory client", async () => {
    vi.mocked(memoryClient.listMemoryItems).mockResolvedValue([]);
    vi.mocked(memoryClient.searchMemory).mockResolvedValue({
      query: "format",
      results: [
        {
          id: "mem-search-1",
          category: "preference",
          content: "Formatting rule",
          metadata: { title: "Found rule" },
          raw_similarity: 0.95,
          effective_score: 0.95,
          source_id: null,
          created_at: "2026-11-08T10:00:00Z",
        },
      ],
      total_results: 1,
    });

    render(<MemoryView />);

    const searchInput = screen.getByPlaceholderText("Search memories");
    fireEvent.change(searchInput, { target: { value: "format" } });

    await waitFor(() => {
      expect(memoryClient.searchMemory).toHaveBeenCalledWith({
        query: "format",
        categories: undefined,
      });
      expect(screen.getByText("Found rule")).toBeInTheDocument();
    });
  });

  it("allows creating a new memory item", async () => {
    vi.mocked(memoryClient.listMemoryItems).mockResolvedValue([]);
    vi.mocked(memoryClient.createMemoryItem).mockResolvedValue([]);

    render(<MemoryView />);

    const addButtons = screen.getAllByRole("button", { name: /add memory/i });
    fireEvent.click(addButtons[0]);

    expect(screen.getByText("Add Memory Item")).toBeInTheDocument();

    const textarea = screen.getByPlaceholderText("Enter memory content...");
    fireEvent.change(textarea, { target: { value: "A new remembered fact" } });

    fireEvent.click(screen.getByRole("button", { name: "Save Memory" }));

    await waitFor(() => {
      expect(memoryClient.createMemoryItem).toHaveBeenCalledWith({
        category: "preference",
        content: "A new remembered fact",
        permanent: false,
      });
    });
  });

  it("allows deleting an individual memory item", async () => {
    const mockItems: memoryClient.MemoryItem[] = [
      {
        id: "mem-del-01",
        workspace_id: "ws-888",
        user_id: "u-999",
        category: "knowledge",
        content: "Temporary fact",
        metadata: { title: "Delete Me" },
        permanent: false,
        access_count: 1,
        last_accessed_at: null,
        expires_at: null,
        created_at: "2026-11-08T09:00:00Z",
      },
    ];

    vi.mocked(memoryClient.listMemoryItems).mockResolvedValueOnce(mockItems);
    vi.mocked(memoryClient.deleteMemoryItem).mockResolvedValueOnce();

    render(<MemoryView />);

    await waitFor(() => {
      expect(screen.getByText("Delete Me")).toBeInTheDocument();
    });

    const deleteBtn = screen.getByLabelText("Delete memory Delete Me");
    fireEvent.click(deleteBtn);
    // Asks first: nothing is deleted by a single click.
    expect(memoryClient.deleteMemoryItem).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Yes, delete" }));

    await waitFor(() => {
      expect(memoryClient.deleteMemoryItem).toHaveBeenCalledWith("mem-del-01");
      expect(screen.queryByText("Delete Me")).not.toBeInTheDocument();
    });
  });

  it("opens purge modal and requires PURGE confirmation", async () => {
    vi.mocked(memoryClient.listMemoryItems).mockResolvedValue([]);
    vi.mocked(memoryClient.purgeMemory).mockResolvedValue({
      status: "purged",
      scope: "all",
      records_deleted: 5,
    });

    render(<MemoryView />);

    const purgeButton = screen.getByRole("button", {
      name: /purge memory/i,
    });
    fireEvent.click(purgeButton);

    expect(screen.getByText("Erase all memory")).toBeInTheDocument();
    const eraseButton = screen.getByRole("button", {
      name: "Erase All Memory Items",
    });
    expect(eraseButton).toBeDisabled();

    const input = screen.getByPlaceholderText("Type PURGE");
    fireEvent.change(input, { target: { value: "PURGE" } });

    expect(eraseButton).not.toBeDisabled();
    fireEvent.click(eraseButton);

    await waitFor(() => {
      expect(memoryClient.purgeMemory).toHaveBeenCalledWith("all");
    });
  });

  it("shows honest error state with retry on fetch failure", async () => {
    vi.mocked(memoryClient.listMemoryItems).mockRejectedValueOnce(
      new Error("Network failed"),
    );

    render(<MemoryView />);

    await waitFor(() => {
      expect(
        screen.getByText(
          "Unable to load memory records from the MAYA backend.",
        ),
      ).toBeInTheDocument();
    });

    vi.mocked(memoryClient.listMemoryItems).mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => {
      expect(screen.getByText("No memory items recorded")).toBeInTheDocument();
    });
  });

  it("shows the text once when there is no real title", async () => {
    vi.mocked(memoryClient.listMemoryItems).mockResolvedValueOnce([
      {
        id: "m1",
        workspace_id: "w",
        user_id: "u",
        category: "preference",
        content: "Prefers short answers in the morning.",
        metadata: {},
        permanent: false,
        access_count: 0,
        last_accessed_at: null,
        expires_at: null,
        created_at: "2026-11-08T09:00:00Z",
      },
    ]);
    render(<MemoryView />);
    await waitFor(() =>
      expect(
        screen.getAllByText("Prefers short answers in the morning."),
      ).toHaveLength(1),
    );
    expect(screen.getByText("Standard retention")).toBeInTheDocument();
    expect(screen.getByTestId("memory-count")).toHaveTextContent("1 memory");
  });

  it("pages with Load more using the real offset and hides it on a short page", async () => {
    const make = (n: number, start: number): memoryClient.MemoryItem[] =>
      Array.from({ length: n }, (_, i) => ({
        id: `m${start + i}`,
        workspace_id: "w",
        user_id: "u",
        category: "knowledge",
        content: `fact ${start + i}`,
        metadata: {},
        permanent: true,
        access_count: 0,
        last_accessed_at: null,
        expires_at: null,
        created_at: "2026-11-08T09:00:00Z",
      }));
    vi.mocked(memoryClient.listMemoryItems)
      .mockResolvedValueOnce(make(50, 0))
      .mockResolvedValueOnce(make(3, 50));
    render(<MemoryView />);
    const more = await screen.findByRole("button", { name: "Load more" });
    expect(screen.getByTestId("memory-count")).toHaveTextContent("50+");
    fireEvent.click(more);
    await waitFor(() =>
      expect(memoryClient.listMemoryItems).toHaveBeenLastCalledWith(
        undefined,
        50,
        50,
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("memory-count")).toHaveTextContent(
        "53 memories",
      ),
    );
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });

  it("only turns http(s) citations into links", async () => {
    vi.mocked(memoryClient.listMemoryItems).mockResolvedValueOnce([
      {
        id: "m9",
        workspace_id: "w",
        user_id: "u",
        category: "knowledge",
        content: "Cited fact",
        metadata: { source: "Doc", sourceUrl: "javascript:alert(1)" },
        permanent: false,
        access_count: 0,
        last_accessed_at: null,
        expires_at: null,
        created_at: "2026-11-08T09:00:00Z",
      },
    ]);
    render(<MemoryView />);
    fireEvent.click(await screen.findByLabelText("Open memory Cited fact"));
    expect(screen.queryByRole("link", { name: "Open reference" })).toBeNull();
  });
});
