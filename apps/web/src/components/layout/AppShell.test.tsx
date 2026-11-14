import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "../../hooks/useAuth";
import { checkBackendReady } from "../../lib/api-client";
import { listApprovals } from "../../lib/approvals-client";
import {
  AppShell,
  NAV_ITEMS,
  isActiveRoute,
  pendingLabel,
  userLabel,
} from "./AppShell";
import { makeAuth } from "./layout-test-utils";
import {
  EMPTY_VOICE,
  WorkspaceProvider,
  useRegisterWorkspaceExtensions,
  useWorkspace,
} from "./WorkspaceProvider";

const mockPush = vi.fn();
let mockPathname = "/tasks";

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ push: mockPush, replace: vi.fn() }),
}));
vi.mock("../../hooks/useAuth", () => ({ useAuth: vi.fn() }));
vi.mock("../../lib/api-client", () => ({ checkBackendReady: vi.fn() }));
vi.mock("../../lib/approvals-client", () => ({ listApprovals: vi.fn() }));

function renderShell(): ReturnType<typeof render> {
  return render(
    <WorkspaceProvider>
      <AppShell>
        <p>page body</p>
      </AppShell>
    </WorkspaceProvider>,
  );
}

let auth = makeAuth();

beforeEach(() => {
  mockPathname = "/tasks";
  auth = makeAuth();
  vi.mocked(useAuth).mockReturnValue(auth);
  vi.mocked(checkBackendReady).mockResolvedValue({ ready: true });
  vi.mocked(listApprovals).mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
  delete document.documentElement.dataset.theme;
  window.localStorage.removeItem("maya.theme");
});

