import { render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as filesClient from "../../lib/files-client";
import { WorkingFolderStatus } from "./WorkingFolderStatus";

afterEach(() => vi.restoreAllMocks());

describe("WorkingFolderStatus", () => {
  it("names the connected folder", async () => {
    vi.spyOn(filesClient, "getFolder").mockResolvedValue({
      path: "C:\Users\ana\Work",
      status: "ok",
      writable: true,
      name: "Work",
    });
    render(<WorkingFolderStatus />);
    const line = await screen.findByTestId("working-folder-status");
    expect(line.getAttribute("data-status")).toBe("ok");
    expect(line.textContent).toContain("Working folder: Work");
  });

  it("says when no folder is connected", async () => {
    vi.spyOn(filesClient, "getFolder").mockResolvedValue({
      path: null,
      status: "none",
      writable: false,
      name: null,
    });
    render(<WorkingFolderStatus />);
    expect(
      (await screen.findByTestId("working-folder-status")).textContent,
    ).toContain("No working folder");
  });

  it("shows nothing when the status cannot be read", async () => {
    vi.spyOn(filesClient, "getFolder").mockRejectedValue(new Error("down"));
    render(<WorkingFolderStatus />);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByTestId("working-folder-status")).toBeNull();
  });
});
