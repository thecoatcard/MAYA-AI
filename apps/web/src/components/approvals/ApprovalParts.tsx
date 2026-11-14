import { Clock, Lock, ShieldAlert } from "lucide-react";
import React from "react";

import type {
  ApprovalLifecycleStatus,
  ApprovalRequest,
} from "../../lib/approvals-client";
import { Badge, Truncate, type Tone } from "../ui";

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString([], { dateStyle: "medium", timeStyle: "medium" });
}

/** Evaluated when rendered: no ticking clock next to the decision controls. */
export function isExpired(approval: ApprovalRequest): boolean {
  const at = new Date(approval.expires_at).getTime();
  return Number.isFinite(at) && at < Date.now();
}

const STATUS_TONE: Readonly<Record<ApprovalLifecycleStatus, Tone>> = {
  pending: "warning",
  approved: "success",
  rejected: "danger",
  denied: "danger",
  expired: "neutral",
  revoked: "neutral",
  claimed: "info",
  executing: "info",
  completed: "success",
  failed: "danger",
  consumed: "neutral",
};

export function ApprovalStatusBadge({
  status,
}: {
  readonly status: ApprovalLifecycleStatus;
}): React.JSX.Element {
  const label = status.charAt(0).toUpperCase() + status.slice(1);
  return (
    <Badge tone={STATUS_TONE[status] ?? "neutral"} className="shrink-0">
      {label}
    </Badge>
  );
}

export function TierBadge({
  tier,
}: {
  readonly tier: ApprovalRequest["tier"];
}): React.JSX.Element {
  return tier === "RESTRICTED" ? (
    <Badge tone="danger" icon={Lock} className="shrink-0">
      Restricted: needs a code
    </Badge>
  ) : (
    <Badge tone="warning" icon={ShieldAlert} className="shrink-0">
      Approval required
    </Badge>
  );
}

export interface ApprovalListItemProps {
  readonly approval: ApprovalRequest;
  readonly selected: boolean;
  readonly onSelect: (id: string) => void;
}

/** One row of an approvals list. Never shrinks (a long list scrolls instead of collapsing rows). */
export function ApprovalListItem({
  approval,
  selected,
  onSelect,
}: ApprovalListItemProps): React.JSX.Element {
  const restricted = approval.tier === "RESTRICTED";
  return (
    <li className="min-w-0 shrink-0">
      <button
        type="button"
        aria-pressed={selected}
        onClick={() => onSelect(approval.id)}
        className={`flex w-full min-w-0 flex-col gap-1.5 border p-3 text-left motion-interactive ${
          selected
            ? "border-maya-accent bg-maya-accent-soft"
            : "border-maya-border bg-maya-raised hover:bg-maya-hover"
        } ${restricted ? "border-l-4 border-l-maya-danger" : ""}`}
      >
        <span className="flex min-w-0 flex-wrap items-start justify-between gap-x-2 gap-y-1">
          <Truncate
            lines={2}
            title={approval.tool_name}
            className="min-w-0 flex-1 basis-32 font-mono text-maya-small font-medium text-maya-text"
          >
            {approval.tool_name}
          </Truncate>
          {approval.status === "pending" ? (
            <TierBadge tier={approval.tier} />
          ) : (
            <ApprovalStatusBadge status={approval.status} />
          )}
        </span>
        <Truncate
          lines={2}
          dir="auto"
          title={approval.action_description}
          className="text-maya-caption text-maya-secondary"
        >
          {approval.action_description}
        </Truncate>
        <span className="flex min-w-0 items-center gap-1 text-maya-caption text-maya-muted">
          <Clock aria-hidden="true" size={14} className="shrink-0" />
          <span className="min-w-0 [overflow-wrap:anywhere]">
            {isExpired(approval) ? "Expired " : "Expires "}
            {formatDateTime(approval.expires_at)}
          </span>
        </span>
      </button>
    </li>
  );
}
