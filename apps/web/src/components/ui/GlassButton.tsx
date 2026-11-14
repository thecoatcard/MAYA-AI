"use client";

import { Loader2 } from "lucide-react";
import React, { forwardRef } from "react";

export type GlassButtonVariant =
  "primary" | "secondary" | "outline" | "ghost" | "danger";

export type GlassButtonSize = "sm" | "md" | "lg";

export interface GlassButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  readonly variant?: GlassButtonVariant;
  readonly size?: GlassButtonSize;
  readonly loading?: boolean;
  readonly loadingText?: string;
  readonly leftIcon?: React.ReactNode;
  readonly rightIcon?: React.ReactNode;
  readonly ripple?: boolean;
}

const variantStyles: Record<GlassButtonVariant, string> = {
  primary:
    "bg-maya-accent hover:bg-maya-accent-hover border border-maya-accent text-maya-accent-text",
  secondary:
    "bg-maya-raised hover:bg-maya-hover border border-maya-border-strong text-maya-text",
  outline:
    "bg-transparent hover:bg-maya-hover border border-maya-control-border text-maya-text",
  ghost:
    "bg-transparent hover:bg-maya-hover border border-transparent text-maya-muted hover:text-maya-text",
  danger:
    "bg-maya-danger-soft hover:bg-maya-danger-soft border border-maya-danger text-maya-danger",
};

const sizeStyles: Record<GlassButtonSize, string> = {
  sm: "text-xs px-2.5 py-1.5 h-8 gap-1.5 rounded-maya-md",
  md: "text-xs md:text-sm px-4 py-2 h-10 gap-2 rounded-maya-md",
  lg: "text-sm md:text-base px-5 py-2.5 h-12 gap-2.5 rounded-maya-md",
};

const iconSizes: Record<GlassButtonSize, string> = {
  sm: "w-3.5 h-3.5",
  md: "w-4 h-4",
  lg: "w-5 h-5",
};

/**
 * GlassButton: token-backed action button with variants, sizes, loading state
 * and icon slots. The `ripple` prop is accepted for compatibility and ignored.
 */
export const GlassButton = forwardRef<HTMLButtonElement, GlassButtonProps>(
  function GlassButton(
    {
      variant = "secondary",
      size = "md",
      loading = false,
      loadingText,
      leftIcon,
      rightIcon,
      // eslint-disable-next-line @typescript-eslint/no-unused-vars -- accepted for compatibility, intentionally ignored
      ripple: _ripple = true,
      disabled = false,
      className = "",
      onClick,
      children,
      ...props
    },
    ref,
  ) {
    const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
      if (disabled || loading) return;
      onClick?.(e);
    };

    const isDisabled = disabled || loading;
    const variantClass = variantStyles[variant];
    const sizeClass = sizeStyles[size];
    const spinnerClass = iconSizes[size];

    return (
      <button
        ref={ref}
        type={props.type ?? "button"}
        disabled={isDisabled}
        aria-busy={loading}
        onClick={handleClick}
        className={`relative inline-flex items-center justify-center font-medium select-none overflow-hidden transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-maya-focus disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none ${variantClass} ${sizeClass} ${className}`.trim()}
        {...props}
      >
        {loading ? (
          <>
            <Loader2
              className={`${spinnerClass} animate-spin motion-reduce:animate-none shrink-0`}
              aria-hidden="true"
            />
            <span>{loadingText ?? children}</span>
          </>
        ) : (
          <>
            {leftIcon && (
              <span className="shrink-0 flex items-center">{leftIcon}</span>
            )}
            {children && <span>{children}</span>}
            {rightIcon && (
              <span className="shrink-0 flex items-center">{rightIcon}</span>
            )}
          </>
        )}
      </button>
    );
  },
);
