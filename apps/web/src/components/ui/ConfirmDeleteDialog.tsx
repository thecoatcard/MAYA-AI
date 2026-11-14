"use client";

import { Trash2 } from "lucide-react";
import React, { useEffect, useRef } from "react";

import { Button } from "./Button";
import { GlassModal } from "./GlassModal";

export interface ConfirmDeleteDialogProps {
  readonly title: string;
  /** What goes away. Shown as a list so nothing is hidden in a sentence. */
  readonly removes: readonly string[];
  /** What stays. Shown as a list; leave empty to omit. */
  readonly keeps?: readonly string[];
  /** Extra controls (for example a checkbox) shown above the buttons. */
  readonly children?: React.ReactNode;
  readonly confirmLabel: string;
  readonly busy?: boolean;
  readonly error?: string | null;
  readonly onConfirm: () => void;
  readonly onClose: () => void;
}

/**
 * Confirmation for a delete that cannot be undone. It says exactly what is removed and what is
 * kept, and that there is no undo. Focus starts on Cancel (the safe choice); Escape closes it;
 * focus is trapped and restored by the modal.
 */
export function ConfirmDeleteDialog({
  title,
  removes,
  keeps = [],
  children,
  confirmLabel,
  busy = false,
  error = null,
  onConfirm,
  onClose,
}: ConfirmDeleteDialogProps): React.JSX.Element {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);
  return (
    <GlassModal
      isOpen
      onClose={busy ? () => undefined : onClose}
      title={title}
      size="sm"
    >
      <div className="grid min-w-0 gap-4">
        <div className="grid gap-1" data-testid="delete-removes">
          <p className="m-0 text-maya-small font-medium text-maya-text">
            What will be removed
          </p>
          <ul className="m-0 grid list-disc gap-0.5 pl-5 text-maya-small text-maya-secondary">
            {removes.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
        {keeps.length > 0 && (
          <div className="grid gap-1" data-testid="delete-keeps">
            <p className="m-0 text-maya-small font-medium text-maya-text">
              What is kept
            </p>
            <ul className="m-0 grid list-disc gap-0.5 pl-5 text-maya-small text-maya-secondary">
              {keeps.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        )}
        <p className="m-0 text-maya-small text-maya-muted">
          This cannot be undone.
        </p>
        {children}
        {error !== null && (
          <p role="alert" className="m-0 text-maya-small text-maya-danger">
            {error}
          </p>
        )}
        <div className="mt-1 flex flex-nowrap items-center justify-end gap-2">
          <Button
            ref={cancelRef}
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            size="sm"
            loading={busy}
            leftIcon={<Trash2 size={14} />}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </GlassModal>
  );
}
