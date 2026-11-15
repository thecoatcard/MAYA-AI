"use client";

import { Loader2 } from "lucide-react";
import React, { forwardRef } from "react";

import { Icon } from "./Icon";
import { cx } from "./cx";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

const VARIANT: Record<ButtonVariant, string> = {
  primary:
    "motion-lift border-maya-accent bg-maya-accent text-maya-accent-text hover:border-maya-accent-hover hover:bg-maya-accent-hover hover:shadow-maya-lift active:bg-maya-accent-active",
  secondary:
    "motion-lift border-maya-border-strong bg-maya-raised text-maya-text hover:bg-maya-hover hover:shadow-maya-lift active:bg-maya-pressed",
  ghost:
    "border-transparent bg-transparent text-maya-secondary hover:bg-maya-hover hover:text-maya-text active:bg-maya-pressed",
  danger:
    "border-maya-danger bg-maya-danger-soft text-maya-danger hover:bg-maya-danger-soft hover:shadow-maya-lift",
};

/** Hit height is at least 32 px (sm) and 44 px under a coarse pointer, for every size. */
const SIZE: Record<ButtonSize, string> = {
  sm: "min-h-8 px-3 text-maya-caption pointer-coarse:min-h-11",
  md: "min-h-9 px-4 text-maya-small pointer-coarse:min-h-11",
  lg: "min-h-11 px-5 text-maya-body",
};

const BASE =
  "maya-button motion-interactive motion-press relative inline-flex max-w-full min-w-0 items-center justify-center gap-2 rounded-maya-md border font-medium select-none disabled:cursor-not-allowed disabled:border-maya-border disabled:bg-maya-disabled-bg disabled:text-maya-disabled-text disabled:shadow-none";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  readonly variant?: ButtonVariant;
  readonly size?: ButtonSize;
  /**
   * Shows a spinner and blocks clicks. The label keeps its space (it is only made invisible), so
   * the button does not change size and nothing around it moves.
   */
  readonly loading?: boolean;
  readonly leftIcon?: React.ReactNode;
  readonly rightIcon?: React.ReactNode;
}

/** Action button. Defaults to type="button" so it never submits a form by accident. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      variant = "secondary",
      size = "md",
      loading = false,
      leftIcon,
      rightIcon,
      disabled = false,
      className,
      children,
      type = "button",
      onClick,
      ...rest
    },
    ref,
  ) {
    return (
      <button
        {...rest}
        ref={ref}
        type={type}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        onClick={(event) => {
          if (loading) return;
          onClick?.(event);
        }}
        className={cx(BASE, VARIANT[variant], SIZE[size], className)}
      >
        {loading && (
          <span className="absolute inset-0 grid place-items-center">
            <Icon
              icon={Loader2}
              size={16}
              className="animate-spin motion-reduce:animate-none"
            />
            <span className="maya-sr-only">Working</span>
          </span>
        )}
        <span
          className={cx(
            "inline-flex min-w-0 items-center justify-center gap-2",
            loading && "invisible",
          )}
        >
          {leftIcon !== undefined && (
            <span className="shrink-0" aria-hidden="true">
              {leftIcon}
            </span>
          )}
          {children !== undefined && children !== null && (
            <span className="min-w-0 truncate">{children}</span>
          )}
          {rightIcon !== undefined && (
            <span className="maya-nudge-icon shrink-0" aria-hidden="true">
              {rightIcon}
            </span>
          )}
        </span>
      </button>
    );
  },
);

export interface IconButtonProps extends Omit<
  ButtonProps,
  "leftIcon" | "rightIcon" | "children" | "aria-label"
> {
  /** Required accessible name, also shown as the native tooltip. */
  readonly label: string;
  readonly icon: React.ReactNode;
}

/** Square icon-only button. The label is mandatory: an icon alone is never a name. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton(
    { label, icon, size = "md", variant = "ghost", className, ...rest },
    ref,
  ) {
    return (
      <Button
        {...rest}
        ref={ref}
        variant={variant}
        size={size}
        aria-label={label}
        title={label}
        className={cx(
          "aspect-square !px-0",
          size === "sm" && "min-w-8 pointer-coarse:min-w-11",
          size === "md" && "min-w-9 pointer-coarse:min-w-11",
          size === "lg" && "min-w-11",
          className,
        )}
      >
        <span aria-hidden="true" className="grid place-items-center">
          {icon}
        </span>
      </Button>
    );
  },
);
