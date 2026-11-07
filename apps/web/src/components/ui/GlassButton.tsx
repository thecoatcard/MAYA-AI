"use client";

import { Loader2 } from "lucide-react";
import React, { forwardRef, useState } from "react";

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

interface RipplePoint {
  readonly id: number;
  readonly x: number;
  readonly y: number;
  readonly size: number;
}

const variantStyles: Record<GlassButtonVariant, string> = {
  primary:
    "bg-teal-500/20 hover:bg-teal-500/30 active:bg-teal-500/35 border border-teal-500/40 text-teal-200 shadow-sm shadow-teal-500/10 focus-visible:ring-teal-400 focus-visible:border-teal-400",
  secondary:
    "bg-white/5 hover:bg-white/10 active:bg-white/15 border border-white/10 text-zinc-200 hover:text-white shadow-sm shadow-black/20 focus-visible:ring-zinc-400",
  outline:
    "bg-transparent hover:bg-white/5 active:bg-white/10 border border-white/20 text-zinc-300 hover:text-white focus-visible:ring-zinc-400",
  ghost:
    "bg-transparent hover:bg-white/5 active:bg-white/10 border border-transparent text-zinc-400 hover:text-zinc-100 focus-visible:ring-zinc-400",
  danger:
    "bg-rose-500/15 hover:bg-rose-500/25 active:bg-rose-500/30 border border-rose-500/35 text-rose-300 shadow-sm shadow-rose-500/10 focus-visible:ring-rose-400",
};

const sizeStyles: Record<GlassButtonSize, string> = {
  sm: "text-xs px-2.5 py-1.5 h-8 gap-1.5 rounded-lg",
  md: "text-xs md:text-sm px-4 py-2 h-10 gap-2 rounded-lg",
  lg: "text-sm md:text-base px-5 py-2.5 h-12 gap-2.5 rounded-xl",
};

const iconSizes: Record<GlassButtonSize, string> = {
  sm: "w-3.5 h-3.5",
  md: "w-4 h-4",
  lg: "w-5 h-5",
};

/**
 * GlassButton - High-polish glassmorphic action button with multiple variants,
 * size options, tactile ripple effect, loading indicator, and icon slots.
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
      ripple = true,
      disabled = false,
      className = "",
      onClick,
      children,
      ...props
    },
    ref,
  ) {
    const [ripples, setRipples] = useState<readonly RipplePoint[]>([]);

    const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
      if (disabled || loading) return;

      if (ripple) {
        const rect = e.currentTarget.getBoundingClientRect();
        const rippleSize = Math.max(rect.width, rect.height) * 2;
        const x = e.clientX - rect.left - rippleSize / 2;
        const y = e.clientY - rect.top - rippleSize / 2;
        const newRipple: RipplePoint = {
          id: Date.now(),
          x,
          y,
          size: rippleSize,
        };

        setRipples((prev) => [...prev, newRipple]);
        setTimeout(() => {
          setRipples((prev) => prev.filter((r) => r.id !== newRipple.id));
        }, 500);
      }

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
        className={`relative inline-flex items-center justify-center font-medium select-none backdrop-blur-md overflow-hidden transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950 disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none ${variantClass} ${sizeClass} ${className}`.trim()}
        {...props}
      >
        {/* Ripple effect layer */}
        {ripples.map((r) => (
          <span
            key={r.id}
            className="absolute rounded-full bg-white/20 pointer-events-none animate-ping motion-reduce:hidden"
            style={{
              left: r.x,
              top: r.y,
              width: r.size,
              height: r.size,
            }}
          />
        ))}

        {loading ? (
          <>
            <Loader2
              className={`${spinnerClass} animate-spin shrink-0`}
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
