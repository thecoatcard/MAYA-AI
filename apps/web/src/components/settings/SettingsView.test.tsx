import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsView } from "./SettingsView";
import { sectionFromHash } from "./sections";
import * as systemClient from "../../lib/system-client";
import * as settingsClient from "../../lib/settings-client";

// This file replaces the tests of the old single-page Settings (one long scroll with every
// section visible). The page is now a section list on the left and one section at a time; the
// behaviors those tests protected (optimistic save and rollback, honest account facts, retry,
// sign out, no invented values) are kept below, reached through the section hash.

vi.mock("../../lib/settings-client", () => ({
  getChatSettings: vi.fn(),
  updateChatSettings: vi.fn(),
}));

vi.mock("../../lib/auth-client", () => ({
  signOut: vi.fn(),
}));

vi.mock("../../lib/files-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/files-client")>()),
  getFolder: vi.fn(async () => ({
    path: null,
    status: "none",
    writable: false,
    name: null,
  })),
  listUploads: vi.fn(async () => ({ items: [], next_cursor: null })),
}));

vi.mock("../../lib/agents-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/agents-client")>()),
  getAgents: vi.fn(async () => ({
    agents: [],
    custom: [],
    folderConnected: false,
    capabilities: {},
  })),
}));

vi.mock("../../lib/connections-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/connections-client")>()),
  listConnections: vi.fn(async () => ({
    encryption_available: true,
    connections: [
      {
        provider: "mail",
        label: "Mailbox",
        state: "not_connected",
        source: null,
        message: "No mailbox connected.",
        account: null,
        scopes: null,
        database_id: null,
        database_title: null,
        last_tested_at: null,
        takes_database_id: false,
        kind: "mailbox",
        mailbox: null,
      },
    ],
  })),
}));

vi.mock("../../lib/system-client", () => ({
  getPrincipalMe: vi.fn(),
  checkSystemHealth: vi.fn(),
}));

function go(hash: string): void {
  window.location.hash = hash;
}

describe("sectionFromHash", () => {
  it("accepts the full id, the short name and aliases, and rejects the rest", () => {
    expect(sectionFromHash("#settings-files")).toBe("files");
    expect(sectionFromHash("#connections")).toBe("connections");
    expect(sectionFromHash("#Mailbox")).toBe("connections");
    expect(sectionFromHash("#nope")).toBeNull();
    expect(sectionFromHash("")).toBeNull();
  });
});

