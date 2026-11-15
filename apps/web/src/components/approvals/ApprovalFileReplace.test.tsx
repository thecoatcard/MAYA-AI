import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import type { ApprovalRequest } from "../../lib/approvals-client";
import { InlineApproval } from "./InlineApproval";

const approval = {
  id: "fr-1",
  workspace_id: "w",
  user_id: "u",
  tool_name: "file_replace_original",
  tool_args: { path: "reports/q3.md", new_version: "reports/q3 (edited).md" },
  payload_hash: "abc123digest",
  tier: "APPROVAL_REQUIRED",
  action_description: "Replace reports/q3.md with the edited version",
  payload_diff: {
    diff: "--- a/reports/q3.md\n+++ b/reports/q3.md\n@@ -1,2 +1,2 @@\n Title\n-old line\n+new line\n",
  },
  status: "pending",
  requested_at: "2026-11-10T11:00:00Z",
  expires_at: "2099-11-10T11:30:00Z",
} as unknown as ApprovalRequest;

describe("InlineApproval for file_replace_original", () => {
  it("shows the original, the new version and the diff in full", () => {
    render(<InlineApproval approval={approval} />);
    expect(screen.getByText("Replace the original file")).toBeTruthy();
    expect(screen.getByText("reports/q3.md", { selector: "dd" })).toBeTruthy();
    expect(
      screen.getByText("reports/q3 (edited).md", { selector: "dd" }),
    ).toBeTruthy();
    expect(screen.getByText(/content is lost/)).toBeTruthy();
    expect(screen.getByText("What will change")).toBeTruthy();
    expect(screen.getByText(/new line/)).toBeTruthy();
    expect(screen.getByText(/old line/)).toBeTruthy();
  });

  it("keeps the decision bar still (motion-none) with approve and reject", () => {
    render(<InlineApproval approval={approval} />);
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve.closest(".motion-none")).not.toBeNull();
    expect(
      screen.getByRole("button", { name: "Reject" }).closest(".motion-none"),
    ).not.toBeNull();
  });

  it("says plainly when the arguments are missing instead of guessing", () => {
    const bare = {
      ...approval,
      tool_args: {},
      payload_diff: null,
    } as unknown as ApprovalRequest;
    render(<InlineApproval approval={bare} />);
    expect(screen.getAllByText("Not given")).toHaveLength(2);
  });
});
