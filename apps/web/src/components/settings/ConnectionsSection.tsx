"use client";

import { Copy, KeyRound, PlugZap, Unplug } from "lucide-react";
import React, { useCallback, useEffect, useId, useRef, useState } from "react";

import {
  describeConnectionsError,
  disconnectConnection,
  listConnections,
  saveConnection,
  testConnection,
  type Connection,
  type ConnectionResult,
  type SavePayload,
} from "../../lib/connections-client";
import {
  Button,
  type ContextMenuEntry,
  ConfirmDeleteDialog,
  ErrorState,
  MenuSurface,
  Notice,
  Skeleton,
} from "../ui";
import { copyText } from "../../lib/menu-actions";
import { CONNECTION_BADGE, ConnectionHeader } from "./ConnectionHeader";
import { MailboxCard } from "./MailboxCard";
import { NotionAutoApprove } from "./NotionAutoApprove";
import { SettingsCard } from "./primitives";

const HELP: Record<string, string> = {
  github:
    "Create a token at github.com/settings/tokens (repo read access is enough).",
  notion:
    "Create an integration at notion.so/my-integrations, copy its secret, then share your tasks database with it (the ... menu > Connections).",
};

const SUBTITLE: Record<string, string> = {
  github: "Lets MAYA read your repositories.",
  notion: "Lets MAYA read and, with your approval, update your tasks database.",
};

/** Mailbox first, then the token-based connections in the order the server lists them. */
const ORDER = ["mail", "notion", "github"];

const INPUT =
  "min-h-9 min-w-0 rounded-maya-md border border-maya-control-border bg-maya-pane px-3 py-2 font-mono text-maya-mono text-maya-text placeholder:text-maya-muted hover:border-maya-border-strong focus:border-maya-focus disabled:cursor-not-allowed disabled:bg-maya-disabled-bg disabled:text-maya-disabled-text";

