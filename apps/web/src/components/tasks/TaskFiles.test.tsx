import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listArtifacts = vi.hoisted(() => vi.fn());
const downloadArtifact = vi.hoisted(() => vi.fn());
vi.mock("../../lib/artifacts-client", () => ({
  listArtifacts: (id: string) => listArtifacts(id),
  downloadArtifact: (...a: unknown[]) => downloadArtifact(...a),
}));
vi.mock("../files/TaskOutputLocation", () => ({
  TaskOutputLocation: () => <i data-testid="output-location" />,
}));

import { fireEvent } from "@testing-library/react";
import { TaskFiles } from "./TaskFiles";

const file = (id: string, name: string, type: string) => ({
  id,
  file_name: name,
  file_type: type,
  size_bytes: 2048,
});

describe("TaskFiles", () => {
  beforeEach(() => {
    listArtifacts.mockReset();
    downloadArtifact.mockReset();
  });

  it("renders nothing when the task made no files", async () => {
    listArtifacts.mockResolvedValue([]);
    const { container } = render(
      <TaskFiles taskId="t" task={{ subtasks: [] }} phase="completed" />,
    );
    await waitFor(() => expect(listArtifacts).toHaveBeenCalledWith("t"));
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a card per file with a Download that keeps name and extension", async () => {
    listArtifacts.mockResolvedValue([
      file("a1", "report.pdf", "pdf"),
      file("a2", "site", "zip"),
    ]);
    render(<TaskFiles taskId="t" task={{ subtasks: [] }} phase="completed" />);
    const cards = await screen.findAllByTestId("file-card");
    expect(cards).toHaveLength(2);
    expect(within(cards[1]).getByText(/ZIP · 2.0 KB/)).toBeInTheDocument();
    expect(screen.getByTestId("output-location")).toBeInTheDocument();
    downloadArtifact.mockResolvedValue(undefined);
    fireEvent.click(
      within(cards[1]).getByRole("button", { name: /download/i }),
    );
    expect(downloadArtifact).toHaveBeenCalledWith("a2", "site", "zip");
  });
});
