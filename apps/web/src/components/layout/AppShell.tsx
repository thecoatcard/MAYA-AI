"use client";

import {
  Activity,
  Bot,
  Brain,
  FileCheck,
  FolderGit2,
  Layers3,
  ListTodo,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  PlusCircle,
  Settings,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React, { useCallback, useEffect, useState } from "react";

import { listApprovals } from "../../lib/approvals-client";
import { checkSystemHealth } from "../../lib/system-client";
import { useAuth } from "../../hooks/useAuth";
import { NewTaskModal } from "../tasks/NewTaskModal";
import {
  ThemeToggle,
  readStoredTheme,
  storeTheme,
  type ThemePreference,
} from "../chat/ThemeToggle";

export interface AppShellProps {
  readonly children: React.ReactNode;
  readonly title?: string;
  readonly breadcrumb?: string;
  readonly requireAuth?: boolean;
}

export function AppShell({
  children,
  title = "Workspace",
  breadcrumb,
  requireAuth = true,
}: AppShellProps): React.JSX.Element {
  const pathname = usePathname();
  const router = useRouter();

  const { user, isAuthenticated, isLoading, logout } = useAuth();

  const [sidebarOpen, setSidebarOpen] = useState<boolean>(true);
  const [mobileMenuOpen, setMobileMenuOpen] = useState<boolean>(false);
  const [newTaskOpen, setNewTaskOpen] = useState<boolean>(false);

  const [pendingApprovalsCount, setPendingApprovalsCount] = useState<number>(0);
  const [systemReady, setSystemReady] = useState<boolean | null>(null);
  const [theme, setTheme] = useState<ThemePreference>("system");

  // Load theme preference
  useEffect(() => {
    const stored = readStoredTheme();
    setTheme(stored);
    storeTheme(stored);
  }, []);

  const changeTheme = (next: ThemePreference) => {
    setTheme(next);
    storeTheme(next);
  };

  // Route Guard: Smooth redirect to login when unauthenticated in browser environment
  useEffect(() => {
    if (
      requireAuth &&
      !isLoading &&
      !isAuthenticated &&
      typeof window !== "undefined"
    ) {
      const redirectTarget = pathname
        ? `/login?redirect=${encodeURIComponent(pathname)}`
        : "/login";
      router.push(redirectTarget);
    }
  }, [requireAuth, isLoading, isAuthenticated, pathname, router]);

  // Real-time backend health & pending approvals polling
  const refreshSystemState = useCallback(async () => {
    try {
      const health = await checkSystemHealth();
      setSystemReady(health.status === "ready" || health.status === "alive");
    } catch {
      setSystemReady(false);
    }

    try {
      const approvals = await listApprovals("pending", 50);
      setPendingApprovalsCount(approvals.length);
    } catch {
      // Offline or unauthenticated
    }
  }, []);

  useEffect(() => {
    refreshSystemState();
    const timer = setInterval(refreshSystemState, 15000);
    return () => clearInterval(timer);
  }, [refreshSystemState]);

  const handleSignOut = async () => {
    try {
      await logout();
    } finally {
      router.push("/login");
    }
  };

  // Clean translucent loading skeleton while authenticating to prevent UI flashing
  if (requireAuth && isLoading) {
    return (
      <div
        className="flex h-screen w-screen overflow-hidden bg-zinc-950 text-zinc-100 font-sans"
        aria-busy="true"
        aria-label="Loading workspace"
        data-testid="appshell-loading"
      >
        <aside className="w-64 border-r border-white/10 bg-zinc-950/80 backdrop-blur-xl flex flex-col p-3.5 gap-4 shadow-2xl">
          <div className="flex items-center gap-2.5 h-10">
            <div className="w-8 h-8 rounded-lg bg-teal-500/10 border border-teal-500/20 animate-pulse" />
            <div className="flex flex-col gap-1.5 flex-1">
              <div className="h-3 w-20 rounded bg-white/10 animate-pulse" />
              <div className="h-2 w-14 rounded bg-white/5 animate-pulse" />
            </div>
          </div>
          <div className="h-8 rounded-lg bg-teal-500/10 border border-teal-500/20 animate-pulse" />
          <div className="flex-1 flex flex-col gap-2 py-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <div
                key={i}
                className="h-8 rounded-lg bg-white/[0.03] border border-white/5 animate-pulse"
                style={{ animationDelay: `${i * 80}ms` }}
              />
            ))}
          </div>
          <div className="p-3 rounded-xl bg-white/[0.03] border border-white/5 flex items-center gap-2.5 animate-pulse">
            <div className="w-8 h-8 rounded-full bg-white/10" />
            <div className="flex flex-col gap-1.5 flex-1">
              <div className="h-3 w-16 bg-white/10 rounded" />
              <div className="h-2 w-24 bg-white/5 rounded" />
            </div>
          </div>
        </aside>
        <div className="flex-1 flex flex-col min-w-0 bg-zinc-950">
          <header className="h-14 px-4 border-b border-white/10 bg-zinc-950/80 backdrop-blur-xl flex items-center justify-between shadow-md">
            <div className="h-4 w-36 bg-white/10 rounded animate-pulse" />
            <div className="flex items-center gap-3">
              <div className="h-6 w-32 bg-white/5 rounded-full border border-white/10 animate-pulse" />
              <div className="h-7 w-7 rounded-lg bg-white/5 animate-pulse" />
            </div>
          </header>
          <main className="flex-1 p-6 flex flex-col gap-4">
            <div className="h-6 w-48 bg-white/5 rounded animate-pulse" />
            <div className="flex-1 rounded-2xl bg-white/[0.02] border border-white/5 animate-pulse" />
          </main>
        </div>
      </div>
    );
  }

  // Smooth redirecting indicator when unauthenticated in browser environment
  if (requireAuth && !isAuthenticated && typeof window !== "undefined") {
    return (
      <div
        className="flex h-screen w-screen items-center justify-center bg-zinc-950 text-zinc-100 font-sans"
        data-testid="appshell-redirecting"
        aria-busy="true"
        aria-label="Redirecting to login"
      >
        <div className="flex flex-col items-center gap-3 p-6 rounded-2xl bg-zinc-900/80 backdrop-blur-xl border border-white/10 shadow-2xl">
          <div className="w-10 h-10 rounded-xl bg-teal-500/10 border border-teal-500/30 flex items-center justify-center text-teal-400">
            <Layers3 className="w-5 h-5 animate-spin" />
          </div>
          <span className="text-xs font-mono text-zinc-400">
            Redirecting to sign-in…
          </span>
        </div>
      </div>
    );
  }

  const navItems = [
    {
      label: "Workspace",
      href: "/workspace",
      icon: <Sparkles className="w-4 h-4" />,
      active: pathname === "/workspace" || pathname === "/chat",
    },
    {
      label: "Tasks",
      href: "/tasks",
      icon: <ListTodo className="w-4 h-4" />,
      active: pathname.startsWith("/tasks"),
    },
    {
      label: "Approvals",
      href: "/approvals",
      icon: <ShieldAlert className="w-4 h-4" />,
      badge: pendingApprovalsCount > 0 ? pendingApprovalsCount : undefined,
      badgeColor: "bg-amber-500/20 text-amber-300 border-amber-500/30",
      active: pathname.startsWith("/approvals"),
    },
    {
      label: "Deliverables",
      href: "/deliverables",
      icon: <FileCheck className="w-4 h-4" />,
      active: pathname.startsWith("/deliverables"),
    },
    {
      label: "Agents",
      href: "/agents",
      icon: <Bot className="w-4 h-4" />,
      active: pathname.startsWith("/agents"),
    },
    {
      label: "Integrations",
      href: "/integrations",
      icon: <FolderGit2 className="w-4 h-4" />,
      active: pathname.startsWith("/integrations"),
    },
    {
      label: "Memory & Knowledge",
      href: "/memory",
      icon: <Brain className="w-4 h-4" />,
      active: pathname.startsWith("/memory"),
    },
    {
      label: "Activity & Audit",
      href: "/activity",
      icon: <Activity className="w-4 h-4" />,
      active: pathname.startsWith("/activity"),
    },
    {
      label: "Settings",
      href: "/settings",
      icon: <Settings className="w-4 h-4" />,
      active: pathname.startsWith("/settings"),
    },
  ];

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-zinc-950 text-zinc-100 font-sans">
      {/* Mobile Drawer Backdrop */}
      {mobileMenuOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/70 backdrop-blur-sm lg:hidden"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}

      {/* Glassmorphic Sidebar Navigation */}
      <aside
        className={`fixed lg:static top-0 bottom-0 left-0 z-50 flex flex-col border-r border-white/10 bg-zinc-950/80 backdrop-blur-xl shadow-2xl transition-all duration-200 ${
          sidebarOpen ? "w-64" : "w-16"
        } ${mobileMenuOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"}`}
      >
        {/* Workspace Brand / Selector */}
        <div className="flex items-center justify-between h-14 px-3.5 border-b border-white/10">
          <Link
            href="/workspace"
            className="flex items-center gap-2.5 font-bold tracking-tight text-sm text-zinc-100 hover:text-teal-400 transition-colors p-1.5 rounded-lg hover:bg-white/[0.04]"
          >
            <div className="p-1.5 rounded-lg bg-teal-500/10 border border-teal-500/30 text-teal-400 shadow-sm">
              <Layers3 className="w-4 h-4" />
            </div>
            {sidebarOpen && (
              <div className="flex flex-col">
                <span className="leading-tight">MAYA AI</span>
                <span className="text-[10px] font-mono text-zinc-500">
                  Digital Employee
                </span>
              </div>
            )}
          </Link>

          <button
            type="button"
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="hidden lg:flex p-1.5 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-white/[0.06] transition-colors"
            title={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
          >
            {sidebarOpen ? (
              <PanelLeftClose className="w-4 h-4" />
            ) : (
              <PanelLeftOpen className="w-4 h-4" />
            )}
          </button>
        </div>

        {/* Quick Task Action */}
        <div className="p-3 border-b border-white/10">
          <button
            type="button"
            onClick={() => setNewTaskOpen(true)}
            className={`w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg bg-teal-600 hover:bg-teal-500 text-zinc-950 font-semibold text-xs shadow-md shadow-teal-950/30 border border-teal-400/30 transition-all ${
              sidebarOpen ? "" : "px-0"
            }`}
            title="Assign New Autonomous Task"
          >
            <PlusCircle className="w-4 h-4 shrink-0 text-zinc-950" />
            {sidebarOpen && <span>New Task</span>}
          </button>
        </div>

        {/* Navigation Items List */}
        <nav
          className="flex-1 overflow-y-auto px-2 py-3 flex flex-col gap-1"
          aria-label="Main Navigation"
        >
          {navItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              onClick={() => setMobileMenuOpen(false)}
              className={`flex items-center gap-3 px-3 py-2 rounded-lg text-xs font-medium transition-all ${
                item.active
                  ? "bg-white/[0.08] backdrop-blur-md text-teal-400 border border-teal-500/30 shadow-md shadow-teal-950/30"
                  : "text-zinc-400 hover:text-zinc-100 hover:bg-white/[0.04] border border-transparent hover:border-white/5"
              } ${sidebarOpen ? "" : "justify-center px-0"}`}
              title={sidebarOpen ? undefined : item.label}
            >
              <span className="shrink-0">{item.icon}</span>
              {sidebarOpen && (
                <div className="flex-1 flex items-center justify-between">
                  <span>{item.label}</span>
                  {item.badge !== undefined && (
                    <span
                      className={`text-[10px] font-mono px-1.5 py-0.2 rounded-full border ${item.badgeColor}`}
                    >
                      {item.badge}
                    </span>
                  )}
                </div>
              )}
            </Link>
          ))}
        </nav>

        {/* User Profile Card & Footer Actions */}
        <div className="p-3 border-t border-white/10 bg-zinc-900/60 backdrop-blur-xl flex flex-col gap-2">
          {sidebarOpen ? (
            <div
              className="flex flex-col gap-2.5 p-2.5 rounded-xl bg-zinc-950/70 border border-white/10 shadow-inner"
              data-testid="user-profile-card"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2.5 min-w-0">
                  <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-teal-500/20 to-teal-400/30 border border-teal-500/40 flex items-center justify-center text-teal-300 text-xs font-bold shrink-0 shadow-sm">
                    {(
                      user?.role?.[0] ??
                      user?.user_id?.[0] ??
                      "U"
                    ).toUpperCase()}
                  </div>
                  <div className="flex flex-col min-w-0">
                    <span
                      className="text-xs font-semibold text-zinc-200 truncate"
                      title={user?.user_id ?? "Authenticated User"}
                    >
                      {user?.user_id ?? "Authenticated User"}
                    </span>
                    <span className="text-[10px] font-mono text-teal-400 font-medium uppercase tracking-wider">
                      {user?.role ?? "user"}
                    </span>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={handleSignOut}
                  className="p-1.5 rounded-lg text-zinc-400 hover:text-red-400 hover:bg-red-500/10 border border-transparent hover:border-red-500/20 transition-all shrink-0"
                  title="Sign out"
                  aria-label="Sign out"
                >
                  <LogOut className="w-4 h-4" />
                </button>
              </div>
              <div className="pt-2 border-t border-white/5 flex items-center justify-between text-[10px] font-mono">
                <span className="text-zinc-500 font-sans">Workspace ID</span>
                <span
                  className="px-1.5 py-0.5 rounded bg-zinc-900/90 border border-white/10 text-zinc-300 font-mono truncate max-w-[130px]"
                  title={user?.workspace_id ?? "default"}
                >
                  {user?.workspace_id ?? "default"}
                </span>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={handleSignOut}
              className="w-full flex justify-center py-2 rounded-lg text-zinc-400 hover:text-red-400 hover:bg-red-500/10 transition-colors"
              title="Sign out"
              aria-label="Sign out"
            >
              <LogOut className="w-4 h-4" />
            </button>
          )}
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden bg-zinc-950">
        {/* Glassmorphic Top Header */}
        <header className="h-14 px-4 sm:px-6 border-b border-white/10 bg-zinc-950/80 backdrop-blur-xl shadow-md flex items-center justify-between z-10 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <button
              type="button"
              onClick={() => setMobileMenuOpen(true)}
              className="lg:hidden p-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-white/[0.06] transition-colors shrink-0"
            >
              <Menu className="w-5 h-5" />
            </button>

            <div className="min-w-0">
              <div className="flex items-center gap-2 truncate">
                {breadcrumb && (
                  <>
                    <span className="text-xs text-zinc-500 shrink-0">
                      {breadcrumb}
                    </span>
                    <span className="text-zinc-600 shrink-0">/</span>
                  </>
                )}
                <h1 className="text-sm font-semibold text-zinc-100 truncate">
                  {title}
                </h1>
              </div>
            </div>
          </div>

          {/* Header Status & Controls */}
          <div className="flex items-center gap-3">
            {/* User Profile Pill in Header */}
            {user && (
              <div
                className="hidden sm:flex items-center gap-2 px-2.5 py-1 rounded-full bg-zinc-900/60 border border-white/10 text-xs font-mono text-zinc-300 shadow-sm backdrop-blur-md"
                data-testid="header-user-profile"
                title={`User: ${user.user_id} (${user.role}) | Workspace: ${user.workspace_id}`}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-teal-400 shadow-[0_0_8px_rgba(45,212,191,0.6)]" />
                <span className="text-[11px] text-zinc-400 font-sans">
                  {user.role}:
                </span>
                <span className="text-[11px] text-teal-300 font-semibold">
                  {user.workspace_id}
                </span>
              </div>
            )}

            {/* Real-time System Connection Status Pill */}
            <div
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono border backdrop-blur-md transition-all shadow-sm ${
                systemReady === true
                  ? "bg-emerald-950/60 border-emerald-500/30 text-emerald-400 shadow-emerald-950/20"
                  : systemReady === false
                    ? "bg-red-950/60 border-red-500/30 text-red-400 shadow-red-950/20"
                    : "bg-zinc-900/60 border-white/10 text-zinc-400"
              }`}
              title="FastAPI Backend Health"
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  systemReady === true
                    ? "bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)]"
                    : systemReady === false
                      ? "bg-red-400 animate-pulse shadow-[0_0_6px_rgba(248,113,113,0.8)]"
                      : "bg-zinc-500"
                }`}
              />
              <span>
                {systemReady === true
                  ? "Backend Connected"
                  : systemReady === false
                    ? "Backend Disconnected"
                    : "Connecting…"}
              </span>
            </div>

            {/* Pending Approvals quick badge */}
            {pendingApprovalsCount > 0 && (
              <Link
                href="/approvals"
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-500/15 border border-amber-500/30 text-amber-300 text-[11px] font-mono hover:bg-amber-500/25 transition-all shadow-sm backdrop-blur-md"
                title={`${pendingApprovalsCount} actions waiting for human approval`}
              >
                <ShieldAlert className="w-3.5 h-3.5" />
                <span>{pendingApprovalsCount} Approvals</span>
              </Link>
            )}

            <ThemeToggle value={theme} onChange={changeTheme} />
          </div>
        </header>

        {/* Page Content Viewport */}
        <main className="flex-1 flex flex-col min-h-0 overflow-hidden bg-zinc-950">
          {children}
        </main>
      </div>

      {/* Global New Task Modal */}
      <NewTaskModal
        isOpen={newTaskOpen}
        onClose={() => setNewTaskOpen(false)}
        onTaskCreated={(task) => {
          router.push(`/tasks?task_id=${task.task_id}`);
        }}
      />
    </div>
  );
}
