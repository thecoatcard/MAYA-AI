import {
  Ban,
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Clock,
  ShieldQuestion,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import React from "react";

import { Badge, type BadgeProps, type Tone } from "../ui";
import {
  PARTIAL_LABEL,
  PHASE_LABEL,
  phaseOfStatus,
  type TaskPhase,
} from "./taskStatus";

const SPEC: Readonly<
  Record<TaskPhase, { readonly tone: Tone; readonly icon: LucideIcon }>
> = {
  loading: { tone: "neutral", icon: CircleDashed },
  planning: { tone: "neutral", icon: Clock },
  running: { tone: "info", icon: CircleDashed },
  awaiting_approval: { tone: "warning", icon: ShieldQuestion },
  completed: { tone: "success", icon: CheckCircle2 },
  failed: { tone: "danger", icon: XCircle },
  cancelled: { tone: "neutral", icon: Ban },
};

export interface TaskStatusBadgeProps extends Omit<
  BadgeProps,
  "tone" | "icon" | "children"
> {
  /** Raw task or subtask status from the API. Ignored when `phase` is given. */
  readonly status?: string;
  readonly phase?: TaskPhase;
  /** Replaces the default label (for example "Waiting for your approval"). */
  readonly label?: string;
  /** A completed task with a failed step: warning tone, "Completed with errors". */
  readonly partial?: boolean;
}

/**
 * Status as icon plus words on the shared Badge. Failed is a danger tone with
 * an icon and the word "Failed": no glow, no colour-only signal. Static: a
 * running task is shown by its label, not by a spinner.
 */
export function TaskStatusBadge({
  status,
  phase,
  label,
  partial = false,
  className,
  ...rest
}: TaskStatusBadgeProps): React.JSX.Element {
  const resolved = phase ?? phaseOfStatus(status ?? "");
  const isPartial = partial && resolved === "completed";
  const spec = isPartial
    ? { tone: "warning" as Tone, icon: AlertTriangle }
    : SPEC[resolved];
  return (
    <Badge
      {...rest}
      tone={spec.tone}
      icon={spec.icon}
      data-phase={resolved}
      data-partial={isPartial ? "true" : undefined}
      className={`shrink-0 ${className ?? ""}`.trim()}
    >
      {label ?? (isPartial ? PARTIAL_LABEL : PHASE_LABEL[resolved])}
    </Badge>
  );
}
