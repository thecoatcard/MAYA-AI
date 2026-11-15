"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Inbox,
  Info,
  RotateCcw,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import React, { useCallback, useEffect, useRef, useState } from "react";

import { Button, IconButton } from "./Button";
import { Icon, ICON_STROKE } from "./Icon";
import { cx } from "./cx";

/* ------------------------------------------------------------------ Skeleton */

export interface SkeletonProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Number of text-line bars. 1 renders a single block you can size with className. */
  readonly lines?: number;
}

/**
 * Loading placeholder with a shimmer (static under reduced motion). Hidden from assistive tech:
 * put aria-busy on the region that is loading and say so in text.
 */
export function Skeleton({
  lines = 1,
  className,
  ...rest
}: SkeletonProps): React.JSX.Element {
  if (lines <= 1) {
    return (
      <div
        {...rest}
        aria-hidden="true"
        className={cx(
          "maya-skeleton motion-shimmer h-3 w-full rounded-maya-sm bg-maya-raised",
          className,
        )}
      />
    );
  }
  return (
    <div
      {...rest}
      aria-hidden="true"
      className={cx("grid content-start gap-2", className)}
    >
      {Array.from({ length: lines }, (_, index) => (
        <div
          key={index}
          className={cx(
            "maya-skeleton motion-shimmer h-3 rounded-maya-sm bg-maya-raised",
            index === lines - 1 ? "w-2/3" : "w-full",
          )}
        />
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- EmptyState */

export interface EmptyStateProps extends Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "title"
> {
  readonly title: string;
  readonly description?: React.ReactNode;
  readonly icon?: LucideIcon;
  /** The next step, for example a Button. */
  readonly action?: React.ReactNode;
}

/** "Nothing here yet" with a next step. Not for errors: use ErrorState. */
export function EmptyState({
  title,
  description,
  icon: EmptyIcon = Inbox,
  action,
  className,
  ...rest
}: EmptyStateProps): React.JSX.Element {
  return (
    <div
      {...rest}
      className={cx(
        "maya-empty grid min-w-0 content-center justify-items-center gap-2 rounded-maya-lg border border-dashed border-maya-border-strong p-8 text-center",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="mb-1 grid size-11 place-items-center rounded-maya-lg bg-maya-raised text-maya-muted"
      >
        <EmptyIcon size={20} strokeWidth={ICON_STROKE} />
      </span>
      <p className="m-0 min-w-0 text-maya-body font-medium [overflow-wrap:anywhere]">
        {title}
      </p>
      {description !== undefined && (
        <p className="m-0 max-w-prose min-w-0 text-maya-small text-maya-secondary [overflow-wrap:anywhere]">
          {description}
        </p>
      )}
      {action !== undefined && <div className="mt-2">{action}</div>}
    </div>
  );
}

/* ---------------------------------------------------------------- ErrorState */

export interface ErrorStateProps extends Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "title"
> {
  readonly title: string;
  /** What happened and what to do. Never a stack trace or secret. */
  readonly message?: React.ReactNode;
  /** Shorthand: renders a "Try again" button. */
  readonly onRetry?: () => void;
  /** Custom retry or help controls (overrides onRetry). */
  readonly retry?: React.ReactNode;
}

/** A load or action failed. Announced to assistive technology; a failure is never an empty list. */
export function ErrorState({
  title,
  message,
  onRetry,
  retry,
  className,
  ...rest
}: ErrorStateProps): React.JSX.Element {
  const control =
    retry ??
    (onRetry !== undefined ? (
      <Button
        size="sm"
        variant="secondary"
        leftIcon={<Icon icon={RotateCcw} size={14} />}
        onClick={onRetry}
      >
        Try again
      </Button>
    ) : null);
  return (
    <div
      {...rest}
      role="alert"
      className={cx(
        "maya-error grid min-w-0 content-start gap-2 rounded-maya-lg border border-maya-danger bg-maya-danger-soft p-4 text-maya-text",
        className,
      )}
    >
      <p className="m-0 flex min-w-0 items-start gap-2 text-maya-body font-medium">
        <XCircle
          aria-hidden="true"
          size={20}
          strokeWidth={ICON_STROKE}
          className="mt-0.5 shrink-0 text-maya-danger"
        />
        <span className="min-w-0 [overflow-wrap:anywhere]">{title}</span>
      </p>
      {message !== undefined && (
        <p className="m-0 min-w-0 text-maya-small text-maya-secondary [overflow-wrap:anywhere]">
          {message}
        </p>
      )}
      {control !== null && <div className="mt-1">{control}</div>}
    </div>
  );
}

/* -------------------------------------------------------------------- Notice */

export type NoticeTone = "info" | "success" | "warning" | "danger";

const NOTICE_TONE: Record<NoticeTone, string> = {
  info: "border-maya-info bg-maya-info-soft",
  success: "border-maya-success bg-maya-success-soft",
  warning: "border-maya-warning bg-maya-warning-soft",
  danger: "border-maya-danger bg-maya-danger-soft",
};

const NOTICE_ICON: Record<NoticeTone, { Icon: LucideIcon; color: string }> = {
  info: { Icon: Info, color: "text-maya-info" },
  success: { Icon: CheckCircle2, color: "text-maya-success" },
  warning: { Icon: AlertTriangle, color: "text-maya-warning" },
  danger: { Icon: XCircle, color: "text-maya-danger" },
};

export interface NoticeProps extends Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "title"
> {
  readonly tone?: NoticeTone;
  readonly title?: string;
  readonly onDismiss?: () => void;
}

/** Inline message with an icon, optional title and a dismiss button. Text colour stays primary. */
export function Notice({
  tone = "info",
  title,
  onDismiss,
  className,
  children,
  ...rest
}: NoticeProps): React.JSX.Element {
  const { Icon: NoticeGlyph, color } = NOTICE_ICON[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      {...rest}
      className={cx(
        "maya-notice flex min-w-0 items-start gap-3 rounded-maya-lg border p-3 text-maya-small text-maya-text",
        NOTICE_TONE[tone],
        className,
      )}
    >
      <NoticeGlyph
        aria-hidden="true"
        size={20}
        strokeWidth={ICON_STROKE}
        className={cx("mt-0.5 shrink-0", color)}
      />
      <div className="min-w-0 flex-1 [overflow-wrap:anywhere]">
        {title !== undefined && (
          <p className="m-0 font-medium text-maya-text">{title}</p>
        )}
        {children}
      </div>
      {onDismiss !== undefined && (
        <IconButton
          label="Dismiss"
          size="sm"
          icon={<Icon icon={X} size={14} />}
          onClick={onDismiss}
          className="-m-1 shrink-0"
        />
      )}
    </div>
  );
}

/* --------------------------------------------------------------------- Toast */

export interface ToastItem {
  readonly id: string;
  readonly tone?: NoticeTone;
  readonly title?: string;
  readonly message: string;
}

export interface ToastRegionProps {
  readonly toasts: readonly ToastItem[];
  readonly onDismiss: (id: string) => void;
}

/**
 * Toast stack on the named toast layer (--maya-z-toast). Anchored at the top centre, below the
 * header and inside the safe area, so it never covers the composer (bottom) or a drawer
 * controls (right). A shell can move it by setting --maya-toast-top. aria-live announces new
 * toasts without stealing focus.
 */
export function ToastRegion({
  toasts,
  onDismiss,
}: ToastRegionProps): React.JSX.Element {
  return (
    <div
      aria-live="polite"
      aria-label="Notifications"
      role="region"
      data-testid="toast-region"
      className="maya-toast-region pointer-events-none fixed left-1/2 z-maya-toast grid w-[min(28rem,calc(100vw-2*var(--maya-space-4)))] -translate-x-1/2 gap-2"
      style={{
        top: "var(--maya-toast-top, calc(var(--maya-header-height) + var(--maya-space-3)))",
        paddingRight: "var(--maya-safe-right)",
      }}
    >
      {toasts.map((toast) => (
        <div key={toast.id} className="pointer-events-auto">
          <Notice
            tone={toast.tone ?? "info"}
            title={toast.title}
            onDismiss={() => onDismiss(toast.id)}
            className="maya-toast bg-maya-overlay"
          >
            {toast.message}
          </Notice>
        </div>
      ))}
    </div>
  );
}

/** How long a non-error toast stays before it dismisses itself. Errors stay until dismissed. */
export const TOAST_TIMEOUT_MS = 6000;

export interface UseToasts {
  readonly toasts: readonly ToastItem[];
  readonly push: (toast: Omit<ToastItem, "id">) => string;
  readonly dismiss: (id: string) => void;
}

/** Toast state with auto-dismiss (not for danger toasts). Pass the result to ToastRegion. */
export function useToasts(): UseToasts {
  const [toasts, setToasts] = useState<readonly ToastItem[]>([]);
  const timers = useRef(new Map<string, number>());
  const counter = useRef(0);

  const dismiss = useCallback((id: string): void => {
    const timer = timers.current.get(id);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      timers.current.delete(id);
    }
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (toast: Omit<ToastItem, "id">): string => {
      counter.current += 1;
      const id = `toast-${counter.current}`;
      setToasts((current) => [...current, { ...toast, id }]);
      if (toast.tone !== "danger") {
        timers.current.set(
          id,
          window.setTimeout(() => dismiss(id), TOAST_TIMEOUT_MS),
        );
      }
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    const active = timers.current;
    return () => {
      for (const timer of active.values()) window.clearTimeout(timer);
      active.clear();
    };
  }, []);

  return { toasts, push, dismiss };
}
