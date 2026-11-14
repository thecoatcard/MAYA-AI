import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../../lib/approvals-client";
import type { ApprovalRequest } from "../../lib/approvals-client";
import { AutoApprovedNote } from "./AutoApprovedNote";

function approval(over: Partial<ApprovalRequest>): ApprovalRequest {
  return {
    id: "a1",
    workspace_id: "w",
    user_id: "u",
    task_id: "t1",
    subtask_id: null,
    tool_name: "notion_create_page",
    tool_args: { requests: [] },
    payload_hash: "h",
    tier: "APPROVAL_REQUIRED",
    action_description: "x",
    status: "completed",
    requested_at: "2026-11-12T10:00:00Z",
    expires_at: "2026-11-19T10:00:00Z",
    ...over,
  };
}

describe("AutoApprovedNote", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("says so, keeps the payload visible and links to the setting", async () => {
    vi.spyOn(client, "listApprovals").mockResolvedValue([
      approval({ auto_approved: true }),
    ]);
    render(<AutoApprovedNote taskId="t1" phase="completed" />);
    expect(
      await screen.findByText(/Auto-approved by your setting/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Undo setting" })).toHaveAttribute(
      "href",
      "/settings#settings-connections",
    );
    expect(screen.getByText(/"requests": \[\]/)).toBeInTheDocument();
  });

  it("renders nothing for a human-approved task or another task", async () => {
    const spy = vi
      .spyOn(client, "listApprovals")
      .mockResolvedValue([
        approval({ auto_approved: false }),
        approval({ id: "a2", task_id: "other", auto_approved: true }),
      ]);
    const { container } = render(
      <AutoApprovedNote taskId="t1" phase="completed" />,
    );
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
