"use client";

import { LogOut, Sparkles, UserCheck } from "lucide-react";
import Link from "next/link";
import React, { useState } from "react";
import { useAuth } from "../../hooks/useAuth";
import { UnifiedAuthForm, type AuthStep } from "./UnifiedAuthForm";

export interface AuthViewProps {
  initialStep: AuthStep;
  initialIdentifier?: string;
}

export function AuthView({
  initialStep,
  initialIdentifier,
}: AuthViewProps): React.JSX.Element {
  const { user, isAuthenticated, isLoading, logout } = useAuth();
  const [signingOut, setSigningOut] = useState<boolean>(false);

  const handleSignOut = async (): Promise<void> => {
    setSigningOut(true);
    try {
      await logout();
    } finally {
      setSigningOut(false);
    }
  };

  if (isLoading) {
    return (
      <div
        className="flex flex-col items-center justify-center py-10 gap-4 animate-pulse"
        aria-busy="true"
        aria-label="Checking authentication state"
      >
        <div className="w-12 h-12 rounded-xl bg-white/5 border border-white/10" />
        <div className="w-48 h-4 rounded-lg bg-white/5" />
        <div className="w-32 h-3 rounded-lg bg-white/5" />
      </div>
    );
  }

  if (isAuthenticated) {
    const roleOrUser = user?.role || user?.user_id || "user";
    const roleUserDetail =
      user?.role && user?.user_id
        ? `${user.role} (${user.user_id})`
        : roleOrUser;

    return (
      <div className="flex flex-col items-center text-center">
        <div className="p-3.5 rounded-2xl bg-teal-500/10 border border-teal-500/30 text-teal-400 mb-5 shadow-lg shadow-teal-500/10">
          <UserCheck className="w-8 h-8" />
        </div>

        <h1 className="text-xl font-bold tracking-tight text-white mb-2">
          Active Session Found
        </h1>

        <p className="text-sm text-zinc-300 leading-relaxed mb-6">
          You are already signed in as{" "}
          <span className="font-semibold text-teal-400 font-mono">
            {roleUserDetail}
          </span>
          .
        </p>

        <div className="w-full flex flex-col gap-3">
          <Link
            href="/workspace"
            className="w-full inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl bg-teal-500 hover:bg-teal-400 text-zinc-950 font-bold text-sm shadow-lg shadow-teal-500/25 transition-all hover:scale-[1.02] active:scale-[0.98]"
          >
            <Sparkles className="w-4 h-4" />
            <span>Continue to Workspace</span>
          </Link>

          <button
            type="button"
            onClick={handleSignOut}
            disabled={signingOut}
            className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border border-white/10 hover:border-white/20 bg-white/5 hover:bg-white/10 text-zinc-300 hover:text-white font-medium text-xs transition-colors disabled:opacity-50"
          >
            <LogOut className="w-3.5 h-3.5 text-zinc-400" />
            <span>
              {signingOut
                ? "Signing out..."
                : "Sign in with a different account"}
            </span>
          </button>
        </div>
      </div>
    );
  }

  return (
    <UnifiedAuthForm
      initialStep={initialStep}
      initialIdentifier={initialIdentifier}
      embedded
    />
  );
}
