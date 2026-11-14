"use client";

import React, {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { ChevronRight, Check } from "lucide-react";

import { cx } from "./cx";

/** One actionable row. A row with `children` opens a one-level submenu instead of acting. */
export interface ContextMenuItem {
  readonly kind?: "item";
  readonly id: string;
  readonly label: string;
  /** A lucide icon element at 14 to 16 px. Decorative: the label carries the meaning. */
  readonly icon?: React.ReactNode;
  /** A shortcut hint such as "Ctrl+C". Display only. */
  readonly shortcut?: string;
  /** Destructive action. The caller still opens ConfirmDeleteDialog from onSelect. */
  readonly danger?: boolean;
  readonly disabled?: boolean;
  /** Plain reason shown under a disabled item and read out with it. */
  readonly disabledReason?: string;
  /** A checkbox row (role menuitemcheckbox) with this state. */
  readonly checked?: boolean;
  readonly onSelect?: () => void;
  /** Submenu entries (one level only; deeper levels are ignored). */
  readonly children?: readonly ContextMenuEntry[];
}

export interface ContextMenuSeparator {
  readonly kind: "separator";
}

/** A labelled group of items. */
export interface ContextMenuGroup {
  readonly kind: "group";
  readonly label: string;
  readonly items: readonly ContextMenuEntry[];
}

export type ContextMenuEntry =
  ContextMenuItem | ContextMenuSeparator | ContextMenuGroup;

export interface MenuPoint {
  readonly x: number;
  readonly y: number;
}

const MARGIN = 8;
export const LONG_PRESS_MS = 450;
const LONG_PRESS_SLOP = 10;

/** Pure placement: opens at the point, flips to the other side of it, clamps to the viewport. */
export function computeMenuPosition(
  point: MenuPoint,
  size: { readonly width: number; readonly height: number },
  viewport: { readonly width: number; readonly height: number },
): { readonly left: number; readonly top: number } {
  let left = point.x;
  let top = point.y;
  if (left + size.width > viewport.width - MARGIN) left = point.x - size.width;
  if (top + size.height > viewport.height - MARGIN) top = point.y - size.height;
  left = Math.max(MARGIN, Math.min(left, viewport.width - size.width - MARGIN));
  top = Math.max(MARGIN, Math.min(top, viewport.height - size.height - MARGIN));
  return { left, top };
}

/** Flattens groups so keyboard navigation sees the actionable rows in order. */
function flatten(entries: readonly ContextMenuEntry[]): ContextMenuItem[] {
  const out: ContextMenuItem[] = [];
  for (const entry of entries) {
    if (entry.kind === "separator") continue;
    if (entry.kind === "group") out.push(...flatten(entry.items));
    else out.push(entry);
  }
  return out;
}

/** Drops leading, trailing and doubled separators and empty groups. */
export function cleanEntries(
  entries: readonly ContextMenuEntry[],
): ContextMenuEntry[] {
  const mapped: ContextMenuEntry[] = [];
  for (const entry of entries) {
    if (entry.kind === "group") {
      const items = cleanEntries(entry.items);
      if (flatten(items).length > 0) mapped.push({ ...entry, items });
    } else mapped.push(entry);
  }
  const out: ContextMenuEntry[] = [];
  for (const entry of mapped) {
    if (entry.kind === "separator") {
      if (out.length === 0 || out[out.length - 1]?.kind === "separator")
        continue;
    }
    out.push(entry);
  }
  while (out[out.length - 1]?.kind === "separator") out.pop();
  return out;
}

// Only one menu is open at a time: opening one closes the previous.
let closeActive: (() => void) | null = null;

/** Closes whichever context menu is open (for example on a route change). */
export function closeAllContextMenus(): void {
  closeActive?.();
}

export interface ContextMenuProps {
  readonly point: MenuPoint;
  readonly items: readonly ContextMenuEntry[];
  /** Accessible name of the menu. */
  readonly label: string;
  readonly onClose: (restoreFocus: boolean) => void;
}

interface Pos {
  readonly left: number;
  readonly top: number;
}

/**
 * The menu surface. Portal to the body, role="menu", roving focus, one submenu level. It closes
 * on Escape, a press outside, scroll, resize and window blur. Rendered by `useContextMenu`.
 */
export function ContextMenu({
  point,
  items,
  label,
  onClose,
}: ContextMenuProps): React.JSX.Element | null {
  const clean = useMemo(() => cleanEntries(items), [items]);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const subRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<Pos | null>(null);
  const [subId, setSubId] = useState<string | null>(null);
  const [subPos, setSubPos] = useState<Pos | null>(null);
  const [focusSub, setFocusSub] = useState(false);
  const typeRef = useRef<{ text: string; at: number }>({ text: "", at: 0 });
  const baseId = useId();

  const close = useCallback((restore: boolean) => onClose(restore), [onClose]);

  // Register as the one open menu.
  useEffect(() => {
    const closer = (): void => close(false);
    closeActive?.();
    closeActive = closer;
    return () => {
      if (closeActive === closer) closeActive = null;
    };
  }, [close]);

  useLayoutEffect(() => {
    const el = menuRef.current;
    if (el === null) return;
    const r = el.getBoundingClientRect();
    setPos(
      computeMenuPosition(
        point,
        { width: r.width, height: r.height },
        {
          width: document.documentElement.clientWidth,
          height: document.documentElement.clientHeight,
        },
      ),
    );
  }, [point, clean]);

  const rows = useCallback(
    (host: HTMLElement | null): HTMLElement[] =>
      host === null
        ? []
        : Array.from(
            host.querySelectorAll<HTMLElement>(
              '[role="menuitem"]:not([aria-disabled="true"]), [role="menuitemcheckbox"]:not([aria-disabled="true"])',
            ),
          ),
    [],
  );

  // Focus moves into the menu (first item) once it is placed.
  useEffect(() => {
    if (pos === null) return;
    rows(menuRef.current)[0]?.focus();
    // Only when first placed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pos === null]);

  // Position the submenu next to its parent row, flipped when there is no room.
  useLayoutEffect(() => {
    if (subId === null) {
      setSubPos(null);
      return;
    }
    const parent = menuRef.current?.querySelector<HTMLElement>(
      `[data-ctx-id="${CSS.escape(subId)}"]`,
    );
    const sub = subRef.current;
    if (!parent || !sub) return;
    const p = parent.getBoundingClientRect();
    const s = sub.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    let left = p.right + 2;
    if (left + s.width > vw - MARGIN) left = p.left - s.width - 2;
    left = Math.max(MARGIN, left);
    const top = Math.max(MARGIN, Math.min(p.top - 4, vh - s.height - MARGIN));
    setSubPos({ left, top });
  }, [subId]);

  useEffect(() => {
    if (subPos === null || !focusSub) return;
    rows(subRef.current)[0]?.focus();
    setFocusSub(false);
  }, [subPos, focusSub, rows]);

  // Dismissal.
  useEffect(() => {
    const onPointer = (event: PointerEvent): void => {
      const t = event.target as Node | null;
      if (t !== null && rootRef.current?.contains(t)) return;
      close(false);
    };
    const onScroll = (event: Event): void => {
      const t = event.target as Node | null;
      if (t !== null && rootRef.current?.contains(t)) return;
      close(false);
    };
    const onResize = (): void => close(false);
    const onBlur = (): void => close(false);
    // A second right-click elsewhere reopens through its own handler; this just closes ours.
    document.addEventListener("pointerdown", onPointer, true);
    document.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    window.addEventListener("blur", onBlur);
    window.addEventListener("popstate", onResize);
    return () => {
      document.removeEventListener("pointerdown", onPointer, true);
      document.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("popstate", onResize);
    };
  }, [close]);

  const activate = (item: ContextMenuItem): void => {
    if (item.disabled === true) return;
    if (item.children !== undefined && item.children.length > 0) {
      setSubId(item.id);
      setFocusSub(true);
      return;
    }
    close(true);
    item.onSelect?.();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    const inSub =
      subRef.current !== null &&
      subRef.current.contains(document.activeElement);
    const host = inSub ? subRef.current : menuRef.current;
    const list = rows(host);
    const current = list.indexOf(document.activeElement as HTMLElement);
    const move = (index: number): void => {
      event.preventDefault();
      list[(index + list.length) % Math.max(1, list.length)]?.focus();
    };
    switch (event.key) {
      case "Escape":
        event.preventDefault();
        event.stopPropagation();
        if (inSub) {
          const parentId = subId;
          setSubId(null);
          menuRef.current
            ?.querySelector<HTMLElement>(
              `[data-ctx-id="${CSS.escape(parentId ?? "")}"]`,
            )
            ?.focus();
        } else close(true);
        return;
      case "Tab":
        event.preventDefault();
        close(true);
        return;
      case "ArrowDown":
        move(current < 0 ? 0 : current + 1);
        return;
      case "ArrowUp":
        move(current < 0 ? list.length - 1 : current - 1);
        return;
      case "Home":
        move(0);
        return;
      case "End":
        move(list.length - 1);
        return;
      case "ArrowRight": {
        const id = (event.target as HTMLElement | null)?.closest<HTMLElement>(
          "[data-ctx-id]",
        )?.dataset.ctxId;
        const item = flatten(clean).find((i) => i.id === id);
        if (!inSub && item?.children !== undefined) {
          event.preventDefault();
          activate(item);
        }
        return;
      }
      case "ArrowLeft":
        if (inSub) {
          event.preventDefault();
          const parentId = subId;
          setSubId(null);
          menuRef.current
            ?.querySelector<HTMLElement>(
              `[data-ctx-id="${CSS.escape(parentId ?? "")}"]`,
            )
            ?.focus();
        }
        return;
      case "Enter":
      case " ": {
        const el = document.activeElement as HTMLElement | null;
        if (el !== null && list.includes(el)) {
          event.preventDefault();
          el.click();
        }
        return;
      }
      default:
        break;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
      const now = Date.now();
      const t = typeRef.current;
      t.text = now - t.at > 600 ? event.key : t.text + event.key;
      t.at = now;
      const needle = t.text.toLowerCase();
      const start = needle.length === 1 ? current + 1 : Math.max(0, current);
      const ordered = [...list.slice(start), ...list.slice(0, start)];
      ordered
        .find((el) =>
          (el.textContent ?? "").trim().toLowerCase().startsWith(needle),
        )
        ?.focus();
    }
  };

  if (typeof document === "undefined") return null;

  const renderEntries = (
    entries: readonly ContextMenuEntry[],
    level: 0 | 1,
  ): React.ReactNode =>
    entries.map((entry, index) => {
      if (entry.kind === "separator") {
        return (
          <div
            key={`sep-${index}`}
            role="separator"
            className="maya-ctx__sep"
          />
        );
      }
      if (entry.kind === "group") {
        const gid = `${baseId}-g${level}-${index}`;
        return (
          <div key={gid} role="group" aria-labelledby={gid}>
            <div id={gid} className="maya-ctx__label">
              {entry.label}
            </div>
            {renderEntries(entry.items, level)}
          </div>
        );
      }
      const hasSub =
        level === 0 &&
        entry.children !== undefined &&
        entry.children.length > 0;
      const reasonId = `${baseId}-r-${entry.id}`;
      const checkable = entry.checked !== undefined;
      return (
        <div
          key={entry.id}
          role={checkable ? "menuitemcheckbox" : "menuitem"}
          aria-checked={checkable ? entry.checked : undefined}
          aria-disabled={entry.disabled === true ? true : undefined}
          aria-haspopup={hasSub ? "menu" : undefined}
          aria-expanded={hasSub ? subId === entry.id : undefined}
          aria-describedby={
            entry.disabled === true && entry.disabledReason !== undefined
              ? reasonId
              : undefined
          }
          data-ctx-id={entry.id}
          data-danger={entry.danger === true ? "true" : undefined}
          tabIndex={-1}
          className="maya-ctx__item"
          onClick={(e) => {
            e.stopPropagation();
            activate(entry);
          }}
          onPointerEnter={() => {
            if (level === 0) setSubId(hasSub ? entry.id : null);
          }}
        >
          <span className="maya-ctx__icon" aria-hidden="true">
            {checkable ? (
              entry.checked === true ? (
                <Check size={14} />
              ) : null
            ) : (
              entry.icon
            )}
          </span>
          <span className="maya-ctx__text">
            {entry.label}
            {entry.disabled === true && entry.disabledReason !== undefined && (
              <span id={reasonId} className="maya-ctx__reason">
                {entry.disabledReason}
              </span>
            )}
          </span>
          {entry.shortcut !== undefined && (
            <span className="maya-ctx__shortcut" aria-hidden="true">
              {entry.shortcut}
            </span>
          )}
          {hasSub && <ChevronRight size={14} aria-hidden="true" />}
        </div>
      );
    });

  const subItem =
    subId === null ? undefined : flatten(clean).find((i) => i.id === subId);

  return createPortal(
    <div ref={rootRef} onKeyDown={onKeyDown} data-testid="context-menu-root">
      <div
        ref={menuRef}
        role="menu"
        aria-label={label}
        className="maya-ctx maya-popover maya-vib-pop fixed z-maya-dropdown"
        style={{
          left: pos?.left ?? point.x,
          top: pos?.top ?? point.y,
          visibility: pos === null ? "hidden" : "visible",
        }}
        onContextMenu={(e) => e.preventDefault()}
      >
        {renderEntries(clean, 0)}
      </div>
      {subItem?.children !== undefined && (
        <div
          ref={subRef}
          role="menu"
          aria-label={subItem.label}
          className="maya-ctx maya-popover maya-vib-pop fixed z-maya-dropdown"
          style={{
            left: subPos?.left ?? 0,
            top: subPos?.top ?? 0,
            visibility: subPos === null ? "hidden" : "visible",
          }}
          onContextMenu={(e) => e.preventDefault()}
        >
          {renderEntries(cleanEntries(subItem.children), 1)}
        </div>
      )}
    </div>,
    document.body,
  );
}

/** Text inputs keep the browser's own menu (Cut, Copy, Paste) unless a surface opts in. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  const el = target.closest("input, textarea, select, [contenteditable]");
  if (el === null) return false;
  if (el instanceof HTMLInputElement) {
    return !["button", "checkbox", "radio", "range", "submit"].includes(
      el.type,
    );
  }
  if (el instanceof HTMLElement && el.isContentEditable) return true;
  return el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement;
}

export interface UseContextMenuOptions {
  /** Menu entries, computed when the menu opens (so nothing is built until asked). */
  readonly getItems: (context: {
    readonly target: Element | null;
  }) => readonly ContextMenuEntry[] | null;
  readonly label: string;
  /** Opt in on text inputs (the composer offers its own text menu). Default: native menu stays. */
  readonly allowEditable?: boolean;
  readonly disabled?: boolean;
}

export interface UseContextMenuResult {
  /** Render once, anywhere in the component: the open menu, or null. */
  readonly menu: React.ReactNode;
  /** Spread on the surface element. */
  readonly triggerProps: {
    readonly onContextMenu: (event: React.MouseEvent) => void;
    readonly onKeyDown: (event: React.KeyboardEvent) => void;
    readonly onPointerDown: (event: React.PointerEvent) => void;
    readonly onPointerMove: (event: React.PointerEvent) => void;
    readonly onPointerUp: (event: React.PointerEvent) => void;
    readonly onPointerCancel: (event: React.PointerEvent) => void;
  };
  /** Props for the visible "..." button that opens the same menu. */
  readonly buttonProps: {
    readonly "aria-haspopup": "menu";
    readonly "aria-expanded": boolean;
    readonly onClick: (event: React.MouseEvent<HTMLElement>) => void;
  };
  readonly openAt: (point: MenuPoint, trigger?: Element | null) => void;
  readonly isOpen: boolean;
  readonly close: () => void;
}

/**
 * Wires a surface to a context menu: right-click, Menu key / Shift+F10, long-press on touch and
 * a "..." button all open the same menu. The native menu is suppressed only when `getItems`
 * returns entries, and never on text inputs unless `allowEditable` is set.
 */
export function useContextMenu({
  getItems,
  label,
  allowEditable = false,
  disabled = false,
}: UseContextMenuOptions): UseContextMenuResult {
  const [open, setOpen] = useState<{
    point: MenuPoint;
    items: readonly ContextMenuEntry[];
  } | null>(null);
  const triggerRef = useRef<Element | null>(null);
  const pressRef = useRef<{
    timer: number;
    x: number;
    y: number;
    target: Element | null;
  } | null>(null);
  const getItemsRef = useRef(getItems);
  getItemsRef.current = getItems;

  const openAt = useCallback(
    (point: MenuPoint, trigger?: Element | null, target?: Element | null) => {
      const items = getItemsRef.current({
        target: target ?? trigger ?? null,
      });
      if (items === null || flatten(cleanEntries(items)).length === 0) return;
      triggerRef.current =
        trigger ?? (document.activeElement as Element | null);
      setOpen({ point, items });
    },
    [],
  );

  const close = useCallback(() => setOpen(null), []);
  const onClose = useCallback((restore: boolean) => {
    setOpen(null);
    const el = triggerRef.current;
    if (restore && el instanceof HTMLElement && el.isConnected) el.focus();
  }, []);

  const clearPress = (): void => {
    if (pressRef.current !== null) {
      window.clearTimeout(pressRef.current.timer);
      pressRef.current = null;
    }
  };
  useEffect(() => clearPress, []);

  const triggerProps: UseContextMenuResult["triggerProps"] = {
    onContextMenu: (event) => {
      if (disabled) return;
      if (!allowEditable && isEditableTarget(event.target)) return;
      const target = event.target instanceof Element ? event.target : null;
      const items = getItems({ target });
      if (items === null || flatten(cleanEntries(items)).length === 0) return;
      event.preventDefault();
      event.stopPropagation();
      triggerRef.current = document.activeElement as Element | null;
      setOpen({ point: { x: event.clientX, y: event.clientY }, items });
    },
    onKeyDown: (event) => {
      if (disabled) return;
      const menuKey =
        event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey);
      if (!menuKey) return;
      if (!allowEditable && isEditableTarget(event.target)) return;
      const el = event.target instanceof Element ? event.target : null;
      if (el === null) return;
      const items = getItems({ target: el });
      if (items === null || flatten(cleanEntries(items)).length === 0) return;
      event.preventDefault();
      event.stopPropagation();
      const r = el.getBoundingClientRect();
      triggerRef.current = el;
      setOpen({ point: { x: r.left + 12, y: r.bottom - 4 }, items });
    },
    onPointerDown: (event) => {
      if (disabled || event.pointerType !== "touch") return;
      if (!allowEditable && isEditableTarget(event.target)) return;
      clearPress();
      const target = event.target instanceof Element ? event.target : null;
      const x = event.clientX;
      const y = event.clientY;
      pressRef.current = {
        x,
        y,
        target,
        timer: window.setTimeout(() => {
          pressRef.current = null;
          const items = getItemsRef.current({ target });
          if (items === null || flatten(cleanEntries(items)).length === 0)
            return;
          triggerRef.current = target;
          setOpen({ point: { x, y }, items });
        }, LONG_PRESS_MS),
      };
    },
    onPointerMove: (event) => {
      const p = pressRef.current;
      if (p === null) return;
      if (
        Math.abs(event.clientX - p.x) > LONG_PRESS_SLOP ||
        Math.abs(event.clientY - p.y) > LONG_PRESS_SLOP
      )
        clearPress();
    },
    onPointerUp: clearPress,
    onPointerCancel: clearPress,
  };

  const buttonProps: UseContextMenuResult["buttonProps"] = {
    "aria-haspopup": "menu",
    "aria-expanded": open !== null,
    onClick: (event) => {
      event.stopPropagation();
      const el = event.currentTarget;
      const r = el.getBoundingClientRect();
      openAt({ x: r.left, y: r.bottom + 4 }, el, el);
    },
  };

  const menu =
    open === null ? null : (
      <ContextMenu
        point={open.point}
        items={open.items}
        label={label}
        onClose={onClose}
      />
    );

  return {
    menu,
    triggerProps,
    buttonProps,
    openAt,
    isOpen: open !== null,
    close,
  };
}

/** The visible "..." trigger that opens the same menu as a right-click. Shows on hover/focus. */
export function MenuButton({
  label,
  buttonProps,
  className,
  children,
}: {
  readonly label: string;
  readonly buttonProps: UseContextMenuResult["buttonProps"];
  readonly className?: string;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      data-reveal=""
      className={cx("maya-ctx__trigger", className)}
      {...buttonProps}
    >
      {children}
    </button>
  );
}

/**
 * A wrapper element that carries a context menu: right-click, Menu key / Shift+F10, long-press
 * and (unless `hideButton`) a visible "..." button that appear on hover and focus.
 * `buttonPlacement="inline"` puts the button after the children inside the wrapper.
 */
export function MenuSurface({
  as: Tag = "div",
  label,
  getItems,
  hideButton = false,
  buttonLabel,
  className,
  children,
  ...rest
}: {
  readonly as?: "div" | "li" | "article" | "section";
  /** Menu name, for example "Actions for report.pdf". */
  readonly label: string;
  readonly getItems: UseContextMenuOptions["getItems"];
  readonly hideButton?: boolean;
  readonly buttonLabel?: string;
  readonly className?: string;
  readonly children: React.ReactNode;
} & Omit<
  React.HTMLAttributes<HTMLElement>,
  "children" | "className"
>): React.JSX.Element {
  const menu = useContextMenu({ getItems, label });
  const Element = Tag as React.ElementType;
  return (
    <Element
      {...rest}
      {...menu.triggerProps}
      className={cx("maya-reveal maya-ctx__host", className)}
    >
      {children}
      {!hideButton && (
        <MenuButton
          label={buttonLabel ?? `More actions for ${label}`}
          buttonProps={menu.buttonProps}
        >
          <MoreHorizontalIcon />
        </MenuButton>
      )}
      {menu.menu}
    </Element>
  );
}

function MoreHorizontalIcon(): React.JSX.Element {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="5" cy="12" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
    </svg>
  );
}
