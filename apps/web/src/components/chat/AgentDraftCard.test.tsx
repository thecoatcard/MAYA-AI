import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../../lib/agents-client";
import { AgentDraftCard } from "./AgentDraftCard";

vi.mock("../../lib/agents-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/agents-client")>()),
  createAgent: vi.fn(),
  previewAgent: vi.fn(),
}));

const draft = {
  name: "weekly-reporter",
  description: "Writes a weekly report.",
  path: ".maya/agents/weekly-reporter.md",
  content: "---\nname: weekly-reporter\n---\nWrite the report.",
  capabilities: ["read_files"],
  instructions: "Write the report.",
};

describe("AgentDraftCard", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the exact file and writes nothing until Approve", async () => {
    vi.mocked(client.createAgent).mockResolvedValue();
    render(<AgentDraftCard draft={draft} />);
    expect(screen.getByText(/will be written to/)).toBeTruthy();
    expect(
      screen.getByText(/Write the report\./, { selector: "pre" }),
    ).toBeTruthy();
    expect(client.createAgent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(client.createAgent).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/created in/)).toBeTruthy();
  });

  it("Cancel discards the draft without calling the API", () => {
    render(<AgentDraftCard draft={draft} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText(/Nothing was saved/)).toBeTruthy();
    expect(client.createAgent).not.toHaveBeenCalled();
  });

  it("Edit re-previews the changed text and Approve sends the edit", async () => {
    vi.mocked(client.previewAgent).mockResolvedValue({
      path: draft.path,
      content: "---\nname: weekly-reporter\n---\nNEW TEXT",
      agent: {} as never,
    });
    vi.mocked(client.createAgent).mockResolvedValue();
    render(<AgentDraftCard draft={draft} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: "NEW TEXT" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Preview changes" }));
    expect(
      await screen.findByText(/NEW TEXT/, { selector: "pre" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(client.createAgent).toHaveBeenCalledWith(
        expect.objectContaining({ instructions: "NEW TEXT" }),
      ),
    );
  });

  it("reports a failed write and stays reviewable", async () => {
    vi.mocked(client.createAgent).mockRejectedValue(new Error("boom"));
    render(<AgentDraftCard draft={draft} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText(/could not be done/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  it("keeps controls exact and still with motion-none", () => {
    const { container } = render(<AgentDraftCard draft={draft} />);
    const section = container.querySelector("section");
    expect(section?.className).toContain("motion-none");
    expect(screen.getByText("review")).toBeTruthy();
  });
});
