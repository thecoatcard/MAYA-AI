import { afterEach, describe, expect, it, vi } from "vitest";

import { listAuditEvents } from "./audit-client";
import { setAccessToken } from "./auth-session";

describe("audit-client", () => {
  afterEach(() => {
    setAccessToken(null);
    vi.unstubAllGlobals();
  });

  it("lists audit events with bearer auth and parses items", async () => {
    setAccessToken("audit-test-token", 3600);
    const mockData = {
      items: [
        {
          id: "audit-1",
          timestamp: "2026-11-07T12:00:00Z",
          event_type: "tool.invoked",
          category: "tool",
          actor: "alice@example.com",
          details: "Invoked tool 'web_search'",
          status: "SUCCESS",
          tool_name: "web_search",
          permission_level: "READ",
          prev_hash: "0".repeat(64),
          row_hash: "a".repeat(64),
          duration_ms: 85,
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
      chain_valid: true,
    };

    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(mockData), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await listAuditEvents({ category: "tool" });
    expect(res.total).toBe(1);
    expect(res.chain_valid).toBe(true);
    expect(res.items[0]?.eventType).toBe("tool.invoked");
    expect(res.items[0]?.category).toBe("tool");
    expect(res.items[0]?.toolName).toBe("web_search");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/audit?category=tool",
      expect.objectContaining({
        headers: expect.any(Headers),
      }),
    );
  });

  it("keeps the full status vocabulary and reads chain fields", async () => {
    setAccessToken("audit-test-token", 3600);
    const base = {
      timestamp: "2026-11-07T12:00:00Z",
      event_type: "x",
      category: "task",
      actor: "a",
      details: "d",
      prev_hash: "0",
      row_hash: "1",
    };
    const body = {
      items: [
        { ...base, id: "1", status: "denied" },
        { ...base, id: "2", status: "timeout" },
        { ...base, id: "3", status: "mystery" },
      ],
      total: 3,
      limit: 50,
      offset: 0,
      chain_valid: false,
      chain_first_bad_id: "2",
      chain_length: 3,
      statuses: ["success", "denied"],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })),
    );
    const res = await listAuditEvents();
    expect(res.items.map((i) => i.status)).toEqual([
      "denied",
      "timeout",
      "warning",
    ]);
    expect(res.chain_first_bad_id).toBe("2");
    expect(res.chain_length).toBe(3);
  });
});
