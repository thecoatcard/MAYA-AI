"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";

import { describeConnectionsError } from "../../lib/connections-client";
import {
  getNotionAutoApprove,
  NOTION_AUTO_APPROVE_SCOPES,
  updateNotionAutoApprove,
  type NotionAutoApproveScope,
  type NotionAutoApproveSettings,
} from "../../lib/settings-client";
import { Button, GlassModal, Notice, Skeleton } from "../ui";
import { SwitchRow } from "./primitives";

type Key = "master" | NotionAutoApproveScope;
type SaveState = { key: Key | null; state: "idle" | "saving" | "saved" };

const SCOPE_COPY: Record<
  NotionAutoApproveScope,
  { title: string; help: string }
> = {
  create_page: {
    title: "Create pages",
    help: "A new page under a page you name, or a new page in a database.",
  },
  add_row: {
    title: "Add database rows",
    help: "A new row in a Notion database.",
  },
  append_blocks: {
    title: "Add content to a page",
    help: "New blocks added at the end of an existing page. Nothing is removed.",
  },
  update_row: {
    title: "Edit database row properties",
    help: "Change column values of one existing row. The old values are replaced.",
  },
};

const TOOL_WORDS: Record<string, string> = {
  notion_create_page: "create page",
  notion_add_row: "add row",
  notion_append_blocks: "add content",
  notion_update_row: "edit row",
};

function formatWhen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/**
 * Settings > Connections > Notion: the opt-in (default off) that lets four additive Notion writes
 * be approved by your setting instead of a click. Turning it on needs the confirmation below.
 * Everything else, and every delete or removal, stays manual; the server enforces that list.
 */