function TokenCard({
  item,
  canSave,
  onChange,
}: {
  readonly item: Connection;
  readonly canSave: boolean;
  readonly onChange: (next: Connection) => void;
}): React.JSX.Element {
  const [token, setToken] = useState("");
  const [database, setDatabase] = useState("");
  const [busy, setBusy] = useState<"save" | "test" | "disconnect" | null>(null);
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(
    null,
  );
  const [confirming, setConfirming] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const mounted = useRef(true);
  const tokenId = useId();
  const dbId = useId();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const apply = (result: ConnectionResult): void => {
    onChange(result.connection);
    if (result.test !== null) {
      setOutcome({ ok: result.test.ok, text: result.test.message });
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
        setOutcome({ ok: false, text: describeConnectionsError(e) });
      }
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const save = (): Promise<void> =>
    run("save", async () => {
      const payload: SavePayload = {};
      if (token.trim() !== "") payload.token = token;
      if (item.takes_database_id && database.trim() !== "") {
        payload.database_id = database;
      }
      const result = await saveConnection(item.provider, payload);
      // The secret never stays in client state once the server has it.
      setToken("");
      setDatabase("");
      if (mounted.current) apply(result);
    });

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

  const stored = item.source === "user";
  const nothingTyped =
    token.trim() === "" && (!item.takes_database_id || database.trim() === "");

  const connectionItems = (): ContextMenuEntry[] => {
    const badge = CONNECTION_BADGE[item.state];
    return [
      {
        id: "test",
        label: "Test",
        icon: <PlugZap size={14} />,
        disabled: busy !== null || item.state === "not_connected",
        disabledReason: "Not connected yet.",
        onSelect: () =>
          void run("test", async () => {
            apply(await testConnection(item.provider));
          }),
      },
      {
        id: "reconnect",
        label: "Reconnect (enter a new token)",
        icon: <KeyRound size={14} />,
        disabled: !canSave,
        disabledReason: "Saving is not allowed for your account.",
        onSelect: () => document.getElementById(tokenId)?.focus(),
      },
      {
        id: "copy-status",
        label: "Copy status",
        icon: <Copy size={14} />,
        onSelect: () =>
          void copyText(`${item.label}: ${badge.text}. ${item.message}`),
      },
      { kind: "separator" },
      {
        id: "disconnect",
        label: "Disconnect",
        icon: <Unplug size={14} />,
        danger: true,
        disabled: !stored || busy !== null,
        disabledReason: "There is no saved token to remove.",
        onSelect: () => setConfirming(true),
      },
    ];
  };

  return (
    <MenuSurface
      label={item.label}
      getItems={connectionItems}
      className="grid min-w-0 gap-3 px-4 py-4 pe-12"
      data-testid={`connection-${item.provider}`}
      data-state={item.state}
    >
      <ConnectionHeader
        item={item}
        title={item.label}
        subtitle={SUBTITLE[item.provider] ?? ""}
      />
      {item.state === "connected" && item.scopes !== null && (
        <p className="m-0 text-maya-small text-maya-secondary">
          Scopes: {item.scopes.length > 0 ? item.scopes.join(", ") : "none"}
        </p>
      )}
      {item.database_id !== null && (
        <p className="m-0 break-all text-maya-small text-maya-secondary">
          Database: {item.database_title ?? item.database_id}
        </p>
      )}
      <p className="m-0 text-maya-small text-maya-muted">
        {HELP[item.provider]}
      </p>

      <form
        className="grid min-w-0 gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <label htmlFor={tokenId} className="text-maya-small font-medium">
          {item.label} token
        </label>
        <input
          id={tokenId}
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder={
            stored ? "Saved. Type to replace it" : "Paste your token"
          }
          autoComplete="off"
          spellCheck={false}
          disabled={!canSave}
          className={INPUT}
        />
        {item.takes_database_id && (
          <>
            <label htmlFor={dbId} className="text-maya-small font-medium">
              Notion database URL or id
            </label>
            <input
              id={dbId}
              type="text"
              value={database}
              onChange={(event) => setDatabase(event.target.value)}
              placeholder={
                item.database_id !== null
                  ? "Saved. Paste a link to change it"
                  : "Paste the database link or id"
              }
              autoComplete="off"
              spellCheck={false}
              disabled={!canSave}
              className={INPUT}
            />
          </>
        )}
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Button
            type="submit"
            variant="primary"
            loading={busy === "save"}
            disabled={!canSave || busy !== null || nothingTyped}
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
          {stored && (
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
        <div data-testid={`connection-${item.provider}-result`}>
          <Notice tone={outcome.ok ? "success" : "danger"}>
            {outcome.text}
          </Notice>
        </div>
      )}
      {item.provider === "notion" && <NotionAutoApprove />}
      {confirming && (
        <ConfirmDeleteDialog
          title={`Disconnect ${item.label}?`}
          removes={[`The saved ${item.label} token for your account`]}
          keeps={[`Your data in ${item.label} is not touched`]}
          confirmLabel={`Disconnect ${item.label}`}
          busy={busy === "disconnect"}
          error={dialogError}
          onConfirm={() => void disconnect()}
          onClose={() => setConfirming(false)}
        />
      )}
    </MenuSurface>
  );
}

export function ConnectionsSection(): React.JSX.Element {
  const [items, setItems] = useState<readonly Connection[] | null>(null);
  const [canSave, setCanSave] = useState(true);
  const [failed, setFailed] = useState(false);
  const [serverBlocked, setServerBlocked] = useState(false);
  const mounted = useRef(true);

  const load = useCallback(() => {
    setFailed(false);
    listConnections()
      .then((list) => {
        if (!mounted.current) return;
        setItems(list.connections);
        setCanSave(list.encryption_available);
        setServerBlocked(list.server_defaults_blocked === true);
      })
      .catch(() => {
        if (mounted.current) setFailed(true);
      });
  }, []);

  useEffect(() => {
    mounted.current = true;
    load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  const replace = (next: Connection): void =>
    setItems((current) =>
      (current ?? []).map((c) => (c.provider === next.provider ? next : c)),
    );

  if (failed) {
    return (
      <ErrorState
        title="Connections could not be loaded"
        message="Nothing was changed."
        onRetry={load}
      />
    );
  }
  if (items === null) {
    return (
      <div aria-busy="true" aria-label="Loading connections" className="py-3">
        <Skeleton lines={4} />
      </div>
    );
  }
  const sorted = [...items].sort(
    (a, b) =>
      (ORDER.indexOf(a.provider) + 1 || 99) -
      (ORDER.indexOf(b.provider) + 1 || 99),
  );
  return (
    <div className="grid min-w-0 gap-4">
      {!canSave && (
        <Notice tone="warning">
          Saving credentials is switched off on this server (no encryption key
          is set). Ask whoever runs the server to set
          MAYA_CONNECTIONS_ENCRYPTION_KEY.
        </Notice>
      )}
      {serverBlocked && (
        <Notice tone="info">
          Server-level connections are off. Set MAYA_ENV_CONNECTIONS_USERS to
          your email to use them, or connect your own accounts here.
        </Notice>
      )}
      {sorted.length === 0 ? (
        <p className="m-0 text-maya-small text-maya-secondary">
          This server offers no connections.
        </p>
      ) : (
        sorted.map((item) => (
          <SettingsCard key={item.provider}>
            {item.kind === "mailbox" ? (
              <MailboxCard item={item} canSave={canSave} onChange={replace} />
            ) : (
              <TokenCard item={item} canSave={canSave} onChange={replace} />
            )}
          </SettingsCard>
        ))
      )}
      <p className="m-0 text-maya-small text-maya-muted">
        Credentials are stored encrypted, never shown again, and only your own
        tasks can use them.
      </p>
    </div>
  );
}
