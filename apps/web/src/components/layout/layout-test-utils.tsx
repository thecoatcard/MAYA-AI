import React from "react";
import { vi } from "vitest";

import type { AuthContextValue } from "../../hooks/useAuth";

export function makeAuth(
  overrides: Partial<AuthContextValue> = {},
): AuthContextValue {
  return {
    user: { user_id: "usr-alice", workspace_id: "ws-1", role: "admin" },
    token: "t",
    isAuthenticated: true,
    isLoading: false,
    logout: vi.fn().mockResolvedValue(undefined),
    refresh: vi.fn().mockResolvedValue(undefined),
    checkSession: vi.fn().mockResolvedValue(true),
    ...overrides,
  };
}

export const noop = (): React.JSX.Element => <></>;
