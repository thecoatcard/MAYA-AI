import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalCenterView } from "./ApprovalCenterView";
import * as approvalsClient from "../../lib/approvals-client";
import * as authSession from "../../lib/auth-session";

describe("ApprovalCenterView", () => {
  const mockApproval1: approvalsClient.ApprovalRequest = {
    id: "app-101",
    workspace_id: "ws-101",
    user_id: "u-101",
    task_id: "t-101",
    subtask_id: "st-101",
    tool_name: "write_file",
    tool_args: { path: "/app/config.json", mode: "overwrite" },
    payload_hash: "rfc8785digestabcdef123456",
    tier: "APPROVAL_REQUIRED",
    action_description: "Write application configuration",
    payload_diff: { old: {}, new: { debug: false } },
    status: "pending",
    requested_at: "2026-11-07T11:00:00Z",
    expires_at: "2026-11-07T11:30:00Z",
  };

  const mockApproval2: approvalsClient.ApprovalRequest = {
    id: "app-102",
    workspace_id: "ws-101",
    user_id: "u-101",
    task_id: "t-101",
    subtask_id: "st-102",
    tool_name: "docker_kill",
    tool_args: { container_id: "sandbox-c1" },
    payload_hash: "restricteddigest987654",
    tier: "RESTRICTED",
    action_description: "Force kill Docker container sandbox",
    status: "pending",
    requested_at: "2026-11-07T11:05:00Z",
    expires_at: "2026-11-07T11:35:00Z",
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(authSession, "getAccessToken").mockReturnValue(
      "placeholder-auth-token",
    );
    vi.spyOn(approvalsClient, "listApprovals").mockResolvedValue([
      mockApproval1,
      mockApproval2,
    ]);
    vi.spyOn(approvalsClient, "decideApproval").mockResolvedValue({
      id: "app-101",
      status: "approved",
      approval_token: "placeholder-approval-token",
    });
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockImplementation(() => Promise.resolve()),
      },
    });
  });

  it("renders approval requests, RFC 8785 digest, and single-use token protocol", async () => {
    render(<ApprovalCenterView />);

    await waitFor(() => {
      expect(screen.getByText("Approval Requests")).toBeInTheDocument();
      expect(screen.getAllByText("write_file").length).toBeGreaterThanOrEqual(
        1,
      );
      expect(screen.getByText("docker_kill")).toBeInTheDocument();
    });

    expect(
      screen.getByText("Single-Use Execution Token Protocol (Phase 06)"),
    ).toBeInTheDocument();
    expect(screen.getByText("rfc8785digestabcdef123456")).toBeInTheDocument();
    expect(screen.getByText("APPROVAL REQUIRED")).toBeInTheDocument();
  });

  it("copies RFC 8785 payload digest to clipboard", async () => {
    render(<ApprovalCenterView />);

    await waitFor(() => {
      expect(screen.getByText("Copy Hash")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Copy Hash"));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "rfc8785digestabcdef123456",
    );
  });

  it("approves APPROVAL_REQUIRED item directly", async () => {
    render(<ApprovalCenterView />);

    await waitFor(() => {
      expect(screen.getByText("Approve & Execute")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Approve & Execute"));

    await waitFor(() => {
      expect(approvalsClient.decideApproval).toHaveBeenCalledWith("app-101", {
        action: "approve",
        totp_code: undefined,
        rejection_reason: undefined,
      });
    });
  });

  it("triggers step-up TOTP modal for RESTRICTED tier item", async () => {
    render(<ApprovalCenterView />);

    await waitFor(() => {
      expect(screen.getByText("docker_kill")).toBeInTheDocument();
    });

    // Select restricted item
    fireEvent.click(screen.getByText("docker_kill"));

    await waitFor(() => {
      expect(screen.getByText("Verify TOTP & Execute")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Verify TOTP & Execute"));

    await waitFor(() => {
      expect(
        screen.getByText("Step-Up Authentication Required"),
      ).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText("000000");
    fireEvent.change(input, { target: { value: "123456" } });

    fireEvent.click(screen.getByText("Verify & Approve"));

    await waitFor(() => {
      expect(approvalsClient.decideApproval).toHaveBeenCalledWith("app-102", {
        action: "approve",
        totp_code: "123456",
        rejection_reason: undefined,
      });
    });
  });

  it("opens rejection modal, inputs reason, and submits rejection", async () => {
    vi.spyOn(approvalsClient, "decideApproval").mockResolvedValueOnce({
      id: "app-101",
      status: "rejected",
    });

    render(<ApprovalCenterView />);

    await waitFor(() => {
      expect(screen.getByText("Reject Action")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Reject Action"));

    await waitFor(() => {
      expect(screen.getByText("Confirm Rejection")).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText(
      /Provide a reason for rejection/i,
    );
    fireEvent.change(textarea, {
      target: { value: "Untrusted payload configuration" },
    });

    fireEvent.click(screen.getByText("Confirm Rejection"));

    await waitFor(() => {
      expect(approvalsClient.decideApproval).toHaveBeenCalledWith("app-101", {
        action: "reject",
        totp_code: undefined,
        rejection_reason: "Untrusted payload configuration",
      });
    });
  });
});
