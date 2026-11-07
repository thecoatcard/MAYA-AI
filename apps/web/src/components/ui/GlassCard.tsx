"use client";

import React, { forwardRef } from "react";

export type GlassCardVariant = "default" | "teal" | "raised" | "ghost" | "glow";
export type GlassCardPadding = "none" | "sm" | "md" | "lg";
export type GlassCardHover = "none" | "lift" | "glow" | "lift-glow";

export interface GlassCardProps extends React.HTMLAttributes<HTMLDivElement> {
  readonly variant?: GlassCardVariant;
  readonly padding?: GlassCardPadding;
  readonly hoverEffect?: GlassCardHover;
  readonly header?: React.ReactNode;
  readonly footer?: React.ReactNode;
  readonly children?: React.ReactNode;
}

const variantStyles: Record<GlassCardVariant, string> = {
  default:
    "bg-zinc-900/60 backdrop-blur-xl border border-white/10 text-zinc-100 shadow-xl shadow-black/25 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.08)]",
  teal: "bg-zinc-900/60 backdrop-blur-xl border border-teal-500/25 text-zinc-100 shadow-xl shadow-teal-950/20 shadow-[inset_0_1px_0_0_rgba(45,212,191,0.12)]",
  raised:
    "bg-zinc-800/70 backdrop-blur-2xl border border-white/15 text-zinc-100 shadow-2xl shadow-black/40 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.14)]",
  ghost:
    "bg-white/[0.03] backdrop-blur-md border border-white/5 text-zinc-200 shadow-sm shadow-black/10",
  glow: "bg-zinc-900/70 backdrop-blur-xl border border-teal-500/35 text-zinc-100 shadow-[0_0_30px_-5px_rgba(45,212,191,0.18)] shadow-[inset_0_1px_0_0_rgba(45,212,191,0.2)]",
};

const paddingStyles: Record<GlassCardPadding, string> = {
  none: "p-0",
  sm: "p-3 sm:p-4",
  md: "p-5 sm:p-6",
  lg: "p-6 sm:p-8",
};

const hoverStyles: Record<GlassCardHover, string> = {
  none: "",
  lift: "hover:-translate-y-1 hover:border-white/20 hover:shadow-2xl transition-all duration-200 ease-out motion-reduce:hover:translate-y-0",
  glow: "hover:border-teal-500/40 hover:shadow-[0_0_25px_-5px_rgba(45,212,191,0.25)] transition-all duration-200 ease-out",
  "lift-glow":
    "hover:-translate-y-1 hover:border-teal-500/40 hover:shadow-[0_12px_30px_-5px_rgba(45,212,191,0.25)] transition-all duration-200 ease-out motion-reduce:hover:translate-y-0",
};

/**
 * GlassCard - Centralized Glassmorphic container with translucent frosted backdrop,
 * subtle 1px border highlight, inner glow, and optional header/footer slots.
 */
export const GlassCard = forwardRef<HTMLDivElement, GlassCardProps>(
  function GlassCard(
    {
      variant = "default",
      padding = "md",
      hoverEffect = "none",
      header,
      footer,
      className = "",
      children,
      ...props
    },
    ref,
  ) {
    const isCustomPadded = paddingStyles[padding];
    const variantClass = variantStyles[variant];
    const hoverClass = hoverStyles[hoverEffect];

    return (
      <div
        ref={ref}
        className={`relative rounded-2xl overflow-hidden ${variantClass} ${hoverClass} ${className}`.trim()}
        {...props}
      >
        {header && (
          <div className="border-b border-white/5 px-5 py-3 sm:px-6">
            {header}
          </div>
        )}

        <div
          className={
            header || footer
              ? padding === "none"
                ? "p-0"
                : isCustomPadded
              : isCustomPadded
          }
        >
          {children}
        </div>

        {footer && (
          <div className="border-t border-white/5 px-5 py-3 sm:px-6 bg-white/[0.01]">
            {footer}
          </div>
        )}
      </div>
    );
  },
);

export function GlassCardHeader({
  className = "",
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return (
    <div
      className={`flex items-center justify-between gap-4 border-b border-white/5 pb-3 mb-4 ${className}`.trim()}
      {...props}
    >
      {children}
    </div>
  );
}

export function GlassCardTitle({
  className = "",
  children,
  ...props
}: React.HTMLAttributes<HTMLHeadingElement>): React.JSX.Element {
  return (
    <h3
      className={`text-base font-semibold text-zinc-100 tracking-tight flex items-center gap-2 ${className}`.trim()}
      {...props}
    >
      {children}
    </h3>
  );
}

export function GlassCardDescription({
  className = "",
  children,
  ...props
}: React.HTMLAttributes<HTMLParagraphElement>): React.JSX.Element {
  return (
    <p
      className={`text-xs text-zinc-400 leading-relaxed mt-0.5 ${className}`.trim()}
      {...props}
    >
      {children}
    </p>
  );
}

export function GlassCardContent({
  className = "",
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return (
    <div className={`space-y-4 ${className}`.trim()} {...props}>
      {children}
    </div>
  );
}

export function GlassCardFooter({
  className = "",
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return (
    <div
      className={`flex items-center justify-between gap-3 border-t border-white/5 pt-4 mt-4 ${className}`.trim()}
      {...props}
    >
      {children}
    </div>
  );
}
