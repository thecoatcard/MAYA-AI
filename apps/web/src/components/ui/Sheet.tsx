"use client";

import { X } from "lucide-react";
import React, { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useOverlay } from "../layout/useOverlay";
import { IconButton } from "./Button";
import { Icon } from "./Icon";
import { cx } from "./cx";

/** How long a closing sheet stays mounted so its exit can play (the fast duration token). */
export const SHEET_EXIT_MS = 160;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export interface SheetProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly title: string;
  readonly description?: string;
  readonly size?: "sm" | "md" | "lg";
  /** Fixed footer for the actions. */
  readonly footer?: React.ReactNode;
  readonly closeOnScrim?: boolean;
  readonly className?: string;
  readonly children: React.ReactNode;
}

const SIZE = {
  sm: "max-w-[min(440px,92vw)]",
  md: "max-w-xl",
  lg: "max-w-3xl",
} as const;

/**
 * Modal sheet: a vibrancy surface that scales and fades in from 0.97 over a blurred scrim (and
 * back out). It is a real dialog: labelled, focus moves in and is trapped, Escape closes, focus
 * returns to the trigger, the page behind does not scroll. Under reduced motion it appears and
 * disappears at once. Never put animation near approval controls inside a sheet: wrap them in
 * .motion-none.
 */
export function Sheet({
  open,
  onClose,
  title,
  description,
  size = "md",
  footer,
  closeOnScrim = true,
  className,
  children,
}: SheetProps): React.JSX.Element | null {
  const [mounted, setMounted] = useState<boolean>(open);
  const [closing, setClosing] = useState<boolean>(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const titleId = useId();
  const descId = useId();

  useEffect(() => {
    if (open) {
      setMounted(true);
      setClosing(false);
      return undefined;
    }
    if (!mounted) return undefined;
    if (prefersReducedMotion()) {
      setMounted(false);
      return undefined;
    }
    setClosing(true);
    const timer = window.setTimeout(() => {
      setMounted(false);
      setClosing(false);
    }, SHEET_EXIT_MS);
    return () => window.clearTimeout(timer);
  }, [open, mounted]);

  useOverlay(open, onClose, ref);

  useEffect(() => {
    if (!mounted) return undefined;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [mounted]);

  if (!mounted || typeof document === "undefined") return null;

  // Portalled to <body> so no vibrancy/transform ancestor can become its containing block.
  return createPortal(
    <div
      data-testid="sheet-scrim"
      data-state={closing ? "closing" : "open"}
      className="maya-sheet-scrim maya-vib-scrim fixed inset-0 z-maya-modal grid place-items-center p-4 sm:p-6"
      onMouseDown={(event) => {
        if (closeOnScrim && event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description !== undefined ? descId : undefined}
        data-state={closing ? "closing" : "open"}
        data-placement="auto"
        tabIndex={-1}
        className={cx(
          "maya-sheet maya-vib-sheet flex max-h-[calc(100dvh-2rem)] w-full flex-col text-maya-text focus:outline-none",
          SIZE[size],
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 px-6 pt-5 pb-3">
          <div className="min-w-0">
            <h2
              id={titleId}
              className="m-0 text-maya-heading font-semibold tracking-tight"
            >
              {title}
            </h2>
            {description !== undefined && (
              <p
                id={descId}
                className="m-0 mt-1 text-maya-small text-maya-secondary"
              >
                {description}
              </p>
            )}
          </div>
          <IconButton
            label="Close"
            size="sm"
            icon={<Icon icon={X} size={16} />}
            onClick={onClose}
            className="-mt-1 -mr-2 shrink-0"
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-5">
          {children}
        </div>
        {footer !== undefined && (
          <div className="flex items-center justify-end gap-2 px-6 pt-3 pb-5">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
