import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityView } from "./ActivityView";
import * as auditClient from "../../lib/audit-client";
import * as systemClient from "../../lib/system-client";

vi.mock("../../lib/system-client", () => ({
  getPrincipalMe: vi.fn(),
  checkSystemHealth: vi.fn(),
}));
vi.mock("../../lib/audit-client", () => ({
  listAuditEvents: vi.fn(),
}));

function evt(id: string, eventType: string, category: string, status: string) {
  return {
    id,
    timestamp: "2026-11-07T10:45:12Z",
    eventType,
    category,
    actor: "user:abc",
    details: `${eventType} details`,
    status,
    prevHash: "a".repeat(64),
    rowHash: "b".repeat(64),
  };
}

function respond(items: unknown[], chainValid = true, firstBad?: string) {
  vi.mocked(auditClient.listAuditEvents).mockResolvedValue({
    items,
    total: items.length,
    limit: 50,
    offset: 0,
    chain_valid: chainValid,
    chain_first_bad_id: firstBad,
  } as never);
}

describe("ActivityView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(systemClient.getPrincipalMe).mockResolvedValue({
      user_id: "user-admin-123",
      workspace_id: "ws-test-456",
      role: "admin",
    });
  });

  it("shows no invented rows when the ledger is empty", async () => {
    respond([]);
    render(<ActivityView />);
    expect(
      await screen.findByText(/No audit events recorded yet/i),
    ).toBeInTheDocument();
    expect(screen.queryByText("task.created")).toBeNull();
    expect(screen.queryByText(/7 Blocks Sealed/i)).toBeNull();
  });

  it("shows chain status as unknown while loading, never verified", () => {
    vi.mocked(auditClient.listAuditEvents).mockReturnValue(
      new Promise(() => {}),
    );
    render(<ActivityView />);
    expect(screen.queryByText("Chain verified")).toBeNull();
    expect(screen.getByText(/Chain: verifying/i)).toBeInTheDocument();
  });

  it("renders real rows and a verified chain", async () => {
    respond([evt("1", "task.created", "task", "success")]);
    render(<ActivityView />);
    expect(await screen.findByText("task.created")).toBeInTheDocument();
    expect(screen.getByText("Chain verified")).toBeInTheDocument();
  });

  it("names the first broken row when the chain is broken", async () => {
    respond([evt("1", "task.created", "task", "success")], false, "row-42");
    render(<ActivityView />);
    expect(
      await screen.findByText("Chain broken at row row-42"),
    ).toBeInTheDocument();
  });

  it("labels failed and denied statuses with text, not colour only", async () => {
    respond([
      evt("1", "tool.invoked", "tool", "failed"),
      evt("2", "approval.decided", "approval", "denied"),
    ]);
    render(<ActivityView />);
    expect(await screen.findByText("FAILED")).toBeInTheDocument();
    expect(screen.getByText("DENIED")).toBeInTheDocument();
  });

  it("shows an error with retry when the backend fails", async () => {
    vi.mocked(auditClient.listAuditEvents).mockRejectedValueOnce(
      new Error("x"),
    );
    render(<ActivityView />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Unable to load/i,
    );
    expect(screen.getByText(/Chain status unknown/i)).toBeInTheDocument();
    respond([evt("1", "task.created", "task", "success")]);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("task.created")).toBeInTheDocument();
  });

  it("opens the audit block inspector", async () => {
    respond([evt("1", "task.created", "task", "success")]);
    render(<ActivityView />);
    await waitFor(() => screen.getByText("task.created"));
    fireEvent.click(
      screen.getAllByRole("button", { name: /inspect block/i })[0],
    );
    expect(screen.getByText(/Audit Block: task\.created/i)).toBeInTheDocument();
  });

  it("gives failed and denied different icons as well as different words", async () => {
    respond([
      evt("1", "tool.invoked", "tool", "failed"),
      evt("2", "approval.decided", "approval", "denied"),
    ]);
    const { container } = render(<ActivityView />);
    await screen.findByText("FAILED");
    const failed = container.querySelector('[data-status="failed"] svg');
    const denied = container.querySelector('[data-status="denied"] svg');
    expect(failed).not.toBeNull();
    expect(denied).not.toBeNull();
    expect(failed?.getAttribute("class")).not.toBe(
      denied?.getAttribute("class"),
    );
  });

  it("wraps a very long detail string instead of widening the row", async () => {
    const long = "x".repeat(900);
    respond([
      { ...evt("1", "tool.invoked", "tool", "success"), details: long },
    ]);
    render(<ActivityView />);
    const node = await screen.findByText(long);
    expect(node.className).toMatch(/overflow-wrap:anywhere/);
  });

  it("pages with Load more using the real offset and total", async () => {
    vi.mocked(auditClient.listAuditEvents)
      .mockResolvedValueOnce({
        items: [evt("1", "task.created", "task", "success")],
        total: 2,
        limit: 50,
        offset: 0,
        chain_valid: true,
        chain_first_bad_id: null,
        chain_length: 2,
        statuses: [],
      } as never)
      .mockResolvedValueOnce({
        items: [evt("2", "task.completed", "task", "success")],
        total: 2,
        limit: 50,
        offset: 1,
        chain_valid: true,
        chain_first_bad_id: null,
        chain_length: 2,
        statuses: [],
      } as never);
    render(<ActivityView />);
    expect(await screen.findByText(/1 of 2 rows loaded/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("task.completed")).toBeInTheDocument();
    expect(auditClient.listAuditEvents).toHaveBeenLastCalledWith({
      category: undefined,
      limit: 50,
      offset: 1,
    });
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });
});
