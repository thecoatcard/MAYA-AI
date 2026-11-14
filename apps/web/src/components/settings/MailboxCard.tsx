"use client";

import React, { useEffect, useId, useRef, useState } from "react";

import {
  describeConnectionsError,
  disconnectConnection,
  saveConnection,
  testConnection,
  type Connection,
  type ConnectionResult,
  type SavePayload,
  type TestOutcome,
} from "../../lib/connections-client";
import { Button, ConfirmDeleteDialog, Notice } from "../ui";
import { ConnectionHeader } from "./ConnectionHeader";

export type MailPreset = "gmail" | "outlook" | "yahoo" | "icloud" | "custom";

interface PresetDef {
  readonly label: string;
  readonly imapHost: string;
  readonly imapPort: number;
  readonly smtpHost: string;
  readonly smtpPort: number;
}

/** Same table as backend/app/connections/mailbox.py; the server re-validates every field. */
export const MAIL_PRESETS: Record<Exclude<MailPreset, "custom">, PresetDef> = {
  gmail: {
    label: "Gmail",
    imapHost: "imap.gmail.com",
    imapPort: 993,
    smtpHost: "smtp.gmail.com",
    smtpPort: 465,
  },
  outlook: {
    label: "Outlook / Office 365",
    imapHost: "outlook.office365.com",
    imapPort: 993,
    smtpHost: "smtp.office365.com",
    smtpPort: 587,
  },
  yahoo: {
    label: "Yahoo",
    imapHost: "imap.mail.yahoo.com",
    imapPort: 993,
    smtpHost: "smtp.mail.yahoo.com",
    smtpPort: 465,
  },
  icloud: {
    label: "iCloud",
    imapHost: "imap.mail.me.com",
    imapPort: 993,
    smtpHost: "smtp.mail.me.com",
    smtpPort: 587,
  },
};

const PRESET_HELP: Record<MailPreset, string> = {
  gmail:
    "Turn on 2-Step Verification on your Google account, create an App Password at myaccount.google.com/apppasswords, and make sure IMAP is enabled in Gmail settings (Forwarding and POP/IMAP). Paste the 16-character app password here, not your normal password.",
  outlook:
    "Use an app password if your account has two-step verification, and make sure IMAP is allowed for the mailbox.",
  yahoo:
    "Create an app password in Yahoo Account Security and paste it here, not your normal password.",
  icloud:
    "Create an app-specific password at appleid.apple.com and paste it here, not your Apple ID password.",
  custom:
    "Enter the incoming (IMAP, port 993) and outgoing (SMTP, port 465 or 587) servers from your mail provider. Public host names only.",
};

const ADDRESS_PATTERN =
  /^[^@\s<>,;"'()[\]\\]+@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$/;

const INPUT =
  "min-h-9 w-full min-w-0 rounded-maya-md border border-maya-control-border bg-maya-pane px-3 py-2 text-maya-small text-maya-text placeholder:text-maya-muted hover:border-maya-border-strong focus:border-maya-focus disabled:cursor-not-allowed disabled:bg-maya-disabled-bg disabled:text-maya-disabled-text";

function detectPreset(item: Connection): MailPreset {
  const box = item.mailbox;
  if (!box) return "gmail";
  for (const [key, def] of Object.entries(MAIL_PRESETS)) {
    if (
      def.imapHost === box.imap_host &&
      def.smtpHost === box.smtp_host &&
      def.smtpPort === box.smtp_port
    ) {
      return key as MailPreset;
    }
  }
  return "custom";
}

function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  readonly id: string;
  readonly label: string;
  readonly hint?: string;
  readonly error?: string | null;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="grid min-w-0 gap-1">
      <label htmlFor={id} className="text-maya-small font-medium">
        {label}
      </label>
      {children}
      {hint !== undefined && error == null && (
        <p id={`${id}-hint`} className="m-0 text-maya-caption text-maya-muted">
          {hint}
        </p>
      )}
      {error != null && (
        <p
          id={`${id}-error`}
          role="alert"
          className="m-0 text-maya-caption text-maya-danger"
        >
          {error}
        </p>
      )}
    </div>
  );
}

