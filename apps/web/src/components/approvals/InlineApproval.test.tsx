import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApprovalRequest } from "../../lib/approvals-client";

const decideApproval = vi.fn();
vi.mock("../../lib/approvals-client", () => ({
  decideApproval: (id: string, body: unknown) => decideApproval(id, body),
}));

import { InlineApproval } from "./InlineApproval";

const approval = {
  id: "a1",
  tool_name: "notion_add_row",
  tier: "APPROVAL_REQUIRED",
  status: "pending",
  action_description:
    "Notion: add the row 'Java Notes' to the Tasks database (1 column)",
  tool_args: {
    requests: [
      {
        method: "POST",
        path: "/pages",
        body: {
          properties: {
            Name: { title: [{ text: { content: "Java Notes" } }] },
          },
        },
      },
    ],
  },
  payload_hash: "h",
  requested_at: "2026-11-11T10:00:00Z",
  expires_at: "2099-11-11T10:30:00Z",
} as unknown as ApprovalRequest;

describe("InlineApproval", () => {
  beforeEach(() => {
    decideApproval.mockReset();
    decideApproval.mockResolvedValue({ id: "a1", status: "approved" });
  });

  it("shows what will happen and hides the exact payload until asked", () => {
    render(<InlineApproval approval={approval} />);
    expect(
      screen.getByText(/add the row 'Java Notes' to the Tasks/),
    ).toBeTruthy();
    expect(screen.getByText("POST /pages")).toBeTruthy();
    expect(screen.queryByText(/"requests"/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show exact payload" }));
    expect(screen.getByText(/"requests"/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Hide exact payload" }));
    expect(screen.queryByText(/"requests"/)).toBeNull();
  });

  it("Approve records the decision once, through the approvals client", async () => {
    const onDecided = vi.fn();
    render(<InlineApproval approval={approval} onDecided={onDecided} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(onDecided).toHaveBeenCalledWith("a1", "approved"),
    );
    expect(decideApproval).toHaveBeenCalledTimes(1);
    expect(decideApproval).toHaveBeenCalledWith("a1", { action: "approve" });
    expect(screen.getByText(/Approved\. It runs once/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("Reject records the decision once", async () => {
    decideApproval.mockResolvedValue({ id: "a1", status: "denied" });
    const onDecided = vi.fn();
    render(<InlineApproval approval={approval} onDecided={onDecided} />);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(onDecided).toHaveBeenCalledWith("a1", "denied"));
    expect(decideApproval).toHaveBeenCalledTimes(1);
    expect(decideApproval).toHaveBeenCalledWith("a1", { action: "reject" });
  });

  it("disables both buttons while the decision is pending and ignores a second click", async () => {
    let finish: (value: unknown) => void = () => undefined;
    decideApproval.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<InlineApproval approval={approval} />);
    const approve = screen.getByRole("button", { name: /Approve/ });
    fireEvent.click(approve);
    fireEvent.click(approve);
    expect(decideApproval).toHaveBeenCalledTimes(1);
    expect(
      (screen.getByRole("button", { name: /Reject/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    finish({ id: "a1", status: "approved" });
    await waitFor(() => expect(screen.getByText(/Approved\./)).toBeTruthy());
  });

  it("shows the server's error plainly and lets the user try again", async () => {
    decideApproval.mockRejectedValueOnce(
      new Error("This request has expired."),
    );
    render(<InlineApproval approval={approval} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("This request has expired.")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("asks for the authenticator code inline for a restricted action", async () => {
    const restricted = { ...approval, tier: "RESTRICTED" } as ApprovalRequest;
    render(<InlineApproval approval={restricted} />);
    const approve = screen.getByRole("button", { name: "Approve" });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Authenticator code/), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(decideApproval).toHaveBeenCalledTimes(1));
    expect(decideApproval).toHaveBeenCalledWith("a1", {
      action: "approve",
      totp_code: "123456",
    });
  });

  it("keeps the decision area still and says typing yes approves nothing", () => {
    render(<InlineApproval approval={approval} />);
    expect(
      screen.getByRole("button", { name: "Approve" }).closest(".motion-none"),
    ).not.toBeNull();
    expect(screen.getByText(/Only these buttons approve/)).toBeTruthy();
  });

  it("renders dedicated preview for terminal commands", () => {
    const cmdApproval = {
      ...approval,
      tool_name: "workspace_run_command",
      tool_args: { command: "npm run build && npm test" },
    } as unknown as ApprovalRequest;
    render(<InlineApproval approval={cmdApproval} />);
    expect(screen.getByText("Terminal Command to Execute")).toBeTruthy();
    expect(screen.getByText("$ npm run build && npm test")).toBeTruthy();
  });

  it("renders dedicated preview for workspace deletions", () => {
    const delApproval = {
      ...approval,
      tool_name: "workspace_delete_file",
      tool_args: { path: "src/legacy/unused.ts" },
    } as unknown as ApprovalRequest;
    render(<InlineApproval approval={delApproval} />);
    expect(screen.getByText("Target to Delete")).toBeTruthy();
    expect(screen.getByText("src/legacy/unused.ts")).toBeTruthy();
  });
});

describe("InlineApproval voice safety wording", () => {
  it("shows the voice notice only while voice is on, and approves nothing", async () => {
    const { ConversationContext } = await import("../chat/ConversationContext");
    const { VOICE_APPROVAL_NOTICE } = await import("../voice/voice-copy");
    const first = render(<InlineApproval approval={approval} />);
    expect(screen.queryByTestId("voice-approval-notice")).toBeNull();
    first.unmount();
    render(
      <ConversationContext.Provider value={{ voiceLive: true } as never}>
        <InlineApproval approval={approval} />
      </ConversationContext.Provider>,
    );
    expect(screen.getByTestId("voice-approval-notice").textContent).toBe(
      VOICE_APPROVAL_NOTICE,
    );
    expect(decideApproval).not.toHaveBeenCalled();
  });
});

describe("InlineApproval context menu", () => {
  it("offers Copy exact payload and Reject, and never Approve", () => {
    render(<InlineApproval approval={approval} />);
    const section = screen.getByTestId("inline-approval");
    fireEvent.contextMenu(section);
    const menu = screen.getByRole("menu");
    const names = Array.from(menu.querySelectorAll('[role="menuitem"]')).map(
      (el) => el.textContent ?? "",
    );
    expect(names.some((n) => /Copy exact payload/.test(n))).toBe(true);
    expect(names.some((n) => /Reject/.test(n))).toBe(true);
    expect(names.some((n) => /Approve/i.test(n))).toBe(false);
  });

  it("Reject from the menu uses the existing decision handler", async () => {
    decideApproval.mockResolvedValue({ id: "a1", status: "rejected" });
    render(<InlineApproval approval={approval} />);
    fireEvent.contextMenu(screen.getByTestId("inline-approval"));
    fireEvent.click(screen.getByRole("menuitem", { name: /Reject/ }));
    await waitFor(() =>
      expect(decideApproval).toHaveBeenCalledWith("a1", { action: "reject" }),
    );
  });
});
