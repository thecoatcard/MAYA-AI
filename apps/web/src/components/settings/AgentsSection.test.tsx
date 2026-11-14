import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../../lib/agents-client";
import { AgentsSection } from "./AgentsSection";

vi.mock("../../lib/agents-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/agents-client")>()),
  getAgents: vi.fn(),
  previewAgent: vi.fn(),
  createAgent: vi.fn(),
  setAgentEnabled: vi.fn(),
}));

const builtin = ["research", "builder", "operator"].map((id) => ({
  id,
  label: id[0].toUpperCase() + id.slice(1),
  summary: `${id} summary`,
  capabilities: ["a", "b"],
  available: true,
}));
const custom = {
  name: "weekly-reporter",
  file: ".maya/agents/weekly-reporter.md",
  valid: true,
  enabled: true,
  description: "Turns notes into a report.",
  instructions: "Write the report.",
  capabilities: ["web_search"],
  model: null,
  error: null,
};
const overview = (folderConnected: boolean, list: unknown[] = []) => ({
  agents: builtin,
  custom: list,
  folderConnected,
  capabilities: { web_search: { tools: ["web_search"], allowed: true } },
});

describe("AgentsSection", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the three agents and asks for a folder when none is connected", async () => {
    vi.mocked(client.getAgents).mockResolvedValue(overview(false) as never);
    render(<AgentsSection />);
    expect(await screen.findByText("Research")).toBeTruthy();
    expect(screen.getByText("Builder")).toBeTruthy();
    expect(screen.getByText("Operator")).toBeTruthy();
    expect(screen.getByText(/Connect a working folder/)).toBeTruthy();
  });

  it("lists custom agents and disables one", async () => {
    vi.mocked(client.getAgents).mockResolvedValue(
      overview(true, [custom]) as never,
    );
    vi.mocked(client.setAgentEnabled).mockResolvedValue();
    render(<AgentsSection />);
    expect(await screen.findByText("weekly-reporter")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    await waitFor(() =>
      expect(client.setAgentEnabled).toHaveBeenCalledWith(
        "weekly-reporter",
        false,
      ),
    );
  });

  it("writes nothing until the user previews and approves", async () => {
    vi.mocked(client.getAgents).mockResolvedValue(overview(true) as never);
    vi.mocked(client.previewAgent).mockResolvedValue({
      path: ".maya/agents/new-agent.md",
      content: "---\nname: new-agent\n---\n",
      agent: custom,
    } as never);
    vi.mocked(client.createAgent).mockResolvedValue();
    render(<AgentsSection />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Write it myself" }),
    );
    fireEvent.change(screen.getByLabelText(/Name/), {
      target: { value: "new-agent" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Preview file" }));
    expect(await screen.findByText(/will be written to/)).toBeTruthy();
    expect(client.createAgent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Approve and create" }));
    await waitFor(() => expect(client.createAgent).toHaveBeenCalledTimes(1));
  });

  it("after preview is shown, submitting the form saves instead of re-previewing", async () => {
    vi.mocked(client.getAgents).mockResolvedValue(overview(true) as never);
    vi.mocked(client.previewAgent).mockResolvedValue({
      path: ".maya/agents/new-agent.md",
      content: "---\nname: new-agent\n---\n",
      agent: custom,
    } as never);
    vi.mocked(client.createAgent).mockResolvedValue();
    render(<AgentsSection />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Write it myself" }),
    );
    fireEvent.change(screen.getByLabelText(/Name/), {
      target: { value: "new-agent" },
    });
    fireEvent.submit(
      screen.getByRole("button", { name: "Preview file" }).closest("form")!,
    );
    expect(await screen.findByText(/will be written to/)).toBeTruthy();
    expect(client.previewAgent).toHaveBeenCalledTimes(1);
    expect(client.createAgent).not.toHaveBeenCalled();

    // Now submit the form again (e.g. hitting Enter in an input)
    fireEvent.submit(
      screen
        .getByRole("button", { name: "Approve and create" })
        .closest("form")!,
    );
    await waitFor(() => expect(client.createAgent).toHaveBeenCalledTimes(1));
    expect(client.previewAgent).toHaveBeenCalledTimes(1);
  });
});
