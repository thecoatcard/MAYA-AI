"use client";

import React, {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import { cx } from "./cx";
import { Kbd } from "./Kbd";
import { computePlacement, type Placement, type Side } from "./position";

const VIEWPORT_MARGIN = 8;

/**
 * Positions a floating element next to an anchor, flipped and shifted so it stays inside the
 * viewport. Re-measures on open, on resize and on any scroll (capture), then writes the result.
 */
function useFloating(
  open: boolean,
  anchorRef: React.RefObject<HTMLElement | null>,
  side: Side,
): {
  readonly floatingRef: React.RefObject<HTMLDivElement | null>;
  readonly placement: Placement | null;
} {
  const floatingRef = useRef<HTMLDivElement | null>(null);
  const [placement, setPlacement] = useState<Placement | null>(null);

  const update = useCallback((): void => {
    const anchor = anchorRef.current;
    const floating = floatingRef.current;
    if (anchor === null || floating === null) return;
    const a = anchor.getBoundingClientRect();
    const f = floating.getBoundingClientRect();
    setPlacement(
      computePlacement({
        anchor: { top: a.top, left: a.left, width: a.width, height: a.height },
        floating: { width: f.width, height: f.height },
        viewport: {
          width: document.documentElement.clientWidth,
          height: document.documentElement.clientHeight,
        },
        side,
        margin: VIEWPORT_MARGIN,
      }),
    );
  }, [anchorRef, side]);

  useLayoutEffect(() => {
    if (!open) {
      setPlacement(null);
      return;
    }
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, update]);

  return { floatingRef, placement };
}

export interface PopoverProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  /** The element the popover is anchored to (usually the trigger button). */
  readonly anchorRef: React.RefObject<HTMLElement | null>;
  readonly side?: Side;
  /** Accessible name of the popover. */
  readonly label: string;
  readonly role?: "dialog" | "menu" | "listbox" | "group";
  readonly id?: string;
  readonly className?: string;
  readonly children: React.ReactNode;
}

/**
 * Base popover. Rendered in a portal on the dropdown layer so no parent can clip it. It closes
 * on Escape and on a press outside it and its anchor. It never leaves the viewport: it flips to
 * the other side and shifts, and its width is capped to the window. Focus handling is up to the
 * content (a menu or dialog built on top of this).
 */
export function Popover({
  open,
  onOpenChange,
  anchorRef,
  side = "bottom",
  label,
  role = "dialog",
  id,
  className,
  children,
}: PopoverProps): React.JSX.Element | null {
  const { floatingRef, placement } = useFloating(open, anchorRef, side);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") onOpenChange(false);
    };
    const onPointer = (event: PointerEvent): void => {
      const target = event.target as Node | null;
      if (
        target !== null &&
        !floatingRef.current?.contains(target) &&
        !anchorRef.current?.contains(target)
      ) {
        onOpenChange(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open, onOpenChange, anchorRef, floatingRef]);

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      ref={floatingRef}
      id={id}
      role={role}
      aria-label={label}
      data-side={placement?.side ?? side}
      className={cx(
        "maya-popover maya-vib-pop fixed z-maya-dropdown max-h-[calc(100dvh-2*var(--maya-space-2))] max-w-[calc(100vw-2*var(--maya-space-2))] overflow-auto p-3 text-maya-small text-maya-text",
        className,
      )}
      // Until measured the element is placed at 0,0 but hidden, so it never flashes in a wrong spot.
      style={{
        top: placement?.top ?? 0,
        left: placement?.left ?? 0,
        visibility: placement === null ? "hidden" : "visible",
      }}
    >
      {children}
    </div>,
    document.body,
  );
}

export interface TooltipProps {
  /** Short text, a hint that repeats or extends the trigger's name. Never the only way to learn something. */
  readonly content: string;
  readonly side?: Side;
  /** Keyboard shortcut shown as a key hint inside the tooltip, for example "Ctrl+B". */
  readonly shortcut?: string;
  /** One focusable element. Its aria-describedby is wired to the tooltip while it shows. */
  readonly children: React.ReactElement<
    React.HTMLAttributes<HTMLElement> & { ref?: React.Ref<HTMLElement> }
  >;
}

/**
 * Tooltip on hover and keyboard focus, closes on Escape, stays inside the viewport. It is
 * supplementary (the trigger keeps its own accessible name) and not for interactive content.
 */
export function Tooltip({
  content,
  side = "top",
  shortcut,
  children,
}: TooltipProps): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLElement | null>(null);
  const tooltipId = useId();
  const { floatingRef, placement } = useFloating(open, anchorRef, side);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const child = React.Children.only(children);
  const trigger = React.cloneElement(child, {
    ref: anchorRef,
    "aria-describedby": open ? tooltipId : child.props["aria-describedby"],
    onMouseEnter: (event: React.MouseEvent<HTMLElement>) => {
      child.props.onMouseEnter?.(event);
      setOpen(true);
    },
    onMouseLeave: (event: React.MouseEvent<HTMLElement>) => {
      child.props.onMouseLeave?.(event);
      setOpen(false);
    },
    onFocus: (event: React.FocusEvent<HTMLElement>) => {
      child.props.onFocus?.(event);
      setOpen(true);
    },
    onBlur: (event: React.FocusEvent<HTMLElement>) => {
      child.props.onBlur?.(event);
      setOpen(false);
    },
  });

  return (
    <>
      {trigger}
      {open &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={floatingRef}
            id={tooltipId}
            role="tooltip"
            data-side={placement?.side ?? side}
            className="maya-popover maya-vib-pop pointer-events-none fixed z-maya-tooltip inline-flex max-w-[min(20rem,calc(100vw-2*var(--maya-space-2)))] items-center gap-2 px-2.5 py-1 text-maya-caption text-maya-text [overflow-wrap:anywhere]"
            style={{
              top: placement?.top ?? 0,
              left: placement?.left ?? 0,
              visibility: placement === null ? "hidden" : "visible",
            }}
          >
            <span>{content}</span>
            {shortcut !== undefined && <Kbd>{shortcut}</Kbd>}
          </div>,
          document.body,
        )}
    </>
  );
}
