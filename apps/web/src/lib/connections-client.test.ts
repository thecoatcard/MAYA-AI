import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  describeConnectionsError,
  disconnectConnection,
  listConnections,
  saveConnection,
  testConnection,
} from "./connections-client";
import { ApiError } from "./api-client";

vi.mock("./auth-session", () => ({
  getAccessToken: () => "access-token",
  refreshSession: vi.fn(async () => false),
}));

const PASSWORD = "abcd efgh ijkl mnop";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("connections client", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lists connections with the bearer token", async () => {
    fetchMock.mockResolvedValue(
      json({ encryption_available: true, connections: [] }),
    );
    const list = await listConnections();
    expect(list.encryption_available).toBe(true);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/settings/connections");
    expect((init.headers as Headers).get("Authorization")).toBe(
      "Bearer access-token",
    );
  });

  it("sends the mailbox fields on PUT and returns only the masked status", async () => {
    fetchMock.mockResolvedValue(
      json({
        connection: {
          provider: "mail",
          kind: "mailbox",
          mailbox: { address: "m***@gmail.com", password_saved: true },
        },
        test: { ok: true, steps: [{ label: "Incoming (IMAP)", ok: true }] },
      }),
    );
    const result = await saveConnection("mail", {
      address: "maya.user@gmail.com",
      token: PASSWORD,
      imap_host: "imap.gmail.com",
      imap_port: 993,
      smtp_host: "smtp.gmail.com",
      smtp_port: 465,
    });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/settings/connections/mail");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string).token).toBe(PASSWORD);
    expect(JSON.stringify(result)).not.toContain(PASSWORD);
    expect(result.test?.steps?.[0]?.label).toBe("Incoming (IMAP)");
  });

  it("tests and disconnects through the provider routes", async () => {
    fetchMock.mockResolvedValueOnce(json({ connection: {}, test: null }));
    await testConnection("mail");
    expect(fetchMock.mock.calls[0]![0]).toBe(
      "/api/v1/settings/connections/mail/test",
    );
    expect(fetchMock.mock.calls[0]![1].method).toBe("POST");
    fetchMock.mockResolvedValueOnce(
      json({ connection: { provider: "mail", state: "not_connected" } }),
    );
    const next = await disconnectConnection("mail");
    expect(fetchMock.mock.calls[1]![1].method).toBe("DELETE");
    expect(next.state).toBe("not_connected");
  });

  it("turns a server refusal into its own plain sentence", async () => {
    fetchMock.mockResolvedValue(
      json(
        { error: "host_forbidden", message: "Use the public host name." },
        422,
      ),
    );
    const error = await saveConnection("mail", { token: PASSWORD }).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(describeConnectionsError(error)).toBe("Use the public host name.");
    expect(describeConnectionsError(new Error("x"))).toMatch(/Try again/);
  });
});
