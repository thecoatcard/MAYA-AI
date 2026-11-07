"use client";

import { useRouter } from "next/navigation";
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { signOut as authSignOut } from "../../lib/auth-client";
import {
  getAccessToken,
  refreshSession,
  setAccessToken,
  subscribeAccessToken,
} from "../../lib/auth-session";
import { getPrincipalMe, type UserPrincipal } from "../../lib/system-client";

export type { UserPrincipal };

export interface AuthContextValue {
  readonly user: UserPrincipal | null;
  readonly token: string | null;
  readonly isAuthenticated: boolean;
  readonly isLoading: boolean;
  readonly logout: (redirectTo?: string) => Promise<void>;
  readonly refresh: () => Promise<void>;
  readonly checkSession: () => Promise<boolean>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

function getServerToken(): string | null {
  return null;
}

export interface AuthProviderProps {
  readonly children: React.ReactNode;
  readonly initialUser?: UserPrincipal | null;
  readonly navigate?: (path: string) => void;
}

export function AuthProvider({
  children,
  initialUser = null,
  navigate,
}: AuthProviderProps): React.JSX.Element {
  let router: ReturnType<typeof useRouter> | null = null;
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    router = useRouter();
  } catch {
    router = null;
  }

  const token = useSyncExternalStore(
    subscribeAccessToken,
    getAccessToken,
    getServerToken,
  );

  const [user, setUser] = useState<UserPrincipal | null>(initialUser);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const lastResolvedTokenRef = useRef<string | null | undefined>(undefined);

  // Initial mount: resolve active session or attempt cookie refresh
  useEffect(() => {
    let mounted = true;

    async function initSession(): Promise<void> {
      try {
        let activeToken = getAccessToken();
        if (!activeToken) {
          const refreshed = await refreshSession();
          if (refreshed?.accessToken) {
            activeToken = refreshed.accessToken;
          }
        }

        if (!mounted) return;

        if (activeToken) {
          try {
            const principal = await getPrincipalMe();
            if (mounted) {
              setUser(principal);
              lastResolvedTokenRef.current = activeToken;
            }
          } catch {
            if (mounted) {
              setUser(null);
              lastResolvedTokenRef.current = null;
            }
          }
        } else {
          setUser(null);
          lastResolvedTokenRef.current = null;
        }
      } catch {
        if (mounted) {
          setUser(null);
          lastResolvedTokenRef.current = null;
        }
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    }

    void initSession();

    return () => {
      mounted = false;
    };
  }, []);

  // Synchronize user principal when token changes (cross-tab storage event or login/logout)
  useEffect(() => {
    if (token === null) {
      if (lastResolvedTokenRef.current !== null) {
        lastResolvedTokenRef.current = null;
        setUser(null);
      }
      return;
    }

    if (token !== lastResolvedTokenRef.current) {
      lastResolvedTokenRef.current = token;
      let mounted = true;
      void (async () => {
        try {
          const principal = await getPrincipalMe();
          if (mounted && getAccessToken() === token) {
            setUser(principal);
          }
        } catch {
          if (mounted && getAccessToken() === token) {
            setUser(null);
          }
        }
      })();

      return () => {
        mounted = false;
      };
    }
  }, [token]);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      let activeToken: string | null = null;
      const refreshed = await refreshSession();
      if (refreshed?.accessToken) {
        activeToken = refreshed.accessToken;
      } else {
        activeToken = getAccessToken();
      }

      if (activeToken) {
        const principal = await getPrincipalMe();
        setUser(principal);
        lastResolvedTokenRef.current = activeToken;
      } else {
        setUser(null);
        lastResolvedTokenRef.current = null;
      }
    } catch {
      setUser(null);
      lastResolvedTokenRef.current = null;
    }
  }, []);

  const checkSession = useCallback(async (): Promise<boolean> => {
    setIsLoading(true);
    try {
      await refresh();
      return getAccessToken() !== null && user !== null;
    } finally {
      setIsLoading(false);
    }
  }, [refresh, user]);

  const logout = useCallback(
    async (redirectTo = "/login"): Promise<void> => {
      setIsLoading(true);
      try {
        await authSignOut();
      } finally {
        setAccessToken(null);
        setUser(null);
        lastResolvedTokenRef.current = null;
        setIsLoading(false);
        if (navigate) {
          navigate(redirectTo);
        } else if (router && typeof router.push === "function") {
          router.push(redirectTo);
        }
      }
    },
    [navigate, router],
  );

  const isAuthenticated = Boolean(user && token);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      isAuthenticated,
      isLoading,
      logout,
      refresh,
      checkSession,
    }),
    [user, token, isAuthenticated, isLoading, logout, refresh, checkSession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    const currentToken =
      typeof window !== "undefined" ? getAccessToken() : null;
    return {
      user: null,
      token: currentToken,
      isAuthenticated: false,
      isLoading: false,
      logout: async () => {},
      refresh: async () => {},
      checkSession: async () => false,
    };
  }
  return context;
}
