import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  CircleDashed,
  Clock,
  Info,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import React from "react";

import { Icon } from "./Icon";
import { cx } from "./cx";

export type Tone =
  "neutral" | "accent" | "success" | "warning" | "danger" | "info";

const TONE: Record<Tone, string> = {
  neutral: "border-maya-border-strong bg-maya-raised text-maya-secondary",
  accent: "border-maya-accent bg-maya-accent-soft text-maya-link",
  success: "border-maya-success bg-maya-success-soft text-maya-success",
  warning: "border-maya-warning bg-maya-warning-soft text-maya-warning",
  danger: "border-maya-danger bg-maya-danger-soft text-maya-danger",
  info: "border-maya-info bg-maya-info-soft text-maya-info",
};

/** Every tone has its own icon, so colour is never the only signal. */
const TONE_ICON: Record<Tone, LucideIcon> = {
  neutral: Circle,
  accent: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  danger: XCircle,
  info: Info,
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  readonly tone?: Tone;
  /** Replace the tone's default icon. */
  readonly icon?: LucideIcon;
}

/** Small label with an icon and text. Text is required (children), so it never relies on colour. */
export function Badge({
  tone = "neutral",
  icon,
  className,
  children,
  ...rest
}: BadgeProps): React.JSX.Element {
  const Icon_ = icon ?? TONE_ICON[tone];
  return (
    <span
      {...rest}
      className={cx(
        "maya-badge inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-maya-full border px-2.5 py-0.5 text-maya-caption font-medium",
        TONE[tone],
        className,
      )}
    >
      <Icon icon={Icon_} size={14} className="shrink-0" />
      <span
        className="min-w-0 truncate"
        title={typeof children === "string" ? children : undefined}
      >
        {children}
      </span>
    </span>
  );
}

export type TaskStatus =
  "pending" | "running" | "awaiting_approval" | "completed" | "failed";

const STATUS: Record<
  TaskStatus,
  { readonly tone: Tone; readonly label: string; readonly icon: LucideIcon }
> = {
  pending: { tone: "neutral", label: "Pending", icon: Clock },
  running: { tone: "info", label: "In progress", icon: CircleDashed },
  awaiting_approval: {
    tone: "warning",
    label: "Awaiting approval",
    icon: AlertTriangle,
  },
  completed: { tone: "success", label: "Completed", icon: CheckCircle2 },
  failed: { tone: "danger", label: "Failed", icon: XCircle },
};

export interface StatusPillProps extends Omit<BadgeProps, "tone" | "icon"> {
  readonly status: TaskStatus;
}

/** Task state as icon plus words (grey pending, blue in progress, amber approval, green, red). */
export function StatusPill({
  status,
  children,
  ...rest
}: StatusPillProps): React.JSX.Element {
  const spec = STATUS[status];
  return (
    <Badge {...rest} tone={spec.tone} icon={spec.icon} data-status={status}>
      {children ?? spec.label}
    </Badge>
  );
}
