import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../../lib/settings-client";
import type { NotionAutoApproveSettings } from "../../lib/settings-client";
import { NotionAutoApprove } from "./NotionAutoApprove";

const OFF: NotionAutoApproveSettings = {
  enabled: false,
  scopes: {
    create_page: false,
    add_row: false,
    append_blocks: false,
    update_row: false,
  },
  version: 0,
  hourly_cap: 30,
  daily_cap: 200,
  used_last_hour: 0,
  used_today: 0,
  always_manual: ["Moving a page to the trash (archive)", "Deleting a block"],
  last_auto_approved: null,
};
const ON: NotionAutoApproveSettings = { ...OFF, enabled: true, version: 1 };

function sw(name: RegExp): HTMLElement {
  return screen.getByRole("switch", { name });
}

describe("NotionAutoApprove", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("is off by default, scopes are disabled, the manual list and caps show", async () => {
    vi.spyOn(client, "getNotionAutoApprove").mockResolvedValue(OFF);
    render(<NotionAutoApprove />);
    const master = await screen.findByRole("switch", {
      name: "Auto-approve Notion writes",
    });
    expect(master).toHaveAttribute("aria-checked", "false");
    expect(sw(/Create pages/)).toBeDisabled();
    expect(sw(/Edit database row properties/)).toBeDisabled();
    const manual = screen.getByTestId("notion-always-manual");
    expect(within(manual).getByText(/Deleting a block/)).toBeInTheDocument();
    expect(screen.getByText(/30 per hour and 200 per day/)).toBeInTheDocument();
    expect(screen.queryByText(/Last auto-approved/)).toBeNull();
  });

  it("turning on asks first, sends nothing until confirmed, then sends confirm", async () => {
    vi.spyOn(client, "getNotionAutoApprove").mockResolvedValue(OFF);
    const update = vi
      .spyOn(client, "updateNotionAutoApprove")
      .mockResolvedValue(ON);
    render(<NotionAutoApprove />);
    fireEvent.click(
      await screen.findByRole("switch", { name: "Auto-approve Notion writes" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("auto-approve-will")).toBeInTheDocument();
    expect(within(dialog).getByTestId("auto-approve-manual")).toHaveTextContent(
      "Deleting a block",
    );
    expect(update).not.toHaveBeenCalled();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Turn on auto-approval" }),
    );
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({ enabled: true, confirm: true }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sw(/Auto-approve Notion writes/)).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(sw(/Create pages/)).not.toBeDisabled();
  });

  it("cancel leaves it off and sends nothing", async () => {
    vi.spyOn(client, "getNotionAutoApprove").mockResolvedValue(OFF);
    const update = vi.spyOn(client, "updateNotionAutoApprove");
    render(<NotionAutoApprove />);
    fireEvent.click(
      await screen.findByRole("switch", { name: "Auto-approve Notion writes" }),
    );
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Cancel",
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(update).not.toHaveBeenCalled();
    expect(sw(/Auto-approve Notion writes/)).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("a scope switch saves at once and shows Saved", async () => {
    vi.spyOn(client, "getNotionAutoApprove").mockResolvedValue(ON);
    const update = vi
      .spyOn(client, "updateNotionAutoApprove")
      .mockResolvedValue({
        ...ON,
        scopes: { ...ON.scopes, add_row: true },
        version: 2,
      });
    render(<NotionAutoApprove />);
    fireEvent.click(
      await screen.findByRole("switch", { name: /Add database rows/ }),
    );
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({ scopes: { add_row: true } }),
    );
    await waitFor(() => expect(screen.getByText("Saved")).toBeInTheDocument());
    expect(sw(/Add database rows/)).toHaveAttribute("aria-checked", "true");
  });

  it("turning the master off needs no dialog", async () => {
    vi.spyOn(client, "getNotionAutoApprove").mockResolvedValue(ON);
    const update = vi
      .spyOn(client, "updateNotionAutoApprove")
      .mockResolvedValue(OFF);
    render(<NotionAutoApprove />);
    fireEvent.click(
      await screen.findByRole("switch", { name: "Auto-approve Notion writes" }),
    );
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({ enabled: false }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows a plain error and keeps the old state when saving fails", async () => {
    vi.spyOn(client, "getNotionAutoApprove").mockResolvedValue(ON);
    vi.spyOn(client, "updateNotionAutoApprove").mockRejectedValue(
      new Error("x"),
    );
    render(<NotionAutoApprove />);
    fireEvent.click(
      await screen.findByRole("switch", { name: /Create pages/ }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Nothing was changed");
    expect(sw(/Create pages/)).toHaveAttribute("aria-checked", "false");
  });

  it("a failure in the confirm dialog keeps it open with the error", async () => {
    vi.spyOn(client, "getNotionAutoApprove").mockResolvedValue(OFF);
    vi.spyOn(client, "updateNotionAutoApprove").mockRejectedValue(
      new Error("x"),
    );
    render(<NotionAutoApprove />);
    fireEvent.click(
      await screen.findByRole("switch", { name: "Auto-approve Notion writes" }),
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Turn on auto-approval" }),
    );
    expect(await within(dialog).findByRole("alert")).toBeInTheDocument();
    expect(sw(/Auto-approve Notion writes/)).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("a load failure offers Retry and shows no switches", async () => {
    vi.spyOn(client, "getNotionAutoApprove").mockRejectedValue(new Error("x"));
    render(<NotionAutoApprove />);
    expect(await screen.findByText(/could not be loaded/)).toBeInTheDocument();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("shows the last auto-approval only when the server has one", async () => {
    vi.spyOn(client, "getNotionAutoApprove").mockResolvedValue({
      ...ON,
      last_auto_approved: {
        at: "2026-11-12T10:00:00+00:00",
        tool: "notion_add_row",
      },
    });
    render(<NotionAutoApprove />);
    expect(await screen.findByText(/Last auto-approved/)).toHaveTextContent(
      "add row",
    );
  });
});