export function NotionAutoApprove(): React.JSX.Element {
  const [settings, setSettings] = useState<NotionAutoApproveSettings | null>(
    null,
  );
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [save, setSave] = useState<SaveState>({ key: null, state: "idle" });
  const [confirming, setConfirming] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [dialogBusy, setDialogBusy] = useState(false);
  const mounted = useRef(true);
  const cancelRef = useRef<HTMLButtonElement>(null);

  const load = useCallback(() => {
    setFailed(false);
    getNotionAutoApprove()
      .then((value) => {
        if (mounted.current) setSettings(value);
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

  useEffect(() => {
    if (confirming) cancelRef.current?.focus();
  }, [confirming]);

  const change = async (
    key: Key,
    payload: Parameters<typeof updateNotionAutoApprove>[0],
  ): Promise<boolean> => {
    setSave({ key, state: "saving" });
    setError(null);
    try {
      const next = await updateNotionAutoApprove(payload);
      if (!mounted.current) return true;
      setSettings(next);
      setSave({ key, state: "saved" });
      return true;
    } catch (e) {
      if (mounted.current) {
        // The switch keeps the last value the server confirmed.
        setSave({ key: null, state: "idle" });
        setError(describeConnectionsError(e));
      }
      return false;
    }
  };

  const confirmEnable = async (): Promise<void> => {
    setDialogBusy(true);
    setDialogError(null);
    setSave({ key: "master", state: "saving" });
    try {
      const next = await updateNotionAutoApprove({
        enabled: true,
        confirm: true,
      });
      if (!mounted.current) return;
      setSettings(next);
      setSave({ key: "master", state: "saved" });
      setConfirming(false);
    } catch (e) {
      if (mounted.current) {
        setSave({ key: null, state: "idle" });
        setDialogError(describeConnectionsError(e));
      }
    } finally {
      if (mounted.current) setDialogBusy(false);
    }
  };

  const stateText = (key: Key, on: boolean): string => {
    if (save.key === key && save.state === "saving") return "Saving…";
    if (save.key === key && save.state === "saved") return "Saved";
    return on ? "On" : "Off";
  };

  if (failed) {
    return (
      <div className="grid gap-2" data-testid="notion-auto-approve">
        <Notice tone="danger">
          Auto-approve settings could not be loaded. Nothing was changed.
        </Notice>
        <div>
          <Button size="sm" onClick={load}>
            Retry
          </Button>
        </div>
      </div>
    );
  }
  if (settings === null) {
    return (
      <div aria-busy="true" aria-label="Loading auto-approve settings">
        <Skeleton lines={2} />
      </div>
    );
  }

  const busy = save.state === "saving";
  return (
    <section
      aria-label="Auto-approve Notion writes"
      data-testid="notion-auto-approve"
      className="grid min-w-0 gap-3 rounded-maya-md border border-maya-border"
    >
      <div className="min-w-0 divide-y divide-maya-border">
        <SwitchRow
          id="notion-auto-approve"
          title="Auto-approve Notion writes"
          help="Off by default. When on, the four kinds of write you pick below are approved by this setting instead of by you clicking Approve. Each one is still checked and read back from Notion afterwards."
          checked={settings.enabled}
          disabled={busy || dialogBusy}
          stateText={stateText("master", settings.enabled)}
          onToggle={() => {
            if (settings.enabled) {
              void change("master", { enabled: false });
            } else {
              setDialogError(null);
              setConfirming(true);
            }
          }}
        />
        {NOTION_AUTO_APPROVE_SCOPES.map((scope) => (
          <SwitchRow
            key={scope}
            id={`notion-auto-${scope}`}
            title={SCOPE_COPY[scope].title}
            help={SCOPE_COPY[scope].help}
            checked={settings.scopes[scope]}
            disabled={!settings.enabled || busy}
            stateText={
              settings.enabled
                ? stateText(scope, settings.scopes[scope])
                : "Off (turn on auto-approve first)"
            }
            onToggle={() =>
              void change(scope, {
                scopes: { [scope]: !settings.scopes[scope] },
              })
            }
          />
        ))}
      </div>

      <div className="grid min-w-0 gap-2 px-4 pb-3">
        <div className="grid gap-1" data-testid="notion-always-manual">
          <p className="m-0 text-maya-small font-medium text-maya-text">
            Always asks you, whatever this setting says
          </p>
          <ul className="m-0 grid list-disc gap-0.5 pl-5 text-maya-small text-maya-secondary">
            {settings.always_manual.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="m-0 text-maya-small text-maya-secondary">
            Pages over 300 blocks (long books) always ask you first.
          </p>
        </div>
        <p className="m-0 text-maya-small text-maya-secondary">
          Limit: {settings.hourly_cap} per hour and {settings.daily_cap} per
          day. After that, approval is manual again until the count drops. Used
          so far: {settings.used_last_hour} in the last hour,{" "}
          {settings.used_today} in the last day.
        </p>
        {settings.last_auto_approved !== null && (
          <p className="m-0 text-maya-small text-maya-secondary">
            Last auto-approved: {formatWhen(settings.last_auto_approved.at)}
            {settings.last_auto_approved.tool !== null &&
            TOOL_WORDS[settings.last_auto_approved.tool] !== undefined
              ? ` (${TOOL_WORDS[settings.last_auto_approved.tool]})`
              : ""}
          </p>
        )}
        {error !== null && (
          <p role="alert" className="m-0 text-maya-small text-maya-danger">
            {error} Nothing was changed.
          </p>
        )}
      </div>

      {confirming && (
        <GlassModal
          isOpen
          onClose={dialogBusy ? () => undefined : () => setConfirming(false)}
          title="Turn on auto-approval for Notion writes?"
          size="sm"
        >
          <div className="grid min-w-0 gap-3">
            <div className="grid gap-1" data-testid="auto-approve-will">
              <p className="m-0 text-maya-small font-medium text-maya-text">
                What will happen
              </p>
              <ul className="m-0 grid list-disc gap-0.5 pl-5 text-maya-small text-maya-secondary">
                <li>
                  The kinds of write you switch on next (create page, add row,
                  add content, edit row properties) will run without waiting for
                  your Approve click.
                </li>
                <li>
                  You still see the exact payload on the task card, and each
                  write is read back from Notion and checked afterwards.
                </li>
                <li>
                  At most {settings.hourly_cap} per hour and{" "}
                  {settings.daily_cap} per day; beyond that you approve by hand
                  again.
                </li>
              </ul>
            </div>
            <div className="grid gap-1" data-testid="auto-approve-manual">
              <p className="m-0 text-maya-small font-medium text-maya-text">
                What stays manual
              </p>
              <ul className="m-0 grid list-disc gap-0.5 pl-5 text-maya-small text-maya-secondary">
                {settings.always_manual.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
            <p className="m-0 text-maya-small text-maya-muted">
              Only you can change this, here. Chat, voice, e-mail or a Notion
              page cannot turn it on. You can turn it off at any time.
            </p>
            {dialogError !== null && (
              <p role="alert" className="m-0 text-maya-small text-maya-danger">
                {dialogError}
              </p>
            )}
            <div className="flex flex-wrap items-start justify-end gap-2">
              <Button
                ref={cancelRef}
                variant="ghost"
                size="sm"
                disabled={dialogBusy}
                onClick={() => setConfirming(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                loading={dialogBusy}
                onClick={() => void confirmEnable()}
              >
                Turn on auto-approval
              </Button>
            </div>
          </div>
        </GlassModal>
      )}
    </section>
  );
}
