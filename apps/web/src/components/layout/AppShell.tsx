"use client";

import {
  Activity,
  Brain,
  CircleAlert,
  CircleCheck,
  FileCheck,
  ListTodo,
  LoaderCircle,
  LogOut,
  Menu,
  MessageSquare,
  Mic,
  MicOff,
  Moon,
  MoreHorizontal,
  PanelLeft,
  Sun,
  PlugZap,
  Plus,
  RefreshCw,
  Settings,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React, {
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import type { UserPrincipal } from "../../lib/system-client";
import { IconButton } from "../ui/Button";
import { ICON_STROKE } from "../ui/Icon";
import { ThemeToggle } from "../ui/ThemeToggle";
import {
  MenuButton,
  closeAllContextMenus,
  useContextMenu,
  type ContextMenuEntry,
} from "../ui/ContextMenu";
import { ConversationContext } from "../chat/ConversationContext";
import { TOGGLE_SIDEBAR_EVENT } from "../chat/ConversationSidebar";
import { useTheme } from "../../lib/theme";
import { useOverlay, useShellMode, type ShellMode } from "./useOverlay";
import { useWorkspace } from "./WorkspaceProvider";

export interface NavItem {
  readonly label: string;
  readonly href: string;
  readonly icon: React.ReactNode;
}

const ICON = {
  size: 16,
  strokeWidth: ICON_STROKE,
  "aria-hidden": true,
} as const;

export const NAV_ITEMS: readonly NavItem[] = [
  { label: "Tasks", href: "/tasks", icon: <ListTodo {...ICON} /> },
  { label: "Artifacts", href: "/artifacts", icon: <FileCheck {...ICON} /> },
  { label: "Memory", href: "/memory", icon: <Brain {...ICON} /> },
  { label: "Connections", href: "/connections", icon: <PlugZap {...ICON} /> },
  { label: "Activity", href: "/activity", icon: <Activity {...ICON} /> },
  { label: "Settings", href: "/settings", icon: <Settings {...ICON} /> },
];

export function isActiveRoute(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export interface AppShellProps {
  readonly children: React.ReactNode;
  /** @deprecated Ignored. Kept so components/chat/AmbientWorkspace still compiles until A4 removes it. */
  readonly title?: string;
  /** @deprecated Ignored. */
  readonly breadcrumb?: string;
  /** @deprecated Ignored. Access is decided by AppGate. */
  readonly requireAuth?: boolean;
}

const BACKEND_LABEL = {
  checking: "Checking backend",
  ready: "Backend ready",
  unreachable: "Backend unreachable",
} as const;

/** The approvals query is capped at 50, so 50 means "at least 50". */
const APPROVALS_CAP = 50;

export function pendingLabel(count: number): {
  readonly text: string;
  readonly aria: string;
} {
  return count >= APPROVALS_CAP
    ? {
        text: `${APPROVALS_CAP}+`,
        aria: `${APPROVALS_CAP} or more waiting for your approval`,
      }
    : { text: String(count), aria: `${count} waiting for your approval` };
}

/** Who is signed in, for people: email, then name. Never the raw id (that stays in the tooltip). */
export function userLabel(user: UserPrincipal): string {
  const email = user.email?.trim();
  if (email !== undefined && email !== "") return email;
  const name = user.display_name?.trim();
  if (name !== undefined && name !== "") return name;
  return "Signed in";
}

type DesktopControls = "left" | "right" | null;

function readDesktopControls(): DesktopControls {
  const maya = (
    window as unknown as {
      maya?: { isDesktop?: boolean; platform?: string };
    }
  ).maya;
  if (maya?.isDesktop !== true) return null;
  return maya.platform === "darwin" ? "left" : "right";
}

/** localStorage key that remembers a folded sidebar. */
const SIDEBAR_KEY = "maya.sidebar.collapsed";

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

const FOCUS_BTN =
  "motion-interactive motion-press inline-flex items-center rounded-maya-sm text-maya-caption";

/** Which way the sidebar last folded: drives the one-off slide of the content gutter. */
type Fold = "open" | "shut" | null;

/**
 * The persistent desktop-style shell: title bar, navigation, one main region and a status bar.
 * Mounted once by app/(app)/layout.tsx so it stays mounted across navigation. There is no side
 * panel: task progress, approvals and files appear in the chat thread.
 *
 * Width modes (useShellMode):
 *  - full and compact (>= 1024 px): sidebar and content;
 *  - narrow (760-1023): navigation is an icon rail, conversations open in a drawer;
 *  - mobile (< 760): navigation and conversations are one drawer behind the menu button.
 * Drawers have a scrim, Escape, a focus trap and return focus to their button.
 */
export function AppShell({ children }: AppShellProps): React.JSX.Element {
  const pathname = usePathname() ?? "/";
  const router = useRouter();
  const {
    auth,
    backend,
    pendingApprovals,
    unseenTaskUpdates,
    clearTaskUpdates,
    conversation,
    voice,
    signOut,
  } = useWorkspace();
  const mode: ShellMode = useShellMode();

  const [navOpen, setNavOpen] = useState<boolean>(false);
  const [controls, setControls] = useState<DesktopControls>(null);
  const [retrying, setRetrying] = useState<boolean>(false);
  const sidebarRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    setControls(readDesktopControls());
  }, []);

  useEffect(() => {
    setNavOpen(false);
    closeAllContextMenus(); // a menu never survives a route change
  }, [pathname]);

  const navIsOverlay = mode === "narrow" || mode === "mobile";

  useEffect(() => {
    if (!navIsOverlay) setNavOpen(false);
  }, [navIsOverlay]);

  const navOverlayOpen = navIsOverlay && navOpen;
  useOverlay(navOverlayOpen, () => setNavOpen(false), sidebarRef);

  // The docked sidebar can be folded away with the hamburger button (or Ctrl/Cmd+B). The choice is
  // remembered; storage may be blocked, in which case it simply lasts for this window.
  const [collapsed, setCollapsed] = useState<boolean>(false);
  const [fold, setFold] = useState<Fold>(null);
  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem(SIDEBAR_KEY) === "1");
    } catch {
      /* storage unavailable: keep the default */
    }
  }, []);
  const toggleSidebar = useCallback((): void => {
    setCollapsed((value) => {
      const next = !value;
      if (prefersReducedMotion()) {
        setFold(null);
      } else {
        setFold(next ? "shut" : "open");
      }
      try {
        window.localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0");
      } catch {
        /* storage unavailable: the choice lasts for this window */
      }
      return next;
    });
  }, []);
  useEffect(() => {
    if (
      typeof window === "undefined" ||
      typeof window.matchMedia !== "function"
    ) {
      return undefined;
    }
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (media.matches) setFold(null);
    const onChange = (event: MediaQueryListEvent): void => {
      if (event.matches) setFold(null);
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  useEffect(() => {
    if (navIsOverlay) return undefined;
    const onKeyDown = (event: KeyboardEvent): void => {
      // Browser autofill and some extensions dispatch keydown events with no `key`.
      if (
        typeof event.key !== "string" ||
        event.key.toLowerCase() !== "b" ||
        !(event.ctrlKey || event.metaKey) ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          target instanceof HTMLInputElement ||
          target instanceof HTMLTextAreaElement)
      ) {
        return; // never take a shortcut away from a text field
      }
      event.preventDefault();
      toggleSidebar();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navIsOverlay, toggleSidebar]);
  useEffect(() => {
    // The sidebar's own menu ("Collapse sidebar") asks the shell to fold it.
    const onToggle = (): void => toggleSidebar();
    window.addEventListener(TOGGLE_SIDEBAR_EVENT, onToggle);
    return () => window.removeEventListener(TOGGLE_SIDEBAR_EVENT, onToggle);
  }, [toggleSidebar]);
  const sidebarHidden = !navIsOverlay && collapsed;
  const runtimeCtx = useContext(ConversationContext);
  const { theme, setTheme } = useTheme();
  const titleMenu = useContextMenu({
    label: "Top bar actions",
    getItems: () => {
      const items: ContextMenuEntry[] = [];
      if (runtimeCtx !== null) {
        const running = runtimeCtx.voiceLive;
        items.push({
          id: "voice",
          label: running ? "Stop voice" : "Start voice",
          icon: running ? <MicOff size={14} /> : <Mic size={14} />,
          onSelect: () =>
            running
              ? (runtimeCtx.stopVoice ?? runtimeCtx.toggleVoice)()
              : (runtimeCtx.toggleMasterVoice ?? runtimeCtx.toggleVoice)(),
        });
        if (running) {
          const muted = runtimeCtx.voice.muted ?? false;
          items.push({
            id: "mute",
            label: "Mute microphone",
            checked: muted,
            disabled: runtimeCtx.voice.toggleMute === undefined,
            disabledReason: "Voice is still connecting.",
            onSelect: () => runtimeCtx.voice.toggleMute?.(),
          });
        }
        items.push({ kind: "separator" });
      }
      items.push(
        {
          id: "theme-light",
          label: "Light theme",
          icon: <Sun size={14} />,
          checked: theme === "light",
          onSelect: () => setTheme("light"),
        },
        {
          id: "theme-dark",
          label: "Dark theme",
          icon: <Moon size={14} />,
          checked: theme === "dark",
          onSelect: () => setTheme("dark"),
        },
        { kind: "separator" },
        {
          id: "sidebar",
          label: sidebarHidden ? "Show sidebar" : "Hide sidebar",
          icon: <PanelLeft size={14} />,
          shortcut: "Ctrl+B",
          onSelect: toggleSidebar,
        },
      );
      return items;
    },
  });
  const railMenu = useContextMenu({
    label: "Navigation actions",
    getItems: () => [
      {
        id: "new",
        label: "New conversation",
        icon: <Plus size={14} />,
        onSelect: () => newConversationRef.current(),
      },
      {
        id: "sidebar",
        label: sidebarHidden ? "Show sidebar" : "Hide sidebar",
        icon: <PanelLeft size={14} />,
        onSelect: toggleSidebar,
      },
    ],
  });
  // One rail component: the narrow-window rail, and the folded docked sidebar (icons only).
  const showRail = mode === "narrow" || sidebarHidden;

  const newConversation = (): void => {
    if (conversation.newConversation !== null) {
      conversation.newConversation();
    }
    setNavOpen(false);
    if (pathname !== "/") router.push("/");
  };
  const newConversationRef = useRef(newConversation);
  newConversationRef.current = newConversation;

  const activeLabel =
    NAV_ITEMS.find((item) => isActiveRoute(pathname, item.href))?.label ??
    "Conversation";
  const viewTitle =
    pathname === "/" ? (conversation.title ?? "Conversation") : activeLabel;

  const user = auth.user;
  const pending = pendingApprovals > 0 ? pendingLabel(pendingApprovals) : null;

  // Opening Tasks is how the user sees what finished while voice was off.
  const onTasksRoute = isActiveRoute(pathname, "/tasks");
  useEffect(() => {
    if (onTasksRoute) clearTaskUpdates();
  }, [onTasksRoute, unseenTaskUpdates, clearTaskUpdates]);

  const retryBackend = (): void => {
    setRetrying(true);
    void backend.recheck().finally(() => setRetrying(false));
  };

  const navLinks = (
    compact: boolean,
    items: readonly NavItem[] = NAV_ITEMS,
    label = "Main",
  ): React.JSX.Element => (
    <nav
      aria-label={label}
      className={
        compact
          ? "flex flex-col items-center gap-0.5 p-2"
          : "flex flex-col gap-0.5 border-t border-maya-border p-2"
      }
    >
      {items.map((item) => {
        const active = isActiveRoute(pathname, item.href);
        // Approvals are decided on the task cards, so Tasks carries the count of what waits for
        // the user; the unseen task updates (voice off) show when nothing waits.
        const count =
          item.href !== "/tasks"
            ? null
            : pending !== null
              ? pending
              : unseenTaskUpdates > 0
                ? {
                    text: String(unseenTaskUpdates),
                    aria: `${unseenTaskUpdates} new task updates`,
                  }
                : null;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            aria-label={compact ? item.label : undefined}
            title={compact ? item.label : undefined}
            className={`maya-row motion-press relative flex min-h-8 min-w-0 items-center text-maya-small pointer-coarse:min-h-11 ${
              compact
                ? "size-9 justify-center pointer-coarse:size-11"
                : "gap-2.5 px-2.5"
            } ${
              active
                ? "font-medium text-maya-text"
                : "text-maya-secondary hover:text-maya-text"
            }`}
          >
            <span className="shrink-0 flex items-center justify-center maya-nudge-icon">
              {item.icon}
            </span>
            {!compact && (
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
            )}
            {count !== null && (
              <span
                className={
                  compact
                    ? "maya-count absolute -right-0.5 -top-0.5 rounded-full border border-maya-warning bg-maya-warning-soft px-1 font-mono text-maya-label text-maya-warning"
                    : "maya-count shrink-0 rounded-full border border-maya-warning bg-maya-warning-soft px-1.5 font-mono text-maya-caption text-maya-warning"
                }
                aria-label={count.aria}
                data-testid={
                  pending !== null ? "pending-count" : "task-updates-count"
                }
              >
                {count.text}
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );

  const overlayOpen = navOverlayOpen;

  return (
    <div className="flex h-dvh min-w-0 flex-col overflow-hidden bg-maya-bg text-maya-text">
      <a href="#maya-main" className="maya-skip-link">
        Skip to main content
      </a>

      <header
        className="maya-drag maya-titlebar maya-vib-bar maya-bar z-maya-sticky flex shrink-0 items-center gap-2 border-b border-maya-border"
        style={{ minHeight: "var(--maya-titlebar-height)" }}
        data-testid="title-bar"
        data-desktop-controls={controls ?? undefined}
        {...titleMenu.triggerProps}
      >
        {mode === "mobile" && (
          <button
            type="button"
            className="maya-no-drag motion-interactive motion-press inline-flex size-8 shrink-0 items-center justify-center rounded-maya-md text-maya-secondary hover:bg-maya-hover hover:text-maya-text active:bg-maya-pressed pointer-coarse:size-11"
            aria-label="Open navigation"
            aria-expanded={navOpen}
            aria-controls="maya-sidebar"
            onClick={() => setNavOpen((open) => !open)}
          >
            <Menu size={16} aria-hidden="true" />
          </button>
        )}
        {!navIsOverlay && (
          <div className="maya-no-drag flex shrink-0 items-center gap-1">
            <button
              type="button"
              className="motion-interactive motion-press inline-flex size-8 shrink-0 items-center justify-center rounded-maya-md text-maya-secondary hover:bg-maya-hover hover:text-maya-text active:bg-maya-pressed pointer-coarse:size-11"
              aria-label={sidebarHidden ? "Show sidebar" : "Hide sidebar"}
              title={`${sidebarHidden ? "Show" : "Hide"} sidebar (Ctrl+B)`}
              aria-expanded={!sidebarHidden}
              aria-controls="maya-sidebar"
              data-testid="sidebar-toggle"
              onClick={toggleSidebar}
            >
              <Menu size={16} aria-hidden="true" />
            </button>
          </div>
        )}
        <span className="shrink-0 text-maya-small font-semibold tracking-tight max-[479px]:hidden">
          MAYA
        </span>
        <span
          className="min-w-0 flex-1 truncate text-maya-small text-maya-muted"
          data-testid="view-title"
          title={viewTitle}
          dir="auto"
        >
          {viewTitle}
        </span>
        <div className="maya-no-drag flex shrink-0 items-center gap-2">
          {voice.chip !== null && (
            <div className="min-w-0" data-testid="voice-slot">
              {voice.chip}
            </div>
          )}
          <ThemeToggle />
          <MenuButton
            label="More top bar actions"
            buttonProps={titleMenu.buttonProps}
          >
            <MoreHorizontal size={16} aria-hidden="true" />
          </MenuButton>
        </div>
        {titleMenu.menu}
      </header>

      <div
        className="relative flex min-h-0 min-w-0 flex-1"
        data-fold={fold ?? undefined}
      >
        {navOverlayOpen && (
          <div
            aria-hidden="true"
            data-testid="nav-scrim"
            className="maya-vib-scrim maya-sheet-scrim fixed inset-0 z-maya-nav"
            onClick={() => setNavOpen(false)}
          />
        )}

        {showRail && (
          <div
            className={`maya-vib-side relative z-maya-nav flex w-[var(--maya-nav-width)] shrink-0 flex-col items-center gap-1 border-r border-maya-border pt-2 ${
              sidebarHidden ? "maya-rail-in" : ""
            }`}
            data-testid="nav-rail"
            data-variant={sidebarHidden ? "folded" : "narrow"}
            {...railMenu.triggerProps}
          >
            {mode === "narrow" && (
              <IconButton
                label="Conversations"
                icon={<MessageSquare size={16} />}
                aria-expanded={navOpen}
                aria-controls="maya-sidebar"
                onClick={() => setNavOpen((open) => !open)}
              />
            )}
            <IconButton
              label="New conversation"
              icon={<Plus size={16} />}
              variant="primary"
              onClick={newConversation}
            />
            <div className="min-h-0 flex-1 overflow-y-auto">
              {navLinks(
                true,
                NAV_ITEMS.filter((item) => item.href !== "/settings"),
              )}
            </div>
            {navLinks(
              true,
              NAV_ITEMS.filter((item) => item.href === "/settings"),
              "Settings",
            )}
            {railMenu.menu}
          </div>
        )}

        <aside
          id="maya-sidebar"
          ref={sidebarRef}
          aria-label="Conversations and navigation"
          {...(navOverlayOpen
            ? { role: "dialog", "aria-modal": true as const }
            : {})}
          data-state={
            navIsOverlay
              ? navOpen
                ? "open"
                : "closed"
              : sidebarHidden
                ? "collapsed"
                : "docked"
          }
          inert={sidebarHidden}
          className={`${
            navIsOverlay
              ? navOpen
                ? `motion-drawer-in absolute inset-y-0 ${
                    mode === "narrow"
                      ? "left-[var(--maya-nav-width)]"
                      : "left-0"
                  } z-maya-nav flex shadow-maya-3`
                : "hidden"
              : "flex shrink-0"
          } maya-sidebar maya-vib-side min-h-0 min-w-0 flex-col border-r border-maya-border`}
          style={{
            width: navIsOverlay
              ? mode === "narrow"
                ? "min(var(--maya-sidebar-width), calc(100% - var(--maya-nav-width) - var(--maya-space-10)))"
                : "min(var(--maya-sidebar-width), calc(100% - var(--maya-space-10)))"
              : "var(--maya-sidebar-width)",
            left:
              navIsOverlay && mode === "narrow"
                ? "var(--maya-nav-width)"
                : undefined,
          }}
        >
          <div className="flex items-center gap-2 p-3">
            <button
              type="button"
              onClick={newConversation}
              className="motion-interactive motion-press motion-lift inline-flex min-h-9 min-w-0 flex-1 items-center justify-center gap-2 rounded-maya-md border border-maya-accent bg-maya-accent text-maya-small font-medium text-maya-accent-text hover:bg-maya-accent-hover hover:shadow-maya-lift pointer-coarse:min-h-11"
            >
              <Plus size={16} aria-hidden="true" className="shrink-0" />
              <span className="truncate">New conversation</span>
            </button>
            {navIsOverlay && (
              <IconButton
                label="Close navigation"
                icon={<X size={16} />}
                onClick={() => setNavOpen(false)}
              />
            )}
          </div>

          <div
            className="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden px-2"
            role="region"
            aria-label="Conversations"
            data-testid="conversation-slot"
          >
            {conversation.sidebarList}
          </div>

          {mode !== "narrow" && navLinks(false)}
        </aside>

        <main
          id="maya-main"
          tabIndex={-1}
          inert={overlayOpen}
          className="maya-gutter flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden focus:outline-none"
          onAnimationEnd={(event) => {
            if (event.target === event.currentTarget) setFold(null);
          }}
        >
          {/* Route change: a short fade and 4 px rise (motion-page), never blocking input. */}
          <div
            key={pathname}
            className="motion-page flex min-h-0 min-w-0 flex-1 flex-col"
          >
            {children}
          </div>
        </main>
      </div>

      <footer
        className="maya-vib-bar maya-bar-bottom z-maya-sticky flex min-w-0 shrink-0 items-center gap-3 border-t border-maya-border px-3 text-maya-caption text-maya-muted"
        style={{ minHeight: "var(--maya-statusbar-height)" }}
        data-testid="status-bar"
      >
        <span
          role="status"
          data-testid="backend-status"
          data-state={backend.state}
          title={BACKEND_LABEL[backend.state]}
          className="inline-flex shrink-0 items-center gap-1.5"
        >
          {backend.state === "ready" ? (
            <CircleCheck
              size={14}
              aria-hidden="true"
              className="text-maya-success"
            />
          ) : backend.state === "checking" ? (
            <LoaderCircle
              size={14}
              aria-hidden="true"
              className="animate-spin motion-reduce:animate-none"
            />
          ) : (
            <CircleAlert
              size={14}
              aria-hidden="true"
              className="text-maya-danger"
            />
          )}
          <span className="max-[479px]:sr-only">
            {BACKEND_LABEL[backend.state]}
          </span>
        </span>
        {backend.state === "unreachable" && (
          <button
            type="button"
            onClick={retryBackend}
            disabled={retrying}
            className={`${FOCUS_BTN} shrink-0 gap-1 px-1.5 hover:bg-maya-hover hover:text-maya-text disabled:text-maya-disabled-text`}
          >
            <RefreshCw size={14} aria-hidden="true" />
            <span className="max-[479px]:sr-only">
              {retrying ? "Retrying" : "Retry"}
            </span>
          </button>
        )}
        {voice.statusLabel !== null && (
          <span className="min-w-0 truncate" title={voice.statusLabel}>
            {voice.statusLabel}
          </span>
        )}
        <span className="min-w-0 flex-1" />
        {user !== null && (
          <span
            className="flex min-w-0 items-baseline gap-1.5"
            data-testid="status-user"
            title={`${userLabel(user)} (${user.role}), id ${user.user_id}`}
          >
            <span
              className="min-w-0 truncate text-maya-secondary max-[479px]:max-w-[7rem]"
              dir="auto"
            >
              {userLabel(user)}
            </span>
            <span className="shrink-0 max-[639px]:sr-only">{user.role}</span>
          </span>
        )}
        <button
          type="button"
          onClick={() => {
            void signOut();
          }}
          aria-label="Sign out"
          className={`${FOCUS_BTN} min-h-6 shrink-0 gap-1 px-1.5 hover:bg-maya-hover hover:text-maya-text pointer-coarse:min-h-11`}
        >
          <LogOut size={14} aria-hidden="true" />
          <span className="max-[479px]:sr-only">Sign out</span>
        </button>
      </footer>
    </div>
  );
}
