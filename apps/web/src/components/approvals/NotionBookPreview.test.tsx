import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApprovalRequest } from "../../lib/approvals-client";

const fetchArtifactText = vi.fn();
vi.mock("../../lib/artifacts-client", () => ({
  fetchArtifactText: (id: string) => fetchArtifactText(id),
}));
const decideApproval = vi.fn();
vi.mock("../../lib/approvals-client", () => ({
  decideApproval: (id: string, body: unknown) => decideApproval(id, body),
}));

import { InlineApproval } from "./InlineApproval";
import { NotionBookPreview } from "./NotionBookPreview";

function book(chapters: number): string {
  const parts = ["*A story-based book*", "[toc]"];
  for (let n = 1; n <= chapters; n += 1) {
    parts.push(
      `# Chapter ${n}: Part ${n}`,
      "## Big part",
      `Words of chapter ${n}.`,
    );
  }
  parts.push("<script>alert(1)</script>");
  return parts.join("\n\n");
}

const args = {
  title: "Master Notes",
  parent_page_id: "0123456789abcdef0123456789abcdef",
  parent_label: "Study space",
  content_artifact_id: "art-1",
  content_file: "book.md",
  content_sha256: "a".repeat(64),
  blocks: 612,
  words: 8123,
  chapters: 5,
  code_blocks: 11,
  tables: 4,
  equations: 3,
  icon: "📘",
  cover_url: "",
};

beforeEach(() => {
  fetchArtifactText.mockReset();
  decideApproval.mockReset();
});

describe("NotionBookPreview", () => {
  it("shows the title, the parent and the counts before the text arrives", () => {
    fetchArtifactText.mockReturnValue(new Promise(() => undefined));
    render(<NotionBookPreview args={args} />);
    expect(screen.getByText("📘 Master Notes")).toBeInTheDocument();
    expect(
      screen.getByText(/Study space \(0123456789abcdef0123456789abcdef\)/),
    ).toBeInTheDocument();
    expect(screen.getByText("612")).toBeInTheDocument();
    expect(screen.getByText("8,123")).toBeInTheDocument();
    expect(screen.getByText("Blocks in Notion")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading the book text",
    );
    expect(fetchArtifactText).toHaveBeenCalledWith("art-1");
  });

  it("lists the chapter outline and the first lines once loaded", async () => {
    fetchArtifactText.mockResolvedValue(book(5));
    render(<NotionBookPreview args={args} />);
    const outline = await screen.findByLabelText("Chapter outline");
    expect(outline).toHaveTextContent("Chapter 1: Part 1");
    expect(outline).toHaveTextContent("Chapter 5: Part 5");
    expect(screen.getByTestId("book-first-lines")).toHaveTextContent(
      "Words of chapter 1.",
    );
    expect(screen.queryByText("[toc]")).toBeNull();
  });

  it("opens the full book a few chapters at a time, and never renders HTML", async () => {
    fetchArtifactText.mockResolvedValue(book(8));
    render(<NotionBookPreview args={args} />);
    const open = await screen.findByRole("button", {
      name: "Read the full book",
    });
    expect(open).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(open);
    const full = await screen.findByTestId("book-full-text");
    expect(full).toHaveTextContent("Words of chapter 1.");
    expect(full).not.toHaveTextContent("Words of chapter 8.");
    fireEvent.click(screen.getByRole("button", { name: /Show more chapters/ }));
    await waitFor(() =>
      expect(screen.getByTestId("book-full-text")).toHaveTextContent(
        "Words of chapter 4.",
      ),
    );
    // markup in the book stays visible text: no script element exists anywhere
    expect(document.querySelector("script")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Hide the full book" }));
    expect(screen.queryByTestId("book-full-text")).toBeNull();
  });

  it("says so when the text cannot be loaded but keeps the counts", async () => {
    fetchArtifactText.mockRejectedValue(new Error("no"));
    render(<NotionBookPreview args={args} />);
    expect(
      await screen.findByText(/The full text could not be loaded here/),
    ).toBeInTheDocument();
    expect(screen.getByText("612")).toBeInTheDocument();
  });
});

describe("the book inside the approval", () => {
  const approval = {
    id: "ap-1",
    tool_name: "notion_create_page_from_job",
    tier: "APPROVAL_REQUIRED",
    status: "pending",
    action_description: "Create the Notion page from the finished book",
    tool_args: args,
    payload_diff: "Create the Notion page 'Master Notes'. 8,123 words.",
    payload_hash: "h",
    requested_at: "2026-11-11T10:00:00Z",
    expires_at: "2099-11-11T10:30:00Z",
  } as unknown as ApprovalRequest;

  it("shows the book preview, the exact payload and click-only Approve and Reject", async () => {
    fetchArtifactText.mockResolvedValue(book(3));
    decideApproval.mockResolvedValue({ status: "approved" });
    render(<InlineApproval approval={approval} />);
    expect(
      screen.getByText("What will be written to Notion"),
    ).toBeInTheDocument();
    expect(await screen.findByLabelText("Chapter outline")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show exact payload" }));
    expect(screen.getByText(/content_sha256/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    expect(decideApproval).not.toHaveBeenCalled(); // nothing decides by itself
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(decideApproval).toHaveBeenCalledWith("ap-1", {
        action: "approve",
      }),
    );
  });
});
