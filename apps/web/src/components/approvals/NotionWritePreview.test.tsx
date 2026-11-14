import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import type { ApprovalRequest } from "../../lib/approvals-client";
import { InlineApproval } from "./InlineApproval";
import { blockText, propertyText } from "./NotionWritePreview";

const text = (c: string) => [{ type: "text", text: { content: c } }];

const approval = {
  id: "n-1",
  workspace_id: "w",
  user_id: "u",
  tool_name: "notion_create_page",
  tool_args: {
    requests: [
      {
        method: "POST",
        path: "/pages",
        body: {
          parent: { page_id: "abc" },
          properties: { title: { title: text("Launch plan") } },
          children: [
            { type: "heading_2", heading_2: { rich_text: text("Goals") } },
            {
              type: "to_do",
              to_do: { rich_text: text("Ship"), checked: true },
            },
          ],
        },
      },
    ],
  },
  payload_hash: "h",
  tier: "APPROVAL_REQUIRED",
  action_description: "Create a Notion page",
  payload_diff: null,
  status: "pending",
  requested_at: "2026-11-10T11:00:00Z",
  expires_at: "2099-11-10T11:30:00Z",
} as unknown as ApprovalRequest;

describe("Notion write approval", () => {
  it("shows the request, title and blocks in plain language", () => {
    render(<InlineApproval approval={approval} />);
    expect(screen.getByText("What will be written to Notion")).toBeTruthy();
    expect(screen.getByText("POST /pages")).toBeTruthy();
    expect(screen.getByText("Launch plan")).toBeTruthy();
    expect(screen.getByText("heading_2 Goals")).toBeTruthy();
    expect(screen.getByText("to_do [x] Ship")).toBeTruthy();
  });

  it("describes property and block payloads", () => {
    expect(propertyText({ select: { name: "Done" } })).toBe("Done");
    expect(
      propertyText({ date: { start: "2026-01-01", end: "2026-01-02" } }),
    ).toBe("2026-01-01 to 2026-01-02");
    expect(
      blockText({
        type: "image",
        image: { external: { url: "https://x/y.png" } },
      }),
    ).toBe("image https://x/y.png");
  });
  it("shows the note and the trash warning of an archive request", () => {
    const archive = {
      ...approval,
      id: "n-2",
      tool_name: "notion_archive_page",
      tool_args: {
        requests: [
          {
            method: "PATCH",
            path: "/pages/abc",
            body: { archived: true },
            note: "Moves the page 'Java Notes' to the trash",
          },
        ],
      },
    } as unknown as ApprovalRequest;
    render(<InlineApproval approval={archive} />);
    expect(
      screen.getByText(/Moves the page 'Java Notes' to the trash/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Moves the page to the trash."),
    ).toBeInTheDocument();
  });
});
