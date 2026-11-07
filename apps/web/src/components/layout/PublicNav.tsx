"use client";

import {
  ChevronDown,
  Layers3,
  LayoutDashboard,
  ListTodo,
  LogOut,
  Menu,
  Settings,
  ShieldAlert,
  Sparkles,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React, { useEffect, useRef, useState } from "react";
import { useAuth } from "../../hooks/useAuth";
import {
  readStoredTheme,
  storeTheme,
  ThemeToggle,
  type ThemePreference,
} from "../chat/ThemeToggle";

export function PublicNav(): React.JSX.Element {
  let pathname = "/";
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    pathname = usePathname() ?? "/";
  } catch {
    // Outside App Router context (e.g. unit tests)
  }

  let router: { push: (url: string) => void } | null = null;
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    router = useRouter();
  } catch {
    // Outside App Router context
  }

  const { user, isAuthenticated, isLoading, logout } = useAuth();

  const [theme, setTheme] = useState<ThemePreference>("system");
  const [mobileMenuOpen, setMobileMenuOpen] = useState<boolean>(false);
  const [userDropdownOpen, setUserDropdownOpen] = useState<boolean>(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const stored = readStoredTheme();
    setTheme(stored);
    storeTheme(stored);
  }, []);

  const changeTheme = (next: ThemePreference) => {
    setTheme(next);
    storeTheme(next);
  };

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setUserDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleSignOut = async () => {
    setUserDropdownOpen(false);
    await logout();
    if (router) {
      router.push("/");
    } else if (typeof window !== "undefined") {
      window.location.href = "/";
    }
  };

  const navLinks = [
    { label: "Features", href: "/features" },
    { label: "Architecture", href: "/architecture" },
    { label: "Pricing", href: "/pricing" },
    { label: "Docs", href: "/docs" },
    { label: "Contact", href: "/contact" },
  ];

  return (
    <header className="sticky top-0 z-40 w-full border-b border-white/5 bg-zinc-950/80 backdrop-blur-xl">
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        {/* Brand */}
        <div className="flex items-center gap-8">
          <Link
            href="/"
            className="flex items-center gap-2.5 font-bold tracking-tight text-white hover:text-teal-400 transition-colors"
            aria-label="MAYA AI home"
          >
            <div className="p-1.5 rounded-lg bg-teal-500/10 border border-teal-500/30 text-teal-400">
              <Layers3 className="w-4 h-4" />
            </div>
            <span className="text-base font-extrabold tracking-tight">
              MAYA AI
            </span>
          </Link>

          {/* Desktop Nav Links */}
          <nav
            className="hidden md:flex items-center gap-6"
            aria-label="Main Navigation"
          >
            {navLinks.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className={`text-xs font-medium transition-colors ${
                  pathname === link.href
                    ? "text-teal-400"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {link.label}
              </Link>
            ))}
          </nav>
        </div>

        {/* Right Section: Auth State + Theme Toggle */}
        <div className="hidden sm:flex items-center gap-4">
          <ThemeToggle value={theme} onChange={changeTheme} />

          {isLoading ? (
            /* Anti-Flicker Skeleton */
            <div className="flex items-center gap-2">
              <div className="w-16 h-8 rounded-lg bg-zinc-800/60 animate-pulse" />
              <div className="w-24 h-8 rounded-lg bg-zinc-800/60 animate-pulse" />
            </div>
          ) : isAuthenticated ? (
            /* Logged-In Experience: No Login/Create Account buttons */
            <div className="flex items-center gap-3">
              <Link
                href="/workspace"
                className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-xl bg-teal-500/10 border border-teal-500/30 hover:bg-teal-500/20 text-teal-300 font-semibold text-xs transition-all shadow-xs"
              >
                <Sparkles className="w-3.5 h-3.5 text-teal-400" />
                <span>Workspace</span>
              </Link>

              {/* User Dropdown */}
              <div className="relative" ref={dropdownRef}>
                <button
                  type="button"
                  onClick={() => setUserDropdownOpen(!userDropdownOpen)}
                  className="flex items-center gap-2 px-2.5 py-1.5 rounded-xl border border-white/10 bg-zinc-900/60 hover:bg-zinc-800/60 text-xs text-zinc-200 transition-colors"
                  aria-expanded={userDropdownOpen}
                >
                  <div className="w-5 h-5 rounded-full bg-teal-500/20 border border-teal-500/40 flex items-center justify-center text-teal-400 text-[10px] font-bold">
                    {user?.role ? user.role[0].toUpperCase() : "U"}
                  </div>
                  <span className="font-mono text-[11px] text-zinc-300 max-w-[100px] truncate">
                    {user?.role ?? "Active"}
                  </span>
                  <ChevronDown className="w-3.5 h-3.5 text-zinc-500" />
                </button>

                {userDropdownOpen && (
                  <div className="absolute right-0 mt-2 w-48 rounded-xl border border-white/10 bg-zinc-900/90 backdrop-blur-2xl p-1.5 shadow-2xl z-50 flex flex-col gap-0.5">
                    <Link
                      href="/workspace"
                      onClick={() => setUserDropdownOpen(false)}
                      className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs text-zinc-300 hover:text-white hover:bg-zinc-800/60 transition-colors"
                    >
                      <LayoutDashboard className="w-4 h-4 text-teal-400" />
                      <span>Console Dashboard</span>
                    </Link>
                    <Link
                      href="/tasks"
                      onClick={() => setUserDropdownOpen(false)}
                      className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs text-zinc-300 hover:text-white hover:bg-zinc-800/60 transition-colors"
                    >
                      <ListTodo className="w-4 h-4 text-cyan-400" />
                      <span>Task Center</span>
                    </Link>
                    <Link
                      href="/approvals"
                      onClick={() => setUserDropdownOpen(false)}
                      className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs text-zinc-300 hover:text-white hover:bg-zinc-800/60 transition-colors"
                    >
                      <ShieldAlert className="w-4 h-4 text-amber-400" />
                      <span>Approvals</span>
                    </Link>
                    <Link
                      href="/settings"
                      onClick={() => setUserDropdownOpen(false)}
                      className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs text-zinc-300 hover:text-white hover:bg-zinc-800/60 transition-colors"
                    >
                      <Settings className="w-4 h-4 text-zinc-400" />
                      <span>Settings</span>
                    </Link>
                    <div className="my-1 border-t border-white/5" />
                    <button
                      type="button"
                      onClick={handleSignOut}
                      className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs text-rose-400 hover:bg-rose-950/30 transition-colors text-left"
                    >
                      <LogOut className="w-4 h-4" />
                      <span>Sign Out</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          ) : (
            /* Logged-Out Experience */
            <div className="flex items-center gap-3">
              <Link
                href="/login"
                className="px-3 py-1.5 text-xs font-semibold text-zinc-300 hover:text-white transition-colors"
              >
                Sign in
              </Link>
              <Link
                href="/register"
                className="inline-flex items-center justify-center px-4 py-1.5 rounded-xl bg-teal-500 hover:bg-teal-400 text-zinc-950 font-bold text-xs shadow-md shadow-teal-500/20 transition-all hover:scale-105"
              >
                Create account
              </Link>
            </div>
          )}
        </div>

        {/* Mobile Hamburger Button */}
        <div className="flex sm:hidden items-center gap-2">
          <ThemeToggle value={theme} onChange={changeTheme} />
          <button
            type="button"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="p-2 rounded-lg border border-white/10 bg-zinc-900/60 text-zinc-300"
            aria-label="Toggle Navigation Menu"
          >
            {mobileMenuOpen ? (
              <X className="w-5 h-5" />
            ) : (
              <Menu className="w-5 h-5" />
            )}
          </button>
        </div>
      </div>

      {/* Mobile Drawer */}
      {mobileMenuOpen && (
        <div className="sm:hidden border-t border-white/5 bg-zinc-950/95 backdrop-blur-2xl px-6 py-6 flex flex-col gap-4">
          <nav className="flex flex-col gap-3">
            {navLinks.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                onClick={() => setMobileMenuOpen(false)}
                className="text-sm font-medium text-zinc-300 hover:text-teal-400 transition-colors py-1"
              >
                {link.label}
              </Link>
            ))}
          </nav>

          <div className="border-t border-white/5 pt-4 flex flex-col gap-3">
            {isAuthenticated ? (
              <>
                <Link
                  href="/workspace"
                  onClick={() => setMobileMenuOpen(false)}
                  className="w-full text-center py-2.5 rounded-xl bg-teal-500 text-zinc-950 font-bold text-xs"
                >
                  Enter Workspace
                </Link>
                <button
                  type="button"
                  onClick={handleSignOut}
                  className="w-full text-center py-2.5 rounded-xl border border-white/10 bg-white/5 text-rose-400 text-xs font-semibold"
                >
                  Sign Out
                </button>
              </>
            ) : (
              <>
                <Link
                  href="/login"
                  onClick={() => setMobileMenuOpen(false)}
                  className="w-full text-center py-2.5 rounded-xl border border-white/10 bg-white/5 text-white font-semibold text-xs"
                >
                  Sign in
                </Link>
                <Link
                  href="/register"
                  onClick={() => setMobileMenuOpen(false)}
                  className="w-full text-center py-2.5 rounded-xl bg-teal-500 text-zinc-950 font-bold text-xs"
                >
                  Create account
                </Link>
              </>
            )}
          </div>
        </div>
      )}
    </header>
  );
}
