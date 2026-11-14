import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../../lib/connections-client";
import type { Connection } from "../../lib/connections-client";
import { ConnectionsSection } from "./ConnectionsSection";

const github: Connection = {
  provider: "github",
  label: "GitHub",
  state: "not_connected",
  source: null,
  message: "Not connected. Add your GitHub token to use it.",
  account: null,
  scopes: null,
  database_id: null,
  database_title: null,
  last_tested_at: null,
  takes_database_id: false,
};
const notion: Connection = {
  ...github,
  provider: "notion",
  label: "Notion",
  takes_database_id: true,
};
const SECRET = "ghp_" + "a".repeat(36);

describe("ConnectionsSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(client, "listConnections").mockResolvedValue({
      encryption_available: true,
      connections: [github, notion],
    });
  });

  it("saves a token, shows the test reason and clears the secret from the field", async () => {
    const save = vi.spyOn(client, "saveConnection").mockResolvedValue({
      connection: {
        ...github,
        state: "connected",
        source: "user",
        account: "octo",
        message: "Connected as octo.",
      },
      test: {
        ok: true,
        code: "ok",
        message: "Connected to GitHub as octo.",
        account: "octo",
        scopes: ["repo"],
        database_title: null,
      },
    });
    render(<ConnectionsSection />);
    // The redesigned list puts Notion before GitHub (mailbox, Notion, GitHub), so the card is
    // addressed by its test id instead of by position.
    const field = await screen.findByLabelText(/GitHub token/);
    fireEvent.change(field, { target: { value: SECRET } });
    fireEvent.click(
      within(screen.getByTestId("connection-github")).getByRole("button", {
        name: "Save",
      }),
    );
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("github", { token: SECRET }),
    );
    expect(
      await screen.findByText("Connected to GitHub as octo."),
    ).toBeTruthy();
    expect((field as HTMLInputElement).value).toBe("");
    expect(document.body.textContent).not.toContain(SECRET);
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeTruthy();
  });

  it("shows the server's reason when a test fails", async () => {
    vi.spyOn(client, "listConnections").mockResolvedValue({
      encryption_available: true,
      connections: [{ ...notion, state: "saved", source: "user" }],
    });
    vi.spyOn(client, "testConnection").mockResolvedValue({
      connection: { ...notion, state: "error", source: "user" },
      test: {
        ok: false,
        code: "database_not_shared",
        message:
          "Notion cannot see this database. Share it with your integration.",
        account: null,
        scopes: null,
        database_title: null,
      },
    });
    render(<ConnectionsSection />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Test connection" }),
    );
    expect(
      await screen.findByText(/Share it with your integration/),
    ).toBeTruthy();
  });

  it("disables saving and explains why when the server has no encryption key", async () => {
    vi.spyOn(client, "listConnections").mockResolvedValue({
      encryption_available: false,
      connections: [github],
    });
    render(<ConnectionsSection />);
    expect(await screen.findByText(/no encryption key/)).toBeTruthy();
    expect(
      (screen.getByLabelText(/GitHub token/) as HTMLInputElement).disabled,
    ).toBe(true);
  });
});
