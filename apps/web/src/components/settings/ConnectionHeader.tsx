import React from "react";

import type { Connection, ConnectionState } from "../../lib/connections-client";
import { Badge, type Tone } from "../ui";

export const CONNECTION_BADGE: Record<
  ConnectionState,
  { tone: Tone; text: string }
> = {
  connected: { tone: "success", text: "Connected" },
  saved: { tone: "info", text: "Saved, not tested" },
  error: { tone: "danger", text: "Needs attention" },
  server_default: { tone: "neutral", text: "Server default" },
  unreadable: { tone: "warning", text: "Needs attention" },
  not_connected: { tone: "neutral", text: "Not connected" },
};

export function formatChecked(iso: string | null): string | null {
  if (iso === null) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** Name, status badge (words, never colour alone), the server's plain message and last check. */
export function ConnectionHeader({
  item,
  title,
  subtitle,
}: {
  readonly item: Connection;
  readonly title: string;
  readonly subtitle: string;
}): React.JSX.Element {
  const badge = CONNECTION_BADGE[item.state];
  const checked = formatChecked(item.last_tested_at);
  return (
    <div className="grid min-w-0 gap-1">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <h4 className="m-0 text-maya-body font-semibold">{title}</h4>
        <Badge tone={badge.tone}>{badge.text}</Badge>
      </div>
      <p className="m-0 text-maya-small text-maya-secondary">{subtitle}</p>
      <p className="m-0 text-maya-small text-maya-secondary">{item.message}</p>
      {checked !== null && (
        <p className="m-0 text-maya-caption text-maya-muted">
          Last checked {checked}
        </p>
      )}
    </div>
  );
}
