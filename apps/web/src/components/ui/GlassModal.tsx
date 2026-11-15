"use client";

import { X } from "lucide-react";
import React, { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";

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
  sm: "max-w-[min(440px,92vw)]",
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
  full: "max-w-6xl",
};

/**
 * GlassModal: accessible dialog on a vibrancy sheet over a blurred scrim (ui-surfaces.css; opaque fallback). ESC dismissal,
 * focus trap, focus restore and WAI-ARIA labelling.
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

        if (
          e.shiftKey &&
          (document.activeElement === firstElement ||
            document.activeElement === modalRef.current ||
            document.activeElement?.getAttribute("role") === "dialog")
        ) {
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

    // Auto-focus container or first element (do not steal focus if a child already focused something)
    requestAnimationFrame(() => {
      if (modalRef.current) {
        if (
          modalRef.current.contains(document.activeElement) &&
          document.activeElement !== modalRef.current
        ) {
          return;
        }
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

  // Portalled to <body>: an ancestor with backdrop-filter or transform (the vibrancy sidebar) would
  // otherwise become the containing block of `position: fixed` and squeeze the dialog into it.
  if (typeof document === "undefined") return null;
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? titleId : undefined}
      aria-describedby={description ? descId : undefined}
      onClick={handleBackdropClick}
      className="maya-sheet-scrim maya-vib-scrim fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6"
    >
      <div
        ref={modalRef}
        tabIndex={-1}
        className={`maya-sheet maya-vib-sheet w-full ${sizeClass} text-maya-text flex flex-col relative overflow-hidden focus:outline-none ${className}`.trim()}
      >
        {(title || showCloseButton) && (
          <div className="flex items-start justify-between gap-4 p-5 sm:p-6 border-b border-maya-border">
            <div className="flex flex-col gap-1">
              {title && (
                <h2
                  id={titleId}
                  className="text-lg font-semibold text-maya-text tracking-tight"
                >
                  {title}
                </h2>
              )}
              {description && (
                <p
                  id={descId}
                  className="text-xs text-maya-muted leading-relaxed"
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
                className="p-1.5 rounded-maya-md text-maya-muted hover:text-maya-text hover:bg-maya-hover motion-interactive focus-visible:outline-2 focus-visible:outline-maya-focus"
              >
                <X size={16} strokeWidth={1.75} aria-hidden="true" />
              </button>
            )}
          </div>
        )}

        <div className="p-5 sm:p-6 overflow-y-auto max-h-[calc(100vh-14rem)]">
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}

export function GlassModalHeader({
  className = "",
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return (
    <div
      className={`flex items-start justify-between gap-4 pb-4 border-b border-maya-border ${className}`.trim()}
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
      className={`text-lg font-semibold text-maya-text tracking-tight ${className}`.trim()}
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
      className={`text-xs text-maya-muted leading-relaxed mt-1 ${className}`.trim()}
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
      className={`flex items-center justify-end gap-3 pt-4 border-t border-maya-border mt-4 ${className}`.trim()}
      {...props}
    >
      {children}
    </div>
  );
}
