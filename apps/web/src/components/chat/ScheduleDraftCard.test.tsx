import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../lib/api-client";
import * as client from "../../lib/schedules-client";
import { ScheduleDraftCard } from "./ScheduleDraftCard";

vi.mock("../../lib/schedules-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/schedules-client")>()),
  getScheduleDraft: vi.fn(),
  confirmScheduleDraft: vi.fn(),
  cancelScheduleDraft: vi.fn(),
}));

const DRAFT = {
  id: "d1",
  preview_text: "AI news every day at 08:00 (Asia/Kolkata), as a PDF.",
  schedule: {},
  expires_at: null,
};

describe("ScheduleDraftCard", () => {
  beforeEach(() => {
    vi.mocked(client.getScheduleDraft).mockReset();
    vi.mocked(client.confirmScheduleDraft).mockReset();
    vi.mocked(client.cancelScheduleDraft).mockReset();
    vi.mocked(client.getScheduleDraft).mockResolvedValue(DRAFT);
  });

  it("shows the preview and creates nothing until Create schedule is clicked", async () => {
    render(<ScheduleDraftCard draftId="d1" />);
    expect(
      await screen.findByTestId("schedule-draft-preview"),
    ).toHaveTextContent("AI news every day at 08:00");
    expect(screen.getByText("Not created until you click.")).toBeTruthy();
    // No keyboard shortcut confirms it.
    for (const key of ["y", "Y", "Enter"]) {
      fireEvent.keyDown(document.body, { key });
      fireEvent.keyDown(screen.getByTestId("schedule-draft-card"), { key });
    }
    expect(client.confirmScheduleDraft).not.toHaveBeenCalled();
  });

  it("confirms on click and says where the schedule is", async () => {
    vi.mocked(client.confirmScheduleDraft).mockResolvedValue(
      {} as client.Schedule,
    );
    render(<ScheduleDraftCard draftId="d1" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Create schedule" }),
    );
    await waitFor(() =>
      expect(client.confirmScheduleDraft).toHaveBeenCalledWith("d1"),
    );
    const status = await screen.findByText(/Schedule created/);
    expect(status.closest("[aria-live]")).not.toBeNull();
    expect(
      screen.getByRole("link", { name: "Settings > Schedules" }),
    ).toHaveAttribute("href", "/settings#settings-schedules");
    expect(
      screen.queryByRole("button", { name: "Create schedule" }),
    ).toBeNull();
  });

  it("cancels on click and creates nothing", async () => {
    vi.mocked(client.cancelScheduleDraft).mockResolvedValue();
    render(<ScheduleDraftCard draftId="d1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await screen.findByText(/No schedule was created/)).toBeTruthy();
    expect(client.cancelScheduleDraft).toHaveBeenCalledWith("d1");
    expect(client.confirmScheduleDraft).not.toHaveBeenCalled();
  });

  it("shows a server error and stays confirmable", async () => {
    vi.mocked(client.confirmScheduleDraft).mockRejectedValue(
      new ApiError(429, "rate_limited", { retryAfterSeconds: 30 }),
    );
    render(<ScheduleDraftCard draftId="d1" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Create schedule" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Too many tries.*Nothing was created/,
    );
    expect(
      screen.getByRole("button", { name: "Create schedule" }),
    ).toBeTruthy();
  });

  it("is reload-safe: a mounted card refetches by id, and a used draft says so", async () => {
    const first = render(<ScheduleDraftCard draftId="d1" />);
    await screen.findByTestId("schedule-draft-preview");
    first.unmount();
    vi.mocked(client.getScheduleDraft).mockRejectedValue(
      new ApiError(404, "not_found"),
    );
    render(<ScheduleDraftCard draftId="d1" />);
    expect(await screen.findByText(/no longer waiting/)).toBeTruthy();
    expect(client.getScheduleDraft).toHaveBeenCalledTimes(2);
    expect(
      screen.queryByRole("button", { name: "Create schedule" }),
    ).toBeNull();
  });

  it("keeps controls exact and still with motion-none", async () => {
    render(<ScheduleDraftCard draftId="d1" />);
    const card = await screen.findByTestId("schedule-draft-card");
    expect(card.className).toContain("motion-none");
    expect(screen.getByTestId("schedule-draft-status")).toHaveTextContent(
      "review",
    );
  });
});
