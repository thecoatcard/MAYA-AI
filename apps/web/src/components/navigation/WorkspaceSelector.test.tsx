import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as desktopBridge from "../../lib/desktop-bridge";
import * as workspacesClient from "../../lib/workspaces-client";
import { WorkspaceSelector } from "./WorkspaceSelector";

const mockWorkspaces: workspacesClient.ProjectWorkspace[] = [
  {
    id: "ws-1",
    name: "project-alpha",
    path: "/home/user/project-alpha",
    is_default: true,
    status: "ok",
    writable: true,
    created_at: "2026-11-15T10:00:00Z",
  },
  {
    id: "ws-2",
    name: "project-beta",
    path: "/home/user/project-beta",
    is_default: false,
    status: "missing",
    writable: false,
    created_at: "2026-11-15T10:05:00Z",
  },
];

describe("WorkspaceSelector", () => {
  beforeEach(() => {
    vi.spyOn(workspacesClient, "listWorkspaces").mockResolvedValue(
      mockWorkspaces,
    );
    vi.spyOn(desktopBridge, "useDesktopBridge").mockReturnValue({
      isDesktop: false,
      pickFolder: null,
      revealPath: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders active workspace name and status dot", async () => {
    render(<WorkspaceSelector />);

    await waitFor(() => {
      expect(screen.getByTestId("active-workspace-name").textContent).toBe(
        "project-alpha",
      );
    });

    const statusDot = screen.getByTestId("workspace-status-dot");
    expect(statusDot.className).toContain("bg-maya-success");
  });

  it("shows 'No project folder' when no workspaces exist", async () => {
    vi.spyOn(workspacesClient, "listWorkspaces").mockResolvedValue([]);
    render(<WorkspaceSelector />);

    await waitFor(() => {
      expect(screen.getByTestId("active-workspace-name").textContent).toBe(
        "No project folder",
      );
    });
  });

  it("opens dropdown on trigger click and lists workspaces with check for active one", async () => {
    render(<WorkspaceSelector />);

    await waitFor(() => {
      expect(screen.getByTestId("active-workspace-name").textContent).toBe(
        "project-alpha",
      );
    });

    const trigger = screen.getByTestId("workspace-selector-trigger");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    const dropdown = screen.getByTestId("workspace-dropdown");
    expect(dropdown).toBeDefined();

    expect(screen.getByTestId("workspace-item-ws-1")).toBeDefined();
    expect(screen.getByTestId("workspace-item-ws-2")).toBeDefined();

    const activeItem = screen.getByTestId("workspace-item-ws-1");
    expect(activeItem.getAttribute("aria-selected")).toBe("true");
  });

  it("switches workspace on click and dispatches 'maya:workspace-changed'", async () => {
    const setDefaultSpy = vi
      .spyOn(workspacesClient, "setDefaultWorkspace")
      .mockResolvedValue({
        ...mockWorkspaces[1],
        is_default: true,
      });

    const eventListener = vi.fn();
    window.addEventListener("maya:workspace-changed", eventListener);

    render(<WorkspaceSelector />);

    await waitFor(() => {
      expect(screen.getByTestId("active-workspace-name").textContent).toBe(
        "project-alpha",
      );
    });

    fireEvent.click(screen.getByTestId("workspace-selector-trigger"));
    fireEvent.click(screen.getByTestId("workspace-item-ws-2"));

    await waitFor(() => {
      expect(setDefaultSpy).toHaveBeenCalledWith("ws-2");
      expect(eventListener).toHaveBeenCalled();
    });

    window.removeEventListener("maya:workspace-changed", eventListener);
  });

  it("closes dropdown on Escape key", async () => {
    render(<WorkspaceSelector />);

    await waitFor(() => {
      expect(screen.getByTestId("active-workspace-name").textContent).toBe(
        "project-alpha",
      );
    });

    fireEvent.click(screen.getByTestId("workspace-selector-trigger"));
    expect(screen.queryByTestId("workspace-dropdown")).not.toBeNull();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("workspace-dropdown")).toBeNull();
  });

  it("adds workspace via bridge.pickFolder() when available", async () => {
    const pickFolderMock = vi
      .fn()
      .mockResolvedValue("/home/user/project-gamma");
    vi.spyOn(desktopBridge, "useDesktopBridge").mockReturnValue({
      isDesktop: true,
      pickFolder: pickFolderMock,
      revealPath: null,
    });

    const registerSpy = vi
      .spyOn(workspacesClient, "registerWorkspace")
      .mockResolvedValue({
        id: "ws-3",
        name: "project-gamma",
        path: "/home/user/project-gamma",
        is_default: true,
        status: "ok",
        writable: true,
        created_at: "2026-11-15T10:10:00Z",
      });

    const eventListener = vi.fn();
    window.addEventListener("maya:workspace-changed", eventListener);

    render(<WorkspaceSelector />);

    await waitFor(() => {
      expect(screen.getByTestId("active-workspace-name").textContent).toBe(
        "project-alpha",
      );
    });

    fireEvent.click(screen.getByTestId("workspace-selector-trigger"));
    fireEvent.click(screen.getByTestId("add-workspace-button"));

    await waitFor(() => {
      expect(pickFolderMock).toHaveBeenCalled();
      expect(registerSpy).toHaveBeenCalledWith("/home/user/project-gamma");
      expect(eventListener).toHaveBeenCalled();
    });

    window.removeEventListener("maya:workspace-changed", eventListener);
  });

  it("falls back to window.prompt when desktop bridge is not available", async () => {
    const promptSpy = vi
      .spyOn(window, "prompt")
      .mockReturnValue("/home/user/project-from-prompt");

    const registerSpy = vi
      .spyOn(workspacesClient, "registerWorkspace")
      .mockResolvedValue({
        id: "ws-4",
        name: "project-from-prompt",
        path: "/home/user/project-from-prompt",
        is_default: true,
        status: "ok",
        writable: true,
        created_at: "2026-11-15T10:15:00Z",
      });

    render(<WorkspaceSelector />);

    await waitFor(() => {
      expect(screen.getByTestId("active-workspace-name").textContent).toBe(
        "project-alpha",
      );
    });

    fireEvent.click(screen.getByTestId("workspace-selector-trigger"));
    fireEvent.click(screen.getByTestId("add-workspace-button"));

    await waitFor(() => {
      expect(promptSpy).toHaveBeenCalled();
      expect(registerSpy).toHaveBeenCalledWith(
        "/home/user/project-from-prompt",
      );
    });
  });

  it("displays error message if registering workspace fails", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("/invalid/path");
    vi.spyOn(workspacesClient, "registerWorkspace").mockRejectedValue(
      new Error("Path is invalid"),
    );

    render(<WorkspaceSelector />);

    await waitFor(() => {
      expect(screen.getByTestId("active-workspace-name").textContent).toBe(
        "project-alpha",
      );
    });

    fireEvent.click(screen.getByTestId("workspace-selector-trigger"));
    fireEvent.click(screen.getByTestId("add-workspace-button"));

    await waitFor(() => {
      expect(screen.getByTestId("workspace-error-message")).toBeDefined();
    });
  });

  it("refreshes workspace list when 'maya:workspace-changed' is dispatched", async () => {
    const listSpy = vi
      .spyOn(workspacesClient, "listWorkspaces")
      .mockResolvedValueOnce(mockWorkspaces)
      .mockResolvedValueOnce([
        {
          ...mockWorkspaces[0],
          name: "updated-project-name",
        },
      ]);

    render(<WorkspaceSelector />);

    await waitFor(() => {
      expect(screen.getByTestId("active-workspace-name").textContent).toBe(
        "project-alpha",
      );
    });

    window.dispatchEvent(new CustomEvent("maya:workspace-changed"));

    await waitFor(() => {
      expect(screen.getByTestId("active-workspace-name").textContent).toBe(
        "updated-project-name",
      );
    });

    expect(listSpy).toHaveBeenCalledTimes(2);
  });
});
