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

const SECRET = "abcd efgh ijkl mnop";

const empty: Connection = {
  provider: "mail",
  label: "Mailbox",
  state: "not_connected",
  source: null,
  message: "No mailbox connected. Add your address and app password.",
  account: null,
  scopes: null,
  database_id: null,
  database_title: null,
  last_tested_at: null,
  takes_database_id: false,
  kind: "mailbox",
  mailbox: null,
};

const saved: Connection = {
  ...empty,
  state: "connected",
  source: "user",
  message: "Connected as m***@gmail.com.",
  account: "m***@gmail.com",
  last_tested_at: "2026-11-12T10:00:00+00:00",
  mailbox: {
    address: "m***@gmail.com",
    imap_host: "imap.gmail.com",
    imap_port: 993,
    smtp_host: "smtp.gmail.com",
    smtp_port: 465,
    password_saved: true,
  },
};

function card(): HTMLElement {
  return screen.getByTestId("connection-mail");
}

describe("Mailbox card", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(client, "listConnections").mockResolvedValue({
      encryption_available: true,
      connections: [empty],
    });
  });

  it("is the first card and offers the Gmail steps by default", async () => {
    vi.spyOn(client, "listConnections").mockResolvedValue({
      encryption_available: true,
      connections: [
        { ...empty, provider: "github", label: "GitHub", kind: "token" },
        empty,
      ],
    });
    render(<ConnectionsSection />);
    await screen.findByTestId("connection-mail");
    const cards = screen.getAllByTestId(/^connection-(mail|github)$/);
    expect(cards[0]).toHaveAttribute("data-testid", "connection-mail");
    expect(card().textContent).toMatch(/myaccount\.google\.com\/apppasswords/);
    expect(card().textContent).toMatch(/2-Step Verification/);
    expect(card().textContent).toMatch(/IMAP/);
  });

  it("fills the servers from the preset and unlocks them only for Custom", async () => {
    render(<ConnectionsSection />);
    await screen.findByTestId("connection-mail");
    const imap = screen.getByLabelText(
      "Incoming server (IMAP)",
    ) as HTMLInputElement;
    expect(imap.value).toBe("imap.gmail.com");
    expect(imap.readOnly).toBe(true);
    fireEvent.change(screen.getByLabelText("Mail provider"), {
      target: { value: "outlook" },
    });
    expect(imap.value).toBe("outlook.office365.com");
    expect(
      (screen.getByLabelText("Outgoing server (SMTP)") as HTMLInputElement)
        .value,
    ).toBe("smtp.office365.com");
    expect(
      (screen.getByLabelText("Outgoing port") as HTMLInputElement).value,
    ).toBe("587");
    fireEvent.change(screen.getByLabelText("Mail provider"), {
      target: { value: "custom" },
    });
    expect(imap.readOnly).toBe(false);
  });

  it("validates inline and does not call the server for bad input", async () => {
    const save = vi.spyOn(client, "saveConnection");
    render(<ConnectionsSection />);
    await screen.findByTestId("connection-mail");
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "nope" },
    });
    fireEvent.click(within(card()).getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Enter a full address/)).toBeTruthy();
    expect(screen.getByText("Paste your app password.")).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
  });

  it("saves, clears the password from the page and never renders it back", async () => {
    const save = vi.spyOn(client, "saveConnection").mockResolvedValue({
      connection: saved,
      test: {
        ok: true,
        code: "ok",
        message: "Connected: MAYA can read and send as m***@gmail.com.",
        account: "m***@gmail.com",
        scopes: null,
        database_title: null,
        steps: [
          { label: "Incoming (IMAP)", ok: true, message: "Signed in." },
          { label: "Outgoing (SMTP)", ok: true, message: "Signed in." },
        ],
      },
    });
    render(<ConnectionsSection />);
    await screen.findByTestId("connection-mail");
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "maya.user@gmail.com" },
    });
    const password = screen.getByLabelText("App password") as HTMLInputElement;
    fireEvent.change(password, { target: { value: SECRET } });
    expect(password.type).toBe("password");
    fireEvent.click(within(card()).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("mail", {
        address: "maya.user@gmail.com",
        token: SECRET,
        imap_host: "imap.gmail.com",
        imap_port: 993,
        smtp_host: "smtp.gmail.com",
        smtp_port: 465,
      }),
    );
    expect(await screen.findByText("Incoming (IMAP): works")).toBeTruthy();
    expect(password.value).toBe("");
    expect(document.body.textContent).not.toContain(SECRET);
    expect(document.body.textContent).not.toContain("abcdefghijklmnop");
    expect(screen.getByText("Connected")).toBeTruthy();
  });

  it("shows each half of a failed test in plain words", async () => {
    vi.spyOn(client, "listConnections").mockResolvedValue({
      encryption_available: true,
      connections: [saved],
    });
    vi.spyOn(client, "testConnection").mockResolvedValue({
      connection: { ...saved, state: "error", message: "failed" },
      test: {
        ok: false,
        code: "smtp_failed",
        message: "Gmail rejected the login.",
        account: null,
        scopes: null,
        database_title: null,
        steps: [
          { label: "Incoming (IMAP)", ok: true, message: "Signed in." },
          {
            label: "Outgoing (SMTP)",
            ok: false,
            message: "Gmail rejected the login; create an App Password.",
          },
        ],
      },
    });
    render(<ConnectionsSection />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Test connection" }),
    );
    expect(await screen.findByText("Outgoing (SMTP): failed")).toBeTruthy();
    expect(
      screen.getByText("Gmail rejected the login; create an App Password."),
    ).toBeTruthy();
    expect(screen.getByText("Incoming (IMAP): works")).toBeTruthy();
  });

  it("shows a saved mailbox with a masked address and a saved password, never the password", async () => {
    vi.spyOn(client, "listConnections").mockResolvedValue({
      encryption_available: true,
      connections: [saved],
    });
    render(<ConnectionsSection />);
    await screen.findByTestId("connection-mail");
    expect(card().textContent).toContain("m***@gmail.com");
    expect(screen.getByText("Connected")).toBeTruthy();
    const password = screen.getByLabelText("App password") as HTMLInputElement;
    expect(password.value).toBe("");
    expect(password.placeholder).toBe("Saved");
  });

  it("asks before disconnecting and only then deletes the secret", async () => {
    vi.spyOn(client, "listConnections").mockResolvedValue({
      encryption_available: true,
      connections: [saved],
    });
    const disconnect = vi
      .spyOn(client, "disconnectConnection")
      .mockResolvedValue(empty);
    render(<ConnectionsSection />);
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    expect(disconnect).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toMatch(/app password/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(disconnect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Disconnect mailbox",
      }),
    );
    await waitFor(() => expect(disconnect).toHaveBeenCalledWith("mail"));
    expect(await screen.findByText("Not connected")).toBeTruthy();
  });

  it("disables the form and says why when the server has no encryption key", async () => {
    vi.spyOn(client, "listConnections").mockResolvedValue({
      encryption_available: false,
      connections: [empty],
    });
    render(<ConnectionsSection />);
    expect(await screen.findByText(/no encryption key/)).toBeTruthy();
    expect(
      (screen.getByLabelText("App password") as HTMLInputElement).disabled,
    ).toBe(true);
  });
});
