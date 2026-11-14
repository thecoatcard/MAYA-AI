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
  default: "bg-maya-surface border border-maya-border text-maya-text",
  teal: "bg-maya-surface border border-maya-accent text-maya-text",
  raised:
    "bg-maya-raised border border-maya-border-strong text-maya-text shadow-maya-1",
  ghost: "bg-transparent border border-maya-border text-maya-text",
  glow: "bg-maya-surface border border-maya-accent text-maya-text",
};

const paddingStyles: Record<GlassCardPadding, string> = {
  none: "p-0",
  sm: "p-3 sm:p-4",
  md: "p-5 sm:p-6",
  lg: "p-6 sm:p-8",
};

/* Hover changes the border only; nothing lifts, glows or blurs. */
const hoverStyles: Record<GlassCardHover, string> = {
  none: "",
  lift: "hover:border-maya-border-strong transition-colors duration-150",
  glow: "hover:border-maya-accent transition-colors duration-150",
  "lift-glow": "hover:border-maya-accent transition-colors duration-150",
};

/**
 * GlassCard: flat token-backed container with a hairline border and optional
 * header/footer slots. The name is kept for existing imports; there is no
 * blur, glow or translucency.
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
        className={`relative rounded-maya-lg overflow-hidden ${variantClass} ${hoverClass} ${className}`.trim()}
        {...props}
      >
        {header && (
          <div className="border-b border-maya-border px-5 py-3 sm:px-6">
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
          <div className="border-t border-maya-border px-5 py-3 sm:px-6">
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
      className={`flex items-center justify-between gap-4 border-b border-maya-border pb-3 mb-4 ${className}`.trim()}
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
      className={`text-base font-semibold text-maya-text tracking-tight flex items-center gap-2 ${className}`.trim()}
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
      className={`text-maya-caption text-maya-muted leading-relaxed mt-0.5 ${className}`.trim()}
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
      className={`flex items-center justify-between gap-3 border-t border-maya-border pt-4 mt-4 ${className}`.trim()}
      {...props}
    >
      {children}
    </div>
  );
}
