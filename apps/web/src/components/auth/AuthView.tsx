"use client";

import { LogOut } from "lucide-react";
import Link from "next/link";
import React, { useState } from "react";
import { useAuth } from "../../hooks/useAuth";
import { Button } from "../ui/Button";
import { Skeleton } from "../ui/Feedback";
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
        className="mx-auto grid w-full max-w-md min-w-0 gap-3"
        aria-busy="true"
        aria-label="Checking authentication state"
      >
        <Skeleton className="h-6 w-2/3" />
        <Skeleton lines={3} />
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
      <div className="mx-auto grid w-full max-w-md min-w-0 gap-4">
        <h1 className="m-0 text-maya-title font-semibold">
          Active Session Found
        </h1>

        <p className="m-0 text-maya-small text-maya-secondary [overflow-wrap:anywhere]">
          You are already signed in as{" "}
          <span className="font-mono font-medium text-maya-text">
            {roleUserDetail}
          </span>
          .
        </p>

        <div className="grid gap-3">
          <Link
            href="/workspace"
            className="maya-button motion-interactive motion-press inline-flex min-h-9 w-full items-center justify-center rounded-maya-md border border-maya-accent bg-maya-accent px-4 text-maya-small font-medium text-maya-accent-text hover:border-maya-accent-hover hover:bg-maya-accent-hover pointer-coarse:min-h-11"
          >
            Continue to Workspace
          </Link>

          <Button
            variant="secondary"
            className="w-full"
            onClick={handleSignOut}
            disabled={signingOut}
            leftIcon={<LogOut size={14} aria-hidden="true" />}
          >
            {signingOut ? "Signing out..." : "Sign in with a different account"}
          </Button>
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
