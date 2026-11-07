"use client";

import { X } from "lucide-react";
import React, { useEffect, useId, useRef } from "react";

export type GlassModalSize = "sm" | "md" | "lg" | "xl" | "full";

export interface GlassModalProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly title?: React.ReactNode;
  readonly description?: React.ReactNode;
  readonly size?: GlassModalSize;
  readonly closeOnBackdropClick?: boolean;
  readonly closeOnEsc?: boolean;
  readonly showCloseButton?: boolean;
  readonly className?: string;
  readonly children?: React.ReactNode;
}

const sizeStyles: Record<GlassModalSize, string> = {
  sm: "max-w-md",
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
  full: "max-w-6xl",
};

/**
 * GlassModal - Accessible high-polish glassmorphism dialog modal.
 * Features backdrop blur (backdrop-blur-xl bg-black/60), smooth transitions,
 * ESC key dismissal, focus trapping, and WAI-ARIA compliance.
 */
export function GlassModal({
  isOpen,
  onClose,
  title,
  description,
  size = "md",
  closeOnBackdropClick = true,
  closeOnEsc = true,
  showCloseButton = true,
  className = "",
  children,
}: GlassModalProps): React.JSX.Element | null {
  const modalRef = useRef<HTMLDivElement>(null);
  const previousActiveElement = useRef<HTMLElement | null>(null);

  const titleId = useId();
  const descId = useId();

  // Focus trap and ESC key handler
  useEffect(() => {
    if (!isOpen) return;

    previousActiveElement.current = document.activeElement as HTMLElement;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (closeOnEsc && e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }

      if (e.key === "Tab" && modalRef.current) {
        const focusableElements =
          modalRef.current.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
          );
        const focusable = Array.from(focusableElements).filter(
          (el) =>
            !el.hasAttribute("disabled") &&
            el.getAttribute("aria-hidden") !== "true",
        );

        if (focusable.length === 0) return;

        const firstElement = focusable[0];
        const lastElement = focusable[focusable.length - 1];

        if (e.shiftKey && document.activeElement === firstElement) {
          e.preventDefault();
          lastElement.focus();
        } else if (!e.shiftKey && document.activeElement === lastElement) {
          e.preventDefault();
          firstElement.focus();
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // Auto-focus container or first element
    requestAnimationFrame(() => {
      if (modalRef.current) {
        const firstFocusable = modalRef.current.querySelector<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (firstFocusable) {
          firstFocusable.focus();
        } else {
          modalRef.current.focus();
        }
      }
    });

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = originalOverflow;
      if (previousActiveElement.current) {
        previousActiveElement.current.focus();
      }
    };
  }, [isOpen, closeOnEsc, onClose]);

  if (!isOpen) return null;

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (closeOnBackdropClick && e.target === e.currentTarget) {
      onClose();
    }
  };

  const sizeClass = sizeStyles[size];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? titleId : undefined}
      aria-describedby={description ? descId : undefined}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 bg-black/60 backdrop-blur-xl transition-all duration-200"
    >
      <div
        ref={modalRef}
        tabIndex={-1}
        className={`w-full ${sizeClass} rounded-2xl border border-white/10 bg-zinc-950/85 backdrop-blur-2xl shadow-2xl shadow-black/60 text-zinc-100 flex flex-col relative overflow-hidden transition-all duration-200 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.12)] focus:outline-none ${className}`.trim()}
      >
        {(title || showCloseButton) && (
          <div className="flex items-start justify-between gap-4 p-5 sm:p-6 border-b border-white/5">
            <div className="flex flex-col gap-1">
              {title && (
                <h2
                  id={titleId}
                  className="text-lg font-semibold text-zinc-100 tracking-tight"
                >
                  {title}
                </h2>
              )}
              {description && (
                <p
                  id={descId}
                  className="text-xs text-zinc-400 leading-relaxed"
                >
                  {description}
                </p>
              )}
            </div>

            {showCloseButton && (
              <button
                type="button"
                onClick={onClose}
                aria-label="Close modal"
                className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-100 hover:bg-white/10 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
        )}

        <div className="p-5 sm:p-6 overflow-y-auto max-h-[calc(100vh-14rem)]">
          {children}
        </div>
      </div>
    </div>
  );
}

export function GlassModalHeader({
  className = "",
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return (
    <div
      className={`flex items-start justify-between gap-4 pb-4 border-b border-white/5 ${className}`.trim()}
      {...props}
    >
      {children}
    </div>
  );
}

export function GlassModalTitle({
  className = "",
  children,
  ...props
}: React.HTMLAttributes<HTMLHeadingElement>): React.JSX.Element {
  return (
    <h2
      className={`text-lg font-semibold text-zinc-100 tracking-tight ${className}`.trim()}
      {...props}
    >
      {children}
    </h2>
  );
}

export function GlassModalDescription({
  className = "",
  children,
  ...props
}: React.HTMLAttributes<HTMLParagraphElement>): React.JSX.Element {
  return (
    <p
      className={`text-xs text-zinc-400 leading-relaxed mt-1 ${className}`.trim()}
      {...props}
    >
      {children}
    </p>
  );
}

export function GlassModalContent({
  className = "",
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return (
    <div className={`space-y-4 py-2 ${className}`.trim()} {...props}>
      {children}
    </div>
  );
}

export function GlassModalFooter({
  className = "",
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return (
    <div
      className={`flex items-center justify-end gap-3 pt-4 border-t border-white/5 mt-4 ${className}`.trim()}
      {...props}
    >
      {children}
    </div>
  );
}