describe("SettingsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    go("");
    document.documentElement.removeAttribute("data-theme");
    vi.mocked(systemClient.getPrincipalMe).mockResolvedValue({
      user_id: "usr_maya_specialist",
      workspace_id: "ws_maya_enterprise",
      role: "admin",
    });
    vi.mocked(systemClient.checkSystemHealth).mockResolvedValue({
      status: "ready",
    });
    vi.mocked(settingsClient.getChatSettings).mockResolvedValue({
      intent_classifier: false,
      classifier_available: true,
      memory_in_chat: true,
    });
  });

  it("starts on General and lists every real section in the navigation", async () => {
    render(<SettingsView />);
    expect(
      screen.getByRole("heading", { name: "Settings", level: 1 }),
    ).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Settings sections" });
    const labels = Array.from(nav.querySelectorAll("a")).map(
      (a) => a.textContent,
    );
    expect(labels).toEqual([
      "General",
      "Connections",
      "Files and working folder",
      "Schedules",
      "Agents",
      "Memory and data",
      "Privacy and security",
      "About",
    ]);
    expect(
      screen.getByRole("heading", { name: "General", level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "General" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await screen.findByRole("switch", { name: "Task detection" });
  });

  it.each([
    ["#connections", "Connections"],
    ["#settings-connections", "Connections"],
    ["#settings-files", "Files and working folder"],
    ["#settings-agents", "Agents"],
    ["#privacy", "Privacy and security"],
  ])("opens the right section for the deep link %s", async (hash, title) => {
    go(hash);
    render(<SettingsView />);
    expect(
      await screen.findByRole("heading", { name: title, level: 2 }),
    ).toBeInTheDocument();
  });

  it("follows hash changes and a click writes the hash", async () => {
    render(<SettingsView />);
    act(() => go("#about"));
    expect(
      await screen.findByRole("heading", { name: "About", level: 2 }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Connections" }));
    expect(window.location.hash).toBe("#settings-connections");
    expect(
      await screen.findByRole("heading", { name: "Connections", level: 2 }),
    ).toBeInTheDocument();
    // The mailbox is a first-class card in Connections.
    expect(await screen.findByTestId("connection-mail")).toBeInTheDocument();
  });

  it("moves focus to the section title after choosing a section", async () => {
    render(<SettingsView />);
    fireEvent.click(screen.getByRole("link", { name: "About" }));
    const title = await screen.findByRole("heading", {
      name: "About",
      level: 2,
    });
    await waitFor(() => expect(title).toHaveFocus());
  });

  it("moves between navigation links with the arrow keys, Home and End", () => {
    render(<SettingsView />);
    const first = screen.getByRole("link", { name: "General" });
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(screen.getByRole("link", { name: "Connections" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement as Element, { key: "End" });
    expect(screen.getByRole("link", { name: "About" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement as Element, { key: "ArrowDown" });
    expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: "ArrowUp" });
    expect(screen.getByRole("link", { name: "About" })).toHaveFocus();
  });

  it("offers a labelled select with the same sections for narrow windows", async () => {
    render(<SettingsView />);
    const select = screen.getByLabelText("Settings section");
    expect(select.tagName).toBe("SELECT");
    expect(select.querySelectorAll("option")).toHaveLength(8);
    fireEvent.change(select, { target: { value: "memory" } });
    expect(
      await screen.findByRole("heading", { name: "Memory and data", level: 2 }),
    ).toBeInTheDocument();
  });

  it("toggles task detection optimistically and saves", async () => {
    vi.mocked(settingsClient.updateChatSettings).mockResolvedValue({
      intent_classifier: true,
      classifier_available: true,
      memory_in_chat: true,
    });
    render(<SettingsView />);
    const toggle = await screen.findByRole("switch", {
      name: "Task detection",
    });
    await waitFor(() => expect(toggle).not.toBeDisabled());
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await waitFor(() =>
      expect(settingsClient.updateChatSettings).toHaveBeenCalledWith({
        intent_classifier: true,
      }),
    );
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("rolls back and shows an error when saving fails", async () => {
    vi.mocked(settingsClient.updateChatSettings).mockRejectedValue(
      new Error("x"),
    );
    render(<SettingsView />);
    const toggle = await screen.findByRole("switch", {
      name: "Task detection",
    });
    await waitFor(() => expect(toggle).not.toBeDisabled());
    fireEvent.click(toggle);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not be saved/i,
    );
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).not.toBeDisabled();
  });

  it("disables task detection when the deployment has it turned off", async () => {
    vi.mocked(settingsClient.getChatSettings).mockResolvedValue({
      intent_classifier: false,
      classifier_available: false,
      memory_in_chat: true,
    });
    render(<SettingsView />);
    const toggle = await screen.findByRole("switch", {
      name: "Task detection",
    });
    await waitFor(() => expect(toggle).toBeDisabled());
    expect(
      screen.getByText("Not available on this server"),
    ).toBeInTheDocument();
  });

  it("toggles memory in chat in Memory and data: saves only that field", async () => {
    go("#memory");
    vi.mocked(settingsClient.updateChatSettings).mockResolvedValue({
      intent_classifier: false,
      classifier_available: true,
      memory_in_chat: false,
    });
    render(<SettingsView />);
    const toggle = await screen.findByRole("switch", {
      name: "Memory in chat and voice",
    });
    await waitFor(() => expect(toggle).not.toBeDisabled());
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(settingsClient.updateChatSettings).toHaveBeenCalledWith({
        memory_in_chat: false,
      }),
    );
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("points the delete actions at the pages that confirm them", async () => {
    go("#memory");
    render(<SettingsView />);
    expect(
      await screen.findByRole("heading", { name: "Danger zone" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Tasks" })).toHaveAttribute(
      "href",
      "/tasks",
    );
    expect(
      screen.getByRole("link", { name: "Go to Artifacts" }),
    ).toHaveAttribute("href", "/artifacts");
    expect(
      screen.getByRole("link", { name: "Open the Memory page" }),
    ).toHaveAttribute("href", "/memory");
  });

  it("shows a retry when the chat settings cannot be loaded", async () => {
    vi.mocked(settingsClient.getChatSettings).mockRejectedValueOnce(
      new Error("x"),
    );
    render(<SettingsView />);
    expect(
      await screen.findByText("Chat settings could not be loaded"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(
      await screen.findByRole("switch", { name: "Task detection" }),
    ).toBeInTheDocument();
  });

  it("switches theme between light and dark and offers no system option", async () => {
    render(<SettingsView />);
    const light = await screen.findByRole("radio", { name: "Light" });
    const dark = screen.getByRole("radio", { name: "Dark" });
    fireEvent.click(light);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(light).toBeChecked();
    fireEvent.click(dark);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.queryByRole("radio", { name: /system/i })).toBeNull();
  });

  it("shows the real account values and no invented ones", async () => {
    go("#privacy");
    render(<SettingsView />);
    await waitFor(() => {
      expect(screen.getByText("usr_maya_specialist")).toBeInTheDocument();
      expect(screen.getByText("ws_maya_enterprise")).toBeInTheDocument();
    });
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.queryByText("Email")).toBeNull(); // /auth/me does not send one today
    expect(screen.getByText(/Gemini API/)).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    for (const invented of [
      /us-central1/i,
      /LEVEL 4/i,
      /Phase 08/i,
      /pgvector/i,
      /Redis/i,
      /LangGraph/i,
      /Model Routing/i,
    ]) {
      expect(text).not.toMatch(invented);
    }
  });

  it("shows an error with retry instead of made-up identity when the account fails", async () => {
    go("#privacy");
    vi.mocked(systemClient.getPrincipalMe).mockRejectedValueOnce(
      new Error("x"),
    );
    render(<SettingsView />);
    expect(await screen.findByText(/could not be loaded/i)).toBeInTheDocument();
    expect(screen.queryByText("anonymous_operator")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText("usr_maya_specialist")).toBeInTheDocument();
  });

  it("signs out from Privacy and security", async () => {
    go("#privacy");
    const auth = await import("../../lib/auth-client");
    vi.mocked(auth.signOut).mockResolvedValue(undefined);
    render(<SettingsView />);
    fireEvent.click(await screen.findByRole("button", { name: /sign out/i }));
    await waitFor(() => expect(auth.signOut).toHaveBeenCalled());
  });

  it("About shows the package version and the server status", async () => {
    go("#about");
    render(<SettingsView />);
    expect(await screen.findByText("Reachable")).toBeInTheDocument();
    expect(screen.getByText("A web browser")).toBeInTheDocument();
    expect(screen.getByText("Version")).toBeInTheDocument();
  });

  it("does not invent settings that have no backend", () => {
    render(<SettingsView />);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/Hinglish|microphone|notification/i);
    expect(screen.queryByRole("link", { name: /^Voice$/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /^Notifications$/ })).toBeNull();
  });
});