describe("AppShell", () => {
  it("has one main landmark, a skip link, a title bar and a status bar", async () => {
    renderShell();
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(
      screen.getByRole("link", { name: "Skip to main content" }),
    ).toHaveAttribute("href", "#maya-main");
    expect(screen.getByTestId("title-bar")).toHaveClass("maya-drag");
    expect(screen.getByText("page body")).toBeInTheDocument();
    expect(await screen.findByText("Backend ready")).toBeInTheDocument();
    // A person, not a UUID: no email is known here, so it says "Signed in" and the role.
    const who = screen.getByTestId("status-user");
    expect(who).toHaveTextContent("Signed in");
    expect(who).toHaveTextContent("admin");
    expect(who.textContent).not.toContain("usr-alice");
  });

  it("shows the email in the status bar when the backend provides one", () => {
    auth = makeAuth({
      user: {
        user_id: "0b9f6c1e-1111-2222-3333-444455556666",
        workspace_id: "ws-1",
        role: "admin",
        email: "alice@example.test",
      },
    });
    vi.mocked(useAuth).mockReturnValue(auth);
    renderShell();
    const who = screen.getByTestId("status-user");
    expect(who).toHaveTextContent("alice@example.test");
    expect(who.textContent).not.toContain("0b9f6c1e");
    expect(userLabel(auth.user!)).toBe("alice@example.test");
  });

  it("caps the pending badge at 50+", async () => {
    vi.mocked(listApprovals).mockResolvedValue(
      Array.from({ length: 50 }, () => ({}) as never),
    );
    renderShell();
    const badge = await screen.findByTestId("pending-count");
    expect(badge).toHaveTextContent("50+");
    expect(badge).toHaveAttribute(
      "aria-label",
      "50 or more waiting for your approval",
    );
    expect(pendingLabel(3).aria).toBe("3 waiting for your approval");
  });

  it("keeps <html data-paused> in step with document visibility", () => {
    renderShell();
    expect(document.documentElement).not.toHaveAttribute("data-paused");
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(document.documentElement).toHaveAttribute("data-paused");
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => false,
    });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(document.documentElement).not.toHaveAttribute("data-paused");
  });

  it("renders the toast region and shows a pushed toast", async () => {
    function Pusher(): React.JSX.Element {
      const { notify } = useWorkspace();
      return (
        <button
          type="button"
          onClick={() => notify({ tone: "info", message: "Saved" })}
        >
          push
        </button>
      );
    }
    render(
      <WorkspaceProvider>
        <AppShell>
          <Pusher />
        </AppShell>
      </WorkspaceProvider>,
    );
    expect(screen.getByTestId("toast-region")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "push" }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("lists every secondary view in the sidebar nav", () => {
    renderShell();
    const nav = screen.getByRole("navigation", { name: "Main" });
    const labels = Array.from(nav.querySelectorAll("a")).map(
      (a) => a.textContent,
    );
    expect(labels).toEqual([
      "Tasks",
      "Artifacts",
      "Memory",
      "Connections",
      "Activity",
      "Settings",
    ]);
    expect(NAV_ITEMS.map((i) => i.href)).toEqual([
      "/tasks",
      "/artifacts",
      "/memory",
      "/connections",
      "/activity",
      "/settings",
    ]);
  });

  it("marks only the active route with aria-current", () => {
    mockPathname = "/memory";
    renderShell();
    expect(screen.getByRole("link", { name: "Memory" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Tasks" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(isActiveRoute("/tasks/abc", "/tasks")).toBe(true);
    expect(isActiveRoute("/tasksX", "/tasks")).toBe(false);
  });

  it("shows the pending approvals count only when above zero", async () => {
    vi.mocked(listApprovals).mockResolvedValue([{} as never, {} as never]);
    renderShell();
    const badge = await screen.findByTestId("pending-count");
    expect(badge).toHaveTextContent("2");
    expect(badge).toHaveAttribute("aria-label", "2 waiting for your approval");
    expect(badge).toHaveClass("text-maya-caption");
    // Approvals are decided on the task cards, so the count sits on the Tasks entry.
    expect(badge.closest("a")).toHaveAttribute("href", "/tasks");
  });

  it("has no Approvals entry in the navigation", () => {
    renderShell();
    expect(
      screen.queryByRole("link", { name: /Approvals/ }),
    ).not.toBeInTheDocument();
  });

  it("shows no count badge when nothing is pending", async () => {
    renderShell();
    await screen.findByText("Backend ready");
    expect(screen.queryByTestId("pending-count")).not.toBeInTheDocument();
  });

  it("reports an unreachable backend in the status bar", async () => {
    vi.mocked(checkBackendReady).mockResolvedValue({
      ready: false,
      reason: "unreachable",
    });
    renderShell();
    expect(await screen.findByText("Backend unreachable")).toBeInTheDocument();
  });

  it("New conversation navigates to / when elsewhere", () => {
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: /New conversation/ }));
    expect(mockPush).toHaveBeenCalledWith("/");
  });

  it("offers empty conversation and voice slots and has no side panel", () => {
    renderShell();
    expect(screen.getByTestId("conversation-slot")).toBeEmptyDOMElement();
    expect(screen.queryByTestId("voice-slot")).not.toBeInTheDocument();
    expect(screen.queryByTestId("right-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("work-panel-toggle")).not.toBeInTheDocument();
  });

  it("switches the whole document between dark and light with one button", async () => {
    renderShell();
    expect(document.documentElement.dataset.theme).toBe("dark");
    fireEvent.click(
      screen.getByRole("button", { name: "Switch to light theme" }),
    );
    expect(document.documentElement.dataset.theme).toBe("light");
    fireEvent.click(
      screen.getByRole("button", { name: "Switch to dark theme" }),
    );
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("signs out from the status bar", async () => {
    renderShell();
    const signOutBtn = screen.getByRole("button", { name: "Sign out" });
    expect(signOutBtn).toHaveClass("pointer-coarse:min-h-11");
    fireEvent.click(signOutBtn);
    await waitFor(() => expect(auth.logout).toHaveBeenCalled());
  });
});

/** Drives the shell's width mode through matchMedia. */
function setWidth(px: number, reducedMotion = false): void {
  window.matchMedia = ((query: string) => {
    if (query.includes("prefers-reduced-motion")) {
      return {
        matches: reducedMotion,
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        onchange: null,
        dispatchEvent: () => false,
      };
    }
    const min = /min-width:\s*(\d+)px/.exec(query);
    return {
      matches: min !== null && px >= Number(min[1]),
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      onchange: null,
      dispatchEvent: () => false,
    };
  }) as unknown as typeof window.matchMedia;
}

function Register(): null {
  const ext = React.useMemo(
    () => ({
      newConversation: null,
      sidebarList: <p>history</p>,
      title: null,
    }),
    [],
  );
  useRegisterWorkspaceExtensions(ext, EMPTY_VOICE);
  return null;
}

describe("AppShell responsive modes", () => {
  afterEach(() => {
    Reflect.deleteProperty(window, "matchMedia");
  });

  function renderWithPanel(width: number): void {
    setWidth(width);
    render(
      <WorkspaceProvider>
        <Register />
        <AppShell>
          <button type="button">page action</button>
        </AppShell>
      </WorkspaceProvider>,
    );
  }

  it("1440 and 1100: no Work panel and no Work button", async () => {
    for (const width of [1440, 1100]) {
      cleanup();
      renderWithPanel(width);
      expect(await screen.findByText("page action")).toBeInTheDocument();
      expect(screen.queryByTestId("right-panel")).not.toBeInTheDocument();
      expect(screen.queryByTestId("work-panel-toggle")).not.toBeInTheDocument();
    }
  });

  it("900: navigation is an icon rail and conversations open in a drawer with a scrim", async () => {
    renderWithPanel(900);
    const rail = await screen.findByTestId("nav-rail");
    expect(rail).toBeInTheDocument();
    expect(
      within(rail).getByRole("link", { name: "Tasks" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Approvals" })).toBeNull();
    const sidebar = document.getElementById("maya-sidebar")!;
    expect(sidebar).toHaveAttribute("data-state", "closed");
    fireEvent.click(screen.getByRole("button", { name: "Conversations" }));
    expect(sidebar).toHaveAttribute("data-state", "open");
    // Drawer is offset next to the rail so the rail is not covered and navigation remains reachable
    expect(sidebar).toHaveStyle({ left: "var(--maya-nav-width)" });
    expect(
      within(rail).getByRole("link", { name: "Tasks" }),
    ).toBeInTheDocument();
    expect(
      within(rail).getByRole("button", { name: "Conversations" }),
    ).toBeInTheDocument();
    // Nav scrim dims the whole viewport
    const scrim = screen.getByTestId("nav-scrim");
    expect(scrim).toHaveClass("fixed");
    expect(scrim).toHaveClass("inset-0");
    fireEvent.click(scrim);
    expect(sidebar).toHaveAttribute("data-state", "closed");
  });

  it("500: the menu button opens a drawer with the full navigation; Escape closes and returns focus", async () => {
    renderWithPanel(500);
    const menu = await screen.findByRole("button", {
      name: "Open navigation",
    });
    menu.focus();
    fireEvent.click(menu);
    const sidebar = document.getElementById("maya-sidebar")!;
    expect(sidebar).toHaveAttribute("data-state", "open");
    expect(
      screen.getByRole("navigation", { name: "Main" }),
    ).toBeInTheDocument();
    expect(document.getElementById("maya-main")).toHaveAttribute("inert");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(sidebar).toHaveAttribute("data-state", "closed");
    expect(document.activeElement).toBe(menu);
  });
});

describe("AppShell folding the docked sidebar", () => {
  afterEach(() => {
    Reflect.deleteProperty(window, "matchMedia");
    window.localStorage.removeItem("maya.sidebar.collapsed");
  });

  function renderAt(width: number): void {
    setWidth(width);
    render(
      <WorkspaceProvider>
        <Register />
        <AppShell>
          <textarea aria-label="draft" />
        </AppShell>
      </WorkspaceProvider>,
    );
  }

  it("the hamburger hides and shows the sidebar and remembers the choice", async () => {
    renderAt(1280);
    const toggle = await screen.findByTestId("sidebar-toggle");
    const sidebar = document.getElementById("maya-sidebar")!;
    expect(toggle).toHaveAttribute("aria-label", "Hide sidebar");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(sidebar).toHaveAttribute("data-state", "docked");

    fireEvent.click(toggle);
    expect(sidebar).toHaveAttribute("data-state", "collapsed");
    expect(sidebar).toHaveAttribute("inert");
    // The fold now animates (transform and opacity, ui-surfaces.css): a collapsed sidebar is no
    // longer display:none, it is inert and marked collapsed so the stylesheet slides it out.
    expect(sidebar).toHaveClass("maya-sidebar");
    expect(sidebar).toHaveAttribute("inert");
    expect(toggle).toHaveAttribute("aria-label", "Show sidebar");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(window.localStorage.getItem("maya.sidebar.collapsed")).toBe("1");
    // Behaviour changed deliberately: a folded sidebar no longer vanishes, it leaves a visible
    // icon rail. Starting a conversation stays one click away, from the rail (not the title bar).
    const rail = screen.getByTestId("nav-rail");
    expect(rail).toHaveAttribute("data-variant", "folded");
    expect(
      within(rail).getByRole("button", { name: "New conversation" }),
    ).toBeTruthy();
    expect(
      within(screen.getByTestId("title-bar")).queryByRole("button", {
        name: "New conversation",
      }),
    ).toBeNull();

    fireEvent.click(toggle);
    expect(sidebar).toHaveAttribute("data-state", "docked");
    expect(sidebar).not.toHaveAttribute("inert");
    expect(window.localStorage.getItem("maya.sidebar.collapsed")).toBe("0");
  });

  it("the folded rail shows every destination as a named icon link, marks the active one, and unfolds", async () => {
    window.localStorage.setItem("maya.sidebar.collapsed", "1");
    renderAt(1280);
    const rail = await screen.findByTestId("nav-rail");
    for (const label of [
      "Tasks",
      "Artifacts",
      "Memory",
      "Connections",
      "Activity",
      "Settings",
    ]) {
      const link = within(rail).getByRole("link", { name: label });
      expect(link).toHaveAttribute("title", label);
      expect(link).not.toHaveAttribute("inert");
    }
    expect(
      within(rail).queryByRole("button", { name: "Conversations" }),
    ).toBeNull();
    // The full sidebar stays inert while folded; the rail is not inside it.
    expect(document.getElementById("maya-sidebar")).toHaveAttribute("inert");
    expect(document.getElementById("maya-sidebar")!.contains(rail)).toBe(false);
    fireEvent.click(screen.getByTestId("sidebar-toggle"));
    expect(screen.queryByTestId("nav-rail")).toBeNull();
    expect(document.getElementById("maya-sidebar")).toHaveAttribute(
      "data-state",
      "docked",
    );
  });

  it("starts folded when the user folded it last time", async () => {
    window.localStorage.setItem("maya.sidebar.collapsed", "1");
    renderAt(1280);
    await screen.findByTestId("sidebar-toggle");
    await waitFor(() =>
      expect(document.getElementById("maya-sidebar")).toHaveAttribute(
        "data-state",
        "collapsed",
      ),
    );
  });

  it("Ctrl+B folds it, except while typing in a text field", async () => {
    renderAt(1280);
    await screen.findByTestId("sidebar-toggle");
    const sidebar = document.getElementById("maya-sidebar")!;

    fireEvent.keyDown(screen.getByLabelText("draft"), {
      key: "b",
      ctrlKey: true,
    });
    expect(sidebar).toHaveAttribute("data-state", "docked");

    fireEvent.keyDown(document.body, { key: "b", ctrlKey: true });
    expect(sidebar).toHaveAttribute("data-state", "collapsed");
    fireEvent.keyDown(document.body, { key: "B", metaKey: true });
    expect(sidebar).toHaveAttribute("data-state", "docked");
    // Other combinations do nothing.
    fireEvent.keyDown(document.body, { key: "b" });
    fireEvent.keyDown(document.body, {
      key: "b",
      ctrlKey: true,
      shiftKey: true,
    });
    expect(sidebar).toHaveAttribute("data-state", "docked");
  });

  it("ignores a keydown that has no key (browser autofill) instead of throwing", async () => {
    renderAt(1280);
    await screen.findByTestId("sidebar-toggle");
    const sidebar = document.getElementById("maya-sidebar")!;
    const event = new Event("keydown", { bubbles: true });
    // Autofill and some extensions dispatch a plain Event: no `key`, no modifier flags.
    expect(() => document.body.dispatchEvent(event)).not.toThrow();
    expect(sidebar).toHaveAttribute("data-state", "docked");
  });

  it("works when storage is blocked", async () => {
    const getItem = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new Error("blocked");
      });
    const setItem = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new Error("blocked");
      });
    try {
      renderAt(1280);
      const toggle = await screen.findByTestId("sidebar-toggle");
      fireEvent.click(toggle);
      expect(document.getElementById("maya-sidebar")).toHaveAttribute(
        "data-state",
        "collapsed",
      );
    } finally {
      getItem.mockRestore();
      setItem.mockRestore();
    }
  });

  it("has no docked toggle on a narrow or mobile window, which already have their own menu", async () => {
    renderAt(900);
    await screen.findByTestId("nav-rail");
    expect(screen.queryByTestId("sidebar-toggle")).toBeNull();
    cleanup();
    renderAt(500);
    await screen.findByRole("button", { name: "Open navigation" });
    expect(screen.queryByTestId("sidebar-toggle")).toBeNull();
  });

  it("uses text-maya-label on count badges in rail/compact navigation", async () => {
    window.localStorage.setItem("maya.sidebar.collapsed", "1");
    vi.mocked(listApprovals).mockResolvedValue([{} as never, {} as never]);
    renderAt(1280);
    const rail = await screen.findByTestId("nav-rail");
    const badge = within(rail).getByTestId("pending-count");
    expect(badge).toHaveClass("text-maya-label");
  });

  it("clears fold immediately under prefers-reduced-motion so data-fold is never stuck", async () => {
    setWidth(1280, true);
    render(
      <WorkspaceProvider>
        <Register />
        <AppShell>
          <p>content</p>
        </AppShell>
      </WorkspaceProvider>,
    );
    const toggle = await screen.findByTestId("sidebar-toggle");
    const main = screen.getByRole("main");
    const container = main.parentElement!;
    expect(container).not.toHaveAttribute("data-fold");
    fireEvent.click(toggle);
    // Under reduced motion, fold is cleared immediately
    expect(container).not.toHaveAttribute("data-fold");
  });

  it("sets data-fold when folding under standard motion and clears on animationend", async () => {
    setWidth(1280, false);
    render(
      <WorkspaceProvider>
        <Register />
        <AppShell>
          <p>content</p>
        </AppShell>
      </WorkspaceProvider>,
    );
    const toggle = await screen.findByTestId("sidebar-toggle");
    const main = screen.getByRole("main");
    const container = main.parentElement!;
    expect(container).not.toHaveAttribute("data-fold");
    fireEvent.click(toggle);
    expect(container).toHaveAttribute("data-fold", "shut");
    fireEvent(main, new Event("webkitAnimationEnd", { bubbles: true }));
    expect(container).not.toHaveAttribute("data-fold");
  });
});
