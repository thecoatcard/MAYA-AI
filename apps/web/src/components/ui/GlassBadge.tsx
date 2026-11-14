"use client";

import React, { forwardRef } from "react";

export type GlassBadgeVariant =
  "teal" | "emerald" | "amber" | "rose" | "blue" | "zinc" | "purple";

export type GlassBadgeSize = "sm" | "md" | "lg";

export interface GlassBadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  readonly variant?: GlassBadgeVariant;
  readonly size?: GlassBadgeSize;
  readonly dot?: boolean;
  readonly dotPulse?: boolean;
  readonly mono?: boolean;
  readonly icon?: React.ReactNode;
  readonly children?: React.ReactNode;
}

const variantStyles: Record<
  GlassBadgeVariant,
  { pill: string; dot: string; glow: string }
> = {
  teal: {
    pill: "bg-maya-surface text-maya-accent border-maya-accent",
    dot: "bg-maya-accent",
    glow: "",
  },
  emerald: {
    pill: "bg-maya-success-soft text-maya-success border-maya-success",
    dot: "bg-maya-success",
    glow: "",
  },
  amber: {
    pill: "bg-maya-warning-soft text-maya-warning border-maya-warning",
    dot: "bg-maya-warning",
    glow: "",
  },
  rose: {
    pill: "bg-maya-danger-soft text-maya-danger border-maya-danger",
    dot: "bg-maya-danger",
    glow: "",
  },
  blue: {
    pill: "bg-maya-info-soft text-maya-info border-maya-info",
    dot: "bg-maya-info",
    glow: "",
  },
  zinc: {
    pill: "bg-maya-surface text-maya-muted border-maya-border-strong",
    dot: "bg-maya-muted",
    glow: "",
  },
  purple: {
    pill: "bg-maya-surface text-maya-text border-maya-border-strong",
    dot: "bg-maya-text",
    glow: "",
  },
};

const sizeStyles: Record<
  GlassBadgeSize,
  { pill: string; dot: string; icon: string }
> = {
  sm: {
    pill: "text-maya-caption px-2 py-0.5 gap-1.5",
    dot: "w-1.5 h-1.5",
    icon: "w-3 h-3",
  },
  md: {
    pill: "text-maya-caption px-2.5 py-1 gap-1.5",
    dot: "w-2 h-2",
    icon: "w-3.5 h-3.5",
  },
  lg: {
    pill: "text-maya-caption md:text-sm px-3.5 py-1.5 gap-2",
    dot: "w-2.5 h-2.5",
    icon: "w-4 h-4",
  },
};

/**
 * GlassBadge: status pill. Meaning always comes from the label as well as the
 * colour; the optional dot is decorative. No glow or blur.
 */
export const GlassBadge = forwardRef<HTMLSpanElement, GlassBadgeProps>(
  function GlassBadge(
    {
      variant = "zinc",
      size = "md",
      dot = false,
      dotPulse = false,
      mono = false,
      icon,
      className = "",
      children,
      ...props
    },
    ref,
  ) {
    const style = variantStyles[variant];
    const sizeConfig = sizeStyles[size];
    const monoClass = mono ? "font-mono tracking-wider" : "font-medium";

    return (
      <span
        ref={ref}
        className={`inline-flex items-center rounded-full border select-none transition-colors duration-150 ${style.pill} ${sizeConfig.pill} ${monoClass} ${className}`.trim()}
        {...props}
      >
        {dot && (
          <span
            className={`rounded-full shrink-0 ${sizeConfig.dot} ${style.dot} ${style.glow} ${
              dotPulse ? "animate-pulse motion-reduce:animate-none" : ""
            }`}
            aria-hidden="true"
          />
        )}
        {icon && (
          <span className={`shrink-0 flex items-center ${sizeConfig.icon}`}>
            {icon}
          </span>
        )}
        {children}
      </span>
    );
  },
);
