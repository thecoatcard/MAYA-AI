import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import * as filesClient from "../../lib/files-client";
import { MessageItem } from "./MessageItem";

describe("MessageItem attachments", () => {
  it("shows a chip per attached file with a download action", async () => {
    const download = vi
      .spyOn(filesClient, "downloadUpload")
      .mockResolvedValue();
    const message = {
      id: "m1",
      role: "user",
      content: "Summarise these",
      state: "complete",
      attachments: [
        { id: "u1", name: "plan.docx", kind: "docx" },
        { id: "u2", name: "data.csv", kind: "csv" },
      ],
    };
    render(<MessageItem message={message as never} />);
    expect(screen.getAllByTestId("attachment-chip")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Download plan.docx" }));
    await waitFor(() =>
      expect(download).toHaveBeenCalledWith("u1", "plan.docx"),
    );
  });

  it("shows no file list when there are no attachments", () => {
    const message = {
      id: "m2",
      role: "user",
      content: "Hi",
      state: "complete",
    };
    render(<MessageItem message={message as never} />);
    expect(screen.queryByRole("list", { name: "Attached files" })).toBeNull();
  });
});

describe("MessageItem deep research badge", () => {
  it("shows a small Deep research badge only on a message sent in that mode", () => {
    const base = {
      id: "m3",
      role: "user",
      content: "Compare",
      state: "complete",
    };
    const { rerender } = render(
      <MessageItem message={{ ...base, mode: "deep_research" } as never} />,
    );
    expect(screen.getByTestId("deep-research-badge").textContent).toContain(
      "Deep research",
    );
    rerender(<MessageItem message={base as never} />);
    expect(screen.queryByTestId("deep-research-badge")).toBeNull();
  });
});
