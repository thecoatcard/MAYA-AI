import React from "react";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ArtifactsManagerView } from "./ArtifactsManagerView";
import * as artifactsClient from "../../lib/artifacts-client";

vi.mock("../../lib/artifacts-client", () => ({
  listArtifacts: vi.fn(),
  downloadArtifact: vi.fn(),
  deleteArtifact: vi.fn(),
  canOpenNatively: vi.fn(),
  openArtifactNatively: vi.fn(),
  artifactToFile: vi.fn(),
}));

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const addFiles = vi.fn();
vi.mock("../chat/ConversationContext", () => ({
  useConversationRuntime: () => ({ attachments: { addFiles } }),
}));

function art(
  over: Partial<artifactsClient.ArtifactMetadata> = {},
): artifactsClient.ArtifactMetadata {
  return {
    id: "art-1",
    workspace_id: "ws-1",
    task_id: "task-1",
    user_id: "u-1",
    file_name: "plan.xlsx",
    file_type: "xlsx",
    size_bytes: 40960,
    sha256: "fedcba98",
    verification_status: "verified",
    verification_report: { row_count: 100 },
    created_at: "2026-11-07T10:00:00Z",
    ...over,
  };
}

async function renderWith(items: artifactsClient.ArtifactMetadata[]) {
  vi.mocked(artifactsClient.listArtifacts).mockResolvedValueOnce(items);
  render(<ArtifactsManagerView />);
  await waitFor(() =>
    expect(screen.queryByText("Loading artifacts…")).toBeNull(),
  );
}

describe("ArtifactsManagerView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(artifactsClient.canOpenNatively).mockReturnValue(true);
  });

  it("shows name, type, size, date and the actions, and no report or preview", async () => {
    await renderWith([art()]);
    const row = await screen.findByTestId("artifact-row");
    expect(within(row).getByText("plan.xlsx")).toBeInTheDocument();
    expect(within(row).getByText(/XLSX · 40 KB/)).toBeInTheDocument();
    for (const name of ["Open", "Ask AI", "Delete", "Download"]) {
      expect(
        within(row).getByRole("button", { name: new RegExp(`^${name}`) }),
      ).toBeInTheDocument();
    }
    expect(screen.queryByText(/audit/i)).toBeNull();
    expect(screen.queryByText(/SHA-256/i)).toBeNull();
    expect(screen.queryByText(/verification/i)).toBeNull();
    expect(screen.queryByText("100")).toBeNull();
    // No bare <a download> links: downloads are authenticated buttons.
    expect(screen.queryAllByRole("link", { name: /download/i })).toHaveLength(
      0,
    );
  });

  it("Open asks the desktop shell and shows a plain note when it refuses", async () => {
    vi.mocked(artifactsClient.openArtifactNatively)
      .mockResolvedValueOnce({ ok: true })
      .mockResolvedValueOnce({ ok: false, reason: "blocked-type" });
    await renderWith([art()]);
    fireEvent.click(await screen.findByRole("button", { name: "Open" }));
    await waitFor(() =>
      expect(artifactsClient.openArtifactNatively).toHaveBeenCalledTimes(1),
    );
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/safety/i);
  });

  it("in a plain browser the Open action becomes a labelled Download", async () => {
    vi.mocked(artifactsClient.canOpenNatively).mockReturnValue(false);
    await renderWith([art()]);
    expect(
      await screen.findByRole("button", { name: /download to open/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open" })).toBeNull();
    expect(screen.getByTestId("browser-note")).toHaveTextContent(/desktop app/);
    vi.mocked(artifactsClient.downloadArtifact).mockResolvedValueOnce();
    fireEvent.click(screen.getByRole("button", { name: /download to open/i }));
    expect(artifactsClient.downloadArtifact).toHaveBeenCalledWith(
      "art-1",
      "plan.xlsx",
      "xlsx",
    );
  });

  it("Ask AI attaches the file to the chat and goes there", async () => {
    const file = new File(["x"], "plan.xlsx");
    vi.mocked(artifactsClient.artifactToFile).mockResolvedValueOnce(file);
    await renderWith([art()]);
    fireEvent.click(await screen.findByRole("button", { name: /Ask AI/ }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    expect(addFiles).toHaveBeenCalledWith([file]);
  });

  it("Ask AI reports a failure and stays on the page", async () => {
    vi.mocked(artifactsClient.artifactToFile).mockRejectedValueOnce(
      new Error("nope"),
    );
    await renderWith([art()]);
    fireEvent.click(await screen.findByRole("button", { name: /Ask AI/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/attached/i);
    expect(push).not.toHaveBeenCalled();
  });

  it("Delete asks first, then removes the row", async () => {
    vi.mocked(artifactsClient.deleteArtifact).mockResolvedValueOnce();
    await renderWith([art()]);
    fireEvent.click(await screen.findByRole("button", { name: /^Delete/ }));
    expect(artifactsClient.deleteArtifact).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: /Delete file/ }),
    );
    await waitFor(() =>
      expect(artifactsClient.deleteArtifact).toHaveBeenCalledWith("art-1"),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("artifact-row")).toBeNull(),
    );
  });

  it("Cancel in the dialog deletes nothing, and a failed delete keeps the row", async () => {
    vi.mocked(artifactsClient.deleteArtifact).mockRejectedValueOnce(
      new Error("500"),
    );
    await renderWith([art()]);
    fireEvent.click(await screen.findByRole("button", { name: /^Delete/ }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Cancel",
      }),
    );
    expect(artifactsClient.deleteArtifact).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^Delete/ }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: /Delete file/,
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /not be deleted/,
    );
    expect(screen.getByTestId("artifact-row")).toBeInTheDocument();
  });

  it("a file in the connected folder has Open and Ask AI but no Delete", async () => {
    await renderWith([art({ in_connected_folder: true })]);
    const row = await screen.findByTestId("artifact-row");
    expect(within(row).getByRole("button", { name: "Open" })).toBeEnabled();
    expect(within(row).getByRole("button", { name: /Ask AI/ })).toBeEnabled();
    expect(within(row).queryByRole("button", { name: /^Delete/ })).toBeNull();
    expect(within(row).getByText(/Stays on your disk/)).toBeInTheDocument();
  });

  it("shows an error with retry when a download fails", async () => {
    vi.mocked(artifactsClient.downloadArtifact).mockRejectedValueOnce(
      new Error("401"),
    );
    await renderWith([art({ id: "art-104", file_name: "x.xlsx" })]);
    fireEvent.click(await screen.findByRole("button", { name: /^Download/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/failed/i);
    expect(
      screen.getByRole("button", { name: /retry download/i }),
    ).toBeInTheDocument();
  });

  it("shows a load error with retry instead of an empty list", async () => {
    vi.mocked(artifactsClient.listArtifacts).mockRejectedValueOnce(
      new Error("boom"),
    );
    render(<ArtifactsManagerView />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Could not load artifacts/,
    );
    expect(screen.queryByText(/No deliverables produced yet/)).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("shows rows 50 at a time with a real total", async () => {
    const many = Array.from({ length: 120 }, (_, i) =>
      art({ id: `id-${i}`, file_name: `file-${i}.xlsx` }),
    );
    await renderWith(many);
    await screen.findByText("Showing 50 of 120");
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(await screen.findByText("Showing 100 of 120")).toBeInTheDocument();
  });
});
