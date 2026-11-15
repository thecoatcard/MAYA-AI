import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../lib/api-client";
import * as filesClient from "../../lib/files-client";
import { ConversationContext } from "../chat/ConversationContext";
import { FilesSection } from "./FilesSection";

const ok = {
  path: "C:\\Users\\ana\\Work",
  status: "ok",
  writable: true,
  name: "Work",
} as const;
const none = {
  path: null,
  status: "none",
  writable: false,
  name: null,
} as const;
const upload = {
  id: "u1",
  name: "plan.docx",
  size: 2048,
  kind: "docx",
  created_at: "2026-11-10T10:00:00Z",
};

type MayaBridge = {
  isDesktop: boolean;
  pickFolder?: () => Promise<string | null>;
  revealPath?: (p: string) => Promise<boolean>;
};
function setBridge(bridge: MayaBridge | undefined): void {
  (window as unknown as { maya?: MayaBridge }).maya = bridge;
}

describe("FilesSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(filesClient, "listUploads").mockResolvedValue({
      items: [upload],
      next_cursor: null,
    });
  });
  afterEach(() => setBridge(undefined));

  it("explains the folder and shows the browser path field when no folder is connected", async () => {
    vi.spyOn(filesClient, "getFolder").mockResolvedValue(none);
    render(<FilesSection />);
    expect(await screen.findByTestId("folder-none")).toBeTruthy();
    expect(
      screen.getByText(/never overwritten without your approval/),
    ).toBeTruthy();
    expect(screen.getByLabelText(/Folder path on the computer/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Choose folder/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Disconnect" })).toBeNull();
  });

  it("connects a typed path and shows name, path and status", async () => {
    vi.spyOn(filesClient, "getFolder").mockResolvedValue(none);
    const set = vi.spyOn(filesClient, "setFolder").mockResolvedValue(ok);
    render(<FilesSection />);
    const field = await screen.findByLabelText(/Folder path on the computer/);
    const connect = screen.getByRole("button", { name: "Connect" });
    expect((connect as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(field, { target: { value: "  C:\\Users\\ana\\Work " } });
    fireEvent.click(connect);
    await waitFor(() =>
      expect(set).toHaveBeenCalledWith("C:\\Users\\ana\\Work"),
    );
    const card = await screen.findByTestId("folder-card");
    expect(card.getAttribute("data-status")).toBe("ok");
    expect(screen.getByText("Connected")).toBeTruthy();
    expect(screen.getByText("C:\\Users\\ana\\Work")).toBeTruthy();
  });

  it("shows the API reason in plain words when the folder is refused", async () => {
    vi.spyOn(filesClient, "getFolder").mockResolvedValue(none);
    vi.spyOn(filesClient, "setFolder").mockRejectedValue(
      new ApiError(422, "validation_error", {
        message: "That is a system folder.",
      }),
    );
    render(<FilesSection />);
    fireEvent.change(
      await screen.findByLabelText(/Folder path on the computer/),
      {
        target: { value: "C:\\Windows" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("That is a system folder.")).toBeTruthy();
    expect(screen.getByTestId("folder-none")).toBeTruthy();
  });

  it("uses the desktop folder dialog and Open folder through window.maya", async () => {
    const pick = vi.fn(async () => "D:\\Docs");
    const reveal = vi.fn(async () => true);
    setBridge({ isDesktop: true, pickFolder: pick, revealPath: reveal });
    vi.spyOn(filesClient, "getFolder").mockResolvedValue(none);
    const set = vi
      .spyOn(filesClient, "setFolder")
      .mockResolvedValue({ ...ok, path: "D:\\Docs", name: "Docs" });
    render(<FilesSection />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Choose folder/ }),
    );
    await waitFor(() => expect(set).toHaveBeenCalledWith("D:\\Docs"));
    expect(screen.queryByLabelText(/Folder path on the computer/)).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Open folder" }));
    await waitFor(() => expect(reveal).toHaveBeenCalledWith("D:\\Docs"));
  });

  it("does nothing when the folder dialog is cancelled", async () => {
    setBridge({ isDesktop: true, pickFolder: async () => null });
    vi.spyOn(filesClient, "getFolder").mockResolvedValue(none);
    const set = vi.spyOn(filesClient, "setFolder");
    render(<FilesSection />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Choose folder/ }),
    );
    await waitFor(() => expect(screen.getByTestId("folder-none")).toBeTruthy());
    expect(set).not.toHaveBeenCalled();
  });

  it("says the folder is missing, with text and not colour alone", async () => {
    vi.spyOn(filesClient, "getFolder").mockResolvedValue({
      ...ok,
      status: "missing",
    });
    render(<FilesSection />);
    expect(await screen.findByText("Folder missing")).toBeTruthy();
    expect(screen.getByText(/cannot be found any more/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeTruthy();
  });

  it("says the folder is not writable", async () => {
    vi.spyOn(filesClient, "getFolder").mockResolvedValue({
      ...ok,
      status: "not_writable",
      writable: false,
    });
    render(<FilesSection />);
    expect(await screen.findByText("Not writable")).toBeTruthy();
    expect(screen.getByText(/cannot write to this folder/)).toBeTruthy();
  });

  it("disconnects and returns to the empty state", async () => {
    vi.spyOn(filesClient, "getFolder").mockResolvedValue(ok);
    const clear = vi
      .spyOn(filesClient, "clearFolder")
      .mockResolvedValue(undefined);
    render(<FilesSection />);
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(clear).toHaveBeenCalled());
    expect(await screen.findByTestId("folder-none")).toBeTruthy();
  });

  it("browser mode: a connected folder can be replaced by typing a new path and disconnected", async () => {
    setBridge(undefined);
    vi.spyOn(filesClient, "getFolder").mockResolvedValue(ok);
    const set = vi.spyOn(filesClient, "setFolder").mockResolvedValue({
      ...ok,
      path: "/home/you/MAYA-Work",
      name: "MAYA-Work",
    });
    const clear = vi.spyOn(filesClient, "clearFolder").mockResolvedValue();
    render(<FilesSection />);
    expect(await screen.findByTestId("folder-card")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Choose folder/ })).toBeNull();
    fireEvent.change(screen.getByLabelText(/Folder path on the computer/), {
      target: { value: "/home/you/MAYA-Work" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() =>
      expect(set).toHaveBeenCalledWith("/home/you/MAYA-Work"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(clear).toHaveBeenCalled());
    expect(await screen.findByTestId("folder-none")).toBeTruthy();
  });

  it("browser mode: shows a refused path reason and keeps the typed text", async () => {
    setBridge(undefined);
    vi.spyOn(filesClient, "getFolder").mockResolvedValue(none);
    vi.spyOn(filesClient, "setFolder").mockRejectedValue(
      new ApiError(422, "invalid_folder", {
        message: "That folder does not exist.",
      }),
    );
    render(<FilesSection />);
    const field = (await screen.findByLabelText(
      /Folder path on the computer/,
    )) as HTMLInputElement;
    fireEvent.change(field, { target: { value: "C:\\Nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("That folder does not exist.")).toBeTruthy();
    expect(field.value).toBe("C:\\Nope");
  });

  it("offers a retry when the folder cannot be loaded", async () => {
    const get = vi
      .spyOn(filesClient, "getFolder")
      .mockRejectedValueOnce(new Error("x"))
      .mockResolvedValueOnce(ok);
    render(<FilesSection />);
    expect(
      await screen.findByText(/working folder could not be loaded/i),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByTestId("folder-card")).toBeTruthy();
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("lists uploads, downloads and deletes after a confirmation", async () => {
    vi.spyOn(filesClient, "getFolder").mockResolvedValue(none);
    const download = vi
      .spyOn(filesClient, "downloadUpload")
      .mockResolvedValue();
    const del = vi.spyOn(filesClient, "deleteUpload").mockResolvedValue();
    render(<FilesSection />);
    expect(await screen.findByTestId("upload-row")).toBeTruthy();
    expect(screen.getByText("plan.docx")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Download plan.docx" }));
    await waitFor(() =>
      expect(download).toHaveBeenCalledWith("u1", "plan.docx"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete plan.docx" }));
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("u1"));
    expect(await screen.findByText(/No uploaded files yet/)).toBeTruthy();
  });

  it("shows a delete error without dropping the row", async () => {
    vi.spyOn(filesClient, "getFolder").mockResolvedValue(none);
    vi.spyOn(filesClient, "deleteUpload").mockRejectedValue(
      new ApiError(404, "not_found"),
    );
    render(<FilesSection />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Delete plan.docx" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText(/no longer exists/)).toBeTruthy();
    expect(screen.getByTestId("upload-row")).toBeTruthy();
  });

  it("Use in chat hands the file to the composer only inside a conversation", async () => {
    vi.spyOn(filesClient, "getFolder").mockResolvedValue(none);
    const { unmount } = render(<FilesSection />);
    await screen.findByTestId("upload-row");
    expect(
      screen.queryByRole("button", { name: /Use plan.docx in chat/ }),
    ).toBeNull();
    unmount();

    const addExisting = vi.fn();
    render(
      <ConversationContext.Provider
        value={{ attachments: { addExisting } } as never}
      >
        <FilesSection />
      </ConversationContext.Provider>,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Use plan.docx in chat/ }),
    );
    expect(addExisting).toHaveBeenCalledWith(upload);
    expect(screen.getByText(/Attached to your next message/)).toBeTruthy();
  });
});
