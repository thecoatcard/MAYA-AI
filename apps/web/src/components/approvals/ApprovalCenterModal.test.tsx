import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalCenterModal } from "./ApprovalCenterModal";
import * as approvalsClient from "../../lib/approvals-client";
import * as authSession from "../../lib/auth-session";

describe("ApprovalCenterModal", () => {
  const mockApproval1: approvalsClient.ApprovalRequest = {
    id: "appr-1",
    workspace_id: "ws-1",
    user_id: "u-1",
    task_id: "task-1",
    subtask_id: "st-1",
    tool_name: "write_file",
    tool_args: { path: "/etc/config.json", content: "{}" },
    payload_hash: "hash1234567890abcdef",
    tier: "APPROVAL_REQUIRED",
    action_description: "Write configuration file to server",
    payload_diff: { old: null, new: { status: "active" } },
    status: "pending",
    requested_at: "2026-11-06T12:00:00Z",
    expires_at: "2026-11-06T12:05:00Z",
  };

  const mockApproval2: approvalsClient.ApprovalRequest = {
    id: "appr-2",
    workspace_id: "ws-1",
    user_id: "u-1",
    task_id: "task-1",
    subtask_id: "st-2",
    tool_name: "db_drop_table",
    tool_args: { table: "audit_logs" },
    payload_hash: "restrictedhash9999",
    tier: "RESTRICTED",
    action_description: "Drop critical database table",
    payload_diff: null,
    status: "pending",
    requested_at: "2026-11-06T12:01:00Z",
    expires_at: "2026-11-06T12:06:00Z",
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(authSession, "getAccessToken").mockReturnValue("test-token");
    vi.spyOn(approvalsClient, "listApprovals").mockResolvedValue([
      mockApproval1,
      mockApproval2,
    ]);
    vi.spyOn(approvalsClient, "decideApproval").mockResolvedValue({
      id: "appr-1",
      status: "approved",
      approval_token: "example-jwt-token",
      expires_at: "2026-11-06T12:05:00Z",
    });
  });

  it("does not render when isOpen is false", () => {
    const { container } = render(
      <ApprovalCenterModal isOpen={false} onClose={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("loads and displays list of approval requests when opened", async () => {
    render(<ApprovalCenterModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("Approval Center")).toBeInTheDocument();
      expect(screen.getAllByText("write_file").length).toBeGreaterThanOrEqual(
        1,
      );
      expect(screen.getByText("db_drop_table")).toBeInTheDocument();
    });

    expect(screen.getByText("Pending Requests (2)")).toBeInTheDocument();
    expect(
      screen.getAllByText("Write configuration file to server").length,
    ).toBeGreaterThanOrEqual(1);
  });

  it("displays empty state when no pending requests exist", async () => {
    vi.spyOn(approvalsClient, "listApprovals").mockResolvedValueOnce([]);

    render(<ApprovalCenterModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(
        screen.getByText(/No pending approval requests/i),
      ).toBeInTheDocument();
    });
  });

  it("switches selected approval on click and copies hash to clipboard", async () => {
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockImplementation(() => Promise.resolve()),
      },
    });

    render(<ApprovalCenterModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getAllByText("write_file").length).toBeGreaterThanOrEqual(
        1,
      );
    });

    // Select second item (restricted)
    fireEvent.click(screen.getByText("db_drop_table"));

    await waitFor(() => {
      expect(
        screen.getAllByText("Drop critical database table").length,
      ).toBeGreaterThanOrEqual(1);
    });

    // Copy hash
    const copyBtn = screen.getByTitle("Copy payload hash");
    fireEvent.click(copyBtn);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "restrictedhash9999",
    );
  });

  it("authorizes APPROVAL_REQUIRED item directly without TOTP modal", async () => {
    const onDecision = vi.fn();
    render(
      <ApprovalCenterModal
        isOpen={true}
        onClose={vi.fn()}
        onDecisionMade={onDecision}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Authorize Execution")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Authorize Execution"));

    await waitFor(() => {
      expect(approvalsClient.decideApproval).toHaveBeenCalledWith("appr-1", {
        action: "approve",
        totp_code: undefined,
      });
      expect(onDecision).toHaveBeenCalledWith("appr-1", "approved");
    });
  });

  it("requires step-up TOTP verification for RESTRICTED item", async () => {
    const onDecision = vi.fn();
    render(
      <ApprovalCenterModal
        isOpen={true}
        onClose={vi.fn()}
        initialApprovalId="appr-2"
        onDecisionMade={onDecision}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Verify & Approve")).toBeInTheDocument();
    });

    // Clicking Verify & Approve triggers step-up modal
    fireEvent.click(screen.getByText("Verify & Approve"));

    await waitFor(() => {
      expect(
        screen.getByText("Step-Up Authentication Required"),
      ).toBeInTheDocument();
    });

    const totpInput = screen.getByLabelText("Authentication Code");
    fireEvent.change(totpInput, { target: { value: "654321" } });

    const confirmBtn = screen.getByText("Confirm & Mint Token");
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(approvalsClient.decideApproval).toHaveBeenCalledWith("appr-2", {
        action: "approve",
        totp_code: "654321",
      });
      expect(onDecision).toHaveBeenCalledWith("appr-2", "approved");
    });
  });

  it("rejects an action upon clicking Reject Action", async () => {
    vi.spyOn(approvalsClient, "decideApproval").mockResolvedValueOnce({
      id: "appr-1",
      status: "denied",
    });

    const onDecision = vi.fn();
    render(
      <ApprovalCenterModal
        isOpen={true}
        onClose={vi.fn()}
        onDecisionMade={onDecision}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Reject Action")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Reject Action"));

    await waitFor(() => {
      expect(approvalsClient.decideApproval).toHaveBeenCalledWith("appr-1", {
        action: "reject",
        totp_code: undefined,
      });
      expect(onDecision).toHaveBeenCalledWith("appr-1", "denied");
    });
  });

  it("closes modal on close button click and escape key", async () => {
    const onClose = vi.fn();
    render(<ApprovalCenterModal isOpen={true} onClose={onClose} />);

    await waitFor(() => {
      expect(screen.getByLabelText("Close modal")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText("Close modal"));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
