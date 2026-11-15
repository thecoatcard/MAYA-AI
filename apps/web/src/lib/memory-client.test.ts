import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createMemoryItem,
  deleteMemoryItem,
  listMemoryItems,
  purgeMemory,
  searchMemory,
} from "./memory-client";
import { setAccessToken } from "./auth-session";

describe("memory-client", () => {
  afterEach(() => {
    setAccessToken(null);
    vi.unstubAllGlobals();
  });

  it("lists memory items with category filter", async () => {
    setAccessToken("mem-test-token", 3600);
    const mockItems = [
      {
        id: "mem-1",
        workspace_id: "ws-1",
        user_id: "u-1",
        category: "knowledge",
        content: "API Architecture Guideline",
        metadata: { title: "API Doc" },
        permanent: true,
        access_count: 5,
        last_accessed_at: null,
        expires_at: null,
        created_at: "2026-11-07T12:00:00Z",
      },
    ];

    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(mockItems), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const items = await listMemoryItems("knowledge");
    expect(items).toHaveLength(1);
    expect(items[0]?.content).toBe("API Architecture Guideline");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/memory/items?category=knowledge&limit=50&offset=0",
      expect.objectContaining({
        headers: expect.any(Headers),
      }),
    );
  });

  it("purges user memories under GDPR scope", async () => {
    setAccessToken("mem-test-token", 3600);
    const mockPurgeRes = {
      status: "purged",
      scope: "all",
      records_deleted: 12,
    };

    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(mockPurgeRes), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await purgeMemory("all");
    expect(res.status).toBe("purged");
    expect(res.records_deleted).toBe(12);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/memory/purge",
      expect.objectContaining({
        method: "DELETE",
        body: JSON.stringify({ scope: "all" }),
      }),
    );
  });

  it("creates a memory item", async () => {
    setAccessToken("mem-test-token", 3600);
    const mockCreated = [
      {
        id: "mem-new-1",
        workspace_id: "ws-1",
        user_id: "u-1",
        category: "preference",
        content: "Dark mode prefered",
        metadata: {},
        permanent: false,
        access_count: 0,
        last_accessed_at: null,
        expires_at: null,
        created_at: "2026-11-07T12:00:00Z",
      },
    ];

    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(mockCreated), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await createMemoryItem({
      category: "preference",
      content: "Dark mode prefered",
    });
    expect(res).toHaveLength(1);
    expect(res[0]?.id).toBe("mem-new-1");
  });

  it("searches memory items and deletes an item", async () => {
    setAccessToken("mem-test-token", 3600);
    const mockSearchRes = {
      query: "architecture",
      results: [
        {
          id: "mem-search-1",
          category: "knowledge",
          content: "Architecture rules",
          metadata: {},
          raw_similarity: 0.89,
          effective_score: 0.89,
          source_id: null,
          created_at: "2026-11-07T12:00:00Z",
        },
      ],
      total_results: 1,
    };

    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(mockSearchRes), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const searchRes = await searchMemory({ query: "architecture" });
    expect(searchRes.total_results).toBe(1);

    fetchMock.mockImplementationOnce(
      async () =>
        new Response(JSON.stringify({ deleted: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    await expect(deleteMemoryItem("mem-search-1")).resolves.toBeUndefined();
  });
});
