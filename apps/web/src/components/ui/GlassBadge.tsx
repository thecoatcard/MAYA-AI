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
    pill: "bg-teal-500/10 text-teal-300 border-teal-500/30 shadow-sm shadow-teal-500/5",
    dot: "bg-teal-400",
    glow: "shadow-[0_0_8px_rgba(45,212,191,0.6)]",
  },
  emerald: {
    pill: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30 shadow-sm shadow-emerald-500/5",
    dot: "bg-emerald-400",
    glow: "shadow-[0_0_8px_rgba(52,211,153,0.6)]",
  },
  amber: {
    pill: "bg-amber-500/10 text-amber-300 border-amber-500/30 shadow-sm shadow-amber-500/5",
    dot: "bg-amber-400",
    glow: "shadow-[0_0_8px_rgba(251,191,36,0.6)]",
  },
  rose: {
    pill: "bg-rose-500/10 text-rose-300 border-rose-500/30 shadow-sm shadow-rose-500/5",
    dot: "bg-rose-400",
    glow: "shadow-[0_0_8px_rgba(244,63,94,0.6)]",
  },
  blue: {
    pill: "bg-blue-500/10 text-blue-300 border-blue-500/30 shadow-sm shadow-blue-500/5",
    dot: "bg-blue-400",
    glow: "shadow-[0_0_8px_rgba(96,165,250,0.6)]",
  },
  zinc: {
    pill: "bg-zinc-800/60 text-zinc-300 border-zinc-700/60 shadow-sm shadow-black/10",
    dot: "bg-zinc-400",
    glow: "shadow-[0_0_8px_rgba(161,161,170,0.5)]",
  },
  purple: {
    pill: "bg-purple-500/10 text-purple-300 border-purple-500/30 shadow-sm shadow-purple-500/5",
    dot: "bg-purple-400",
    glow: "shadow-[0_0_8px_rgba(192,132,252,0.6)]",
  },
};

const sizeStyles: Record<
  GlassBadgeSize,
  { pill: string; dot: string; icon: string }
> = {
  sm: {
    pill: "text-[10px] px-2 py-0.5 gap-1.5",
    dot: "w-1.5 h-1.5",
    icon: "w-3 h-3",
  },
  md: {
    pill: "text-xs px-2.5 py-1 gap-1.5",
    dot: "w-2 h-2",
    icon: "w-3.5 h-3.5",
  },
  lg: {
    pill: "text-xs md:text-sm px-3.5 py-1.5 gap-2",
    dot: "w-2.5 h-2.5",
    icon: "w-4 h-4",
  },
};

/**
 * GlassBadge - Status pill and indicator badge with glassmorphism glow,
 * rich color variants, pulsing dot indicator, and monospace font option.
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
        className={`inline-flex items-center rounded-full border backdrop-blur-md select-none transition-colors duration-150 ${style.pill} ${sizeConfig.pill} ${monoClass} ${className}`.trim()}
        {...props}
      >
        {dot && (
          <span
            className={`rounded-full shrink-0 ${sizeConfig.dot} ${style.dot} ${style.glow} ${
              dotPulse ? "animate-pulse" : ""
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