export function MailboxCard({
  item,
  canSave,
  onChange,
}: {
  readonly item: Connection;
  readonly canSave: boolean;
  readonly onChange: (next: Connection) => void;
}): React.JSX.Element {
  const saved = item.source === "user";
  const box = item.mailbox ?? null;
  const [preset, setPreset] = useState<MailPreset>(detectPreset(item));
  const initial = preset === "custom" ? null : MAIL_PRESETS[preset];
  const [address, setAddress] = useState("");
  const [password, setPassword] = useState("");
  const [imapHost, setImapHost] = useState(
    box?.imap_host ?? initial?.imapHost ?? "",
  );
  const [imapPort, setImapPort] = useState(
    String(box?.imap_port ?? initial?.imapPort ?? 993),
  );
  const [smtpHost, setSmtpHost] = useState(
    box?.smtp_host ?? initial?.smtpHost ?? "",
  );
  const [smtpPort, setSmtpPort] = useState(
    String(box?.smtp_port ?? initial?.smtpPort ?? 465),
  );
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState<"save" | "test" | "disconnect" | null>(null);
  const [outcome, setOutcome] = useState<{
    ok: boolean;
    test: TestOutcome | null;
    text: string;
  } | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const mounted = useRef(true);
  const ids = {
    preset: useId(),
    address: useId(),
    password: useId(),
    imapHost: useId(),
    imapPort: useId(),
    smtpHost: useId(),
    smtpPort: useId(),
  };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const choosePreset = (next: MailPreset): void => {
    setPreset(next);
    if (next !== "custom") {
      const def = MAIL_PRESETS[next];
      setImapHost(def.imapHost);
      setImapPort(String(def.imapPort));
      setSmtpHost(def.smtpHost);
      setSmtpPort(String(def.smtpPort));
    }
  };

  const errors = {
    address:
      address.trim() === ""
        ? saved
          ? null
          : "Enter the full email address."
        : ADDRESS_PATTERN.test(address.trim())
          ? null
          : "Enter a full address, like name@gmail.com.",
    password:
      password.trim() === "" && !saved ? "Paste your app password." : null,
    imapHost: imapHost.trim() === "" ? "Enter the incoming server." : null,
    smtpHost: smtpHost.trim() === "" ? "Enter the outgoing server." : null,
    imapPort:
      Number(imapPort) === 993 ? null : "The incoming port must be 993.",
    smtpPort: [465, 587].includes(Number(smtpPort))
      ? null
      : "The outgoing port must be 465 or 587.",
  };
  const invalid = Object.values(errors).some((e) => e !== null);
  const shown = (key: keyof typeof errors): string | null =>
    touched ? errors[key] : null;

  const apply = (result: ConnectionResult): void => {
    onChange(result.connection);
    if (result.test !== null) {
      setOutcome({
        ok: result.test.ok,
        test: result.test,
        text: result.test.message,
      });
    }
  };

  const run = async (
    kind: "save" | "test",
    action: () => Promise<void>,
  ): Promise<void> => {
    setBusy(kind);
    setOutcome(null);
    try {
      await action();
    } catch (e) {
      if (mounted.current) {
        setOutcome({
          ok: false,
          test: null,
          text: describeConnectionsError(e),
        });
      }
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const save = (): Promise<void> => {
    setTouched(true);
    if (invalid) return Promise.resolve();
    return run("save", async () => {
      const payload: SavePayload = {
        imap_host: imapHost.trim(),
        imap_port: Number(imapPort),
        smtp_host: smtpHost.trim(),
        smtp_port: Number(smtpPort),
      };
      if (address.trim() !== "") payload.address = address.trim();
      if (password.trim() !== "") payload.token = password;
      const result = await saveConnection(item.provider, payload);
      // The password never stays in the page once the server has it.
      setPassword("");
      setAddress("");
      setTouched(false);
      if (mounted.current) apply(result);
    });
  };

  const disconnect = async (): Promise<void> => {
    setBusy("disconnect");
    setDialogError(null);
    try {
      const next = await disconnectConnection(item.provider);
      if (!mounted.current) return;
      setConfirming(false);
      setOutcome(null);
      onChange(next);
    } catch (e) {
      if (mounted.current) setDialogError(describeConnectionsError(e));
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const custom = preset === "custom";

  return (
    <div
      className="grid min-w-0 gap-4 px-4 py-4"
      data-testid={`connection-${item.provider}`}
      data-state={item.state}
    >
      <ConnectionHeader
        item={item}
        title="Mailbox"
        subtitle="Lets MAYA read your mail and send mail you approve, from your own account."
      />
      {saved && box !== null && (
        <p className="m-0 text-maya-small text-maya-secondary">
          Saved mailbox: <span className="font-medium">{box.address}</span>. The
          app password is saved and is never shown again.
        </p>
      )}

      <form
        className="grid min-w-0 gap-4"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Field id={ids.preset} label="Mail provider">
          <select
            id={ids.preset}
            value={preset}
            disabled={!canSave}
            onChange={(event) => choosePreset(event.target.value as MailPreset)}
            className={INPUT}
          >
            {(
              Object.keys(MAIL_PRESETS) as Array<keyof typeof MAIL_PRESETS>
            ).map((key) => (
              <option key={key} value={key}>
                {MAIL_PRESETS[key].label}
              </option>
            ))}
            <option value="custom">Custom</option>
          </select>
        </Field>
        <p className="m-0 text-maya-small text-maya-secondary">
          {PRESET_HELP[preset]}
        </p>

        <div className="grid min-w-0 gap-4 sm:grid-cols-2">
          <Field
            id={ids.address}
            label="Email address"
            error={shown("address")}
            hint={saved ? "Leave empty to keep the saved address." : undefined}
          >
            <input
              id={ids.address}
              type="email"
              value={address}
              onChange={(event) => setAddress(event.target.value)}
              placeholder={saved && box ? box.address : "name@example.com"}
              autoComplete="off"
              spellCheck={false}
              disabled={!canSave}
              aria-invalid={shown("address") !== null}
              className={INPUT}
            />
          </Field>
          <Field
            id={ids.password}
            label="App password"
            error={shown("password")}
            hint={
              saved
                ? "Saved. Type a new one to replace it."
                : "Write-only: MAYA never shows it again."
            }
          >
            <input
              id={ids.password}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={saved ? "Saved" : "Paste your app password"}
              autoComplete="new-password"
              spellCheck={false}
              disabled={!canSave}
              aria-invalid={shown("password") !== null}
              className={INPUT}
            />
          </Field>
          <Field
            id={ids.imapHost}
            label="Incoming server (IMAP)"
            error={shown("imapHost")}
          >
            <input
              id={ids.imapHost}
              type="text"
              value={imapHost}
              onChange={(event) => setImapHost(event.target.value)}
              readOnly={!custom}
              disabled={!canSave}
              autoComplete="off"
              spellCheck={false}
              className={INPUT}
            />
          </Field>
          <Field
            id={ids.imapPort}
            label="Incoming port"
            error={shown("imapPort")}
          >
            <input
              id={ids.imapPort}
              type="text"
              inputMode="numeric"
              value={imapPort}
              onChange={(event) => setImapPort(event.target.value)}
              readOnly={!custom}
              disabled={!canSave}
              className={INPUT}
            />
          </Field>
          <Field
            id={ids.smtpHost}
            label="Outgoing server (SMTP)"
            error={shown("smtpHost")}
          >
            <input
              id={ids.smtpHost}
              type="text"
              value={smtpHost}
              onChange={(event) => setSmtpHost(event.target.value)}
              readOnly={!custom}
              disabled={!canSave}
              autoComplete="off"
              spellCheck={false}
              className={INPUT}
            />
          </Field>
          <Field
            id={ids.smtpPort}
            label="Outgoing port"
            error={shown("smtpPort")}
          >
            <input
              id={ids.smtpPort}
              type="text"
              inputMode="numeric"
              value={smtpPort}
              onChange={(event) => setSmtpPort(event.target.value)}
              readOnly={!custom}
              disabled={!canSave}
              className={INPUT}
            />
          </Field>
        </div>

        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Button
            type="submit"
            variant="primary"
            loading={busy === "save"}
            disabled={!canSave || busy !== null}
          >
            Save
          </Button>
          <Button
            loading={busy === "test"}
            disabled={busy !== null || item.state === "not_connected"}
            onClick={() =>
              void run("test", async () => {
                apply(await testConnection(item.provider));
              })
            }
          >
            Test connection
          </Button>
          {saved && (
            <Button
              variant="danger"
              disabled={busy !== null}
              onClick={() => setConfirming(true)}
            >
              Disconnect
            </Button>
          )}
        </div>
      </form>

      {outcome !== null && (
        <div
          data-testid={`connection-${item.provider}-result`}
          className="grid min-w-0 gap-2"
        >
          {outcome.test?.steps != null && outcome.test.steps.length > 0 ? (
            <ul className="m-0 grid list-none gap-2 p-0">
              {outcome.test.steps.map((step) => (
                <li key={step.label}>
                  <Notice
                    tone={step.ok ? "success" : "danger"}
                    title={`${step.label}: ${step.ok ? "works" : "failed"}`}
                  >
                    {step.message}
                  </Notice>
                </li>
              ))}
            </ul>
          ) : (
            <Notice tone={outcome.ok ? "success" : "danger"}>
              {outcome.text}
            </Notice>
          )}
        </div>
      )}

      {confirming && (
        <ConfirmDeleteDialog
          title="Disconnect your mailbox?"
          removes={[
            "The saved app password and mailbox settings for your account",
          ]}
          keeps={[
            "Your mail itself: nothing is deleted from your mailbox",
            "Mail already sent",
          ]}
          confirmLabel="Disconnect mailbox"
          busy={busy === "disconnect"}
          error={dialogError}
          onConfirm={() => void disconnect()}
          onClose={() => setConfirming(false)}
        />
      )}
    </div>
  );
}
