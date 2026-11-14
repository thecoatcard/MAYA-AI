"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { useAuth, type AuthContextValue } from "../../hooks/useAuth";
import { checkBackendReady } from "../../lib/api-client";
import { listApprovals } from "../../lib/approvals-client";
import { useTaskNotifications } from "../../hooks/useTaskNotifications";
import { ToastRegion, useToasts, type ToastItem } from "../ui/Feedback";
import { usePauseWhenHidden } from "./useOverlay";

/**
 * WorkspaceProvider: shell state that must outlive navigation.
 *
 * It is mounted once, above every (app) route, so a voice session, a chat
 * stream or any other long-lived store held here is never torn down when the
 * user moves between views.
 *
 * Holds: auth (from AuthProvider), backend readiness, the pending-approvals
 * count, and two extension points that stay EMPTY until the conversation
 * runtime registers (see useRegisterWorkspaceExtensions):
 *  - `conversation`: `sidebarList` is rendered below "New conversation";
 *    `newConversation` is called by that button (which also navigates to "/");
 *    `title` is shown in the title bar.
 *  - `voice`: `chip` is rendered in the title bar; `statusLabel` is the
 *    plain-language state shown in the status bar.
 */

export type BackendState = "checking" | "ready" | "unreachable";

export interface BackendStatus {
  readonly state: BackendState;
  /** Machine reason from /readyz when not ready, otherwise undefined. */
  readonly reason?: string;
  /** Re-check now (used by the Retry button). */
  readonly recheck: () => Promise<void>;
}

export interface ConversationExtension {
  readonly newConversation: (() => void) | null;
  readonly sidebarList: React.ReactNode | null;
  readonly title: string | null;
}

export interface VoiceExtension {
  readonly chip: React.ReactNode | null;
  readonly statusLabel: string | null;
  /**
   * True while a live voice session is connected. Then MAYA speaks task updates herself, so the
   * shell does not also toast them.
   */
  readonly live?: boolean;
}

export interface WorkspaceContextValue {
  readonly auth: AuthContextValue;
  readonly backend: BackendStatus;
  /** Number of approvals waiting for a human decision; 0 until loaded. */
  readonly pendingApprovals: number;
  readonly refreshPendingApprovals: () => Promise<void>;
  /**
   * True when a signed-in session ended without the user asking for it (expired or revoked).
   * The gate uses it to say so; it clears when the user signs in again.
   */
  readonly sessionExpired: boolean;
  /** The user's own sign-out (not an expiry). Calls auth.logout. */
  readonly signOut: () => Promise<void>;
  /** Show a toast from anywhere (rendered by the provider's ToastRegion). Returns its id. */
  readonly notify: (toast: Omit<ToastItem, "id">) => string;
  readonly dismissToast: (id: string) => void;
  /** Task updates (done, failed, needs approval) that came while voice was off and are unseen. */
  readonly unseenTaskUpdates: number;
  readonly clearTaskUpdates: () => void;
  readonly conversation: ConversationExtension;
  readonly voice: VoiceExtension;
}

export const EMPTY_CONVERSATION: ConversationExtension = {
  newConversation: null,
  sidebarList: null,
  title: null,
};

export const EMPTY_VOICE: VoiceExtension = {
  chip: null,
  statusLabel: null,
};

/**
 * Registry for the two extension points. The conversation runtime is mounted
 * INSIDE this provider (so it can read auth) and registers what it wants the
 * shell to show. Until something registers, both stay EMPTY.
 */
interface ExtensionRegistry {
  readonly register: (
    conversation: ConversationExtension,
    voice: VoiceExtension,
  ) => void;
  readonly clear: () => void;
}

const ExtensionRegistryContext = createContext<ExtensionRegistry | null>(null);

/**
 * Called by the conversation runtime: fills useConversationExtension() and
 * useVoiceExtension(). Pass memoised values; they are cleared on unmount.
 */
export function useRegisterWorkspaceExtensions(
  conversation: ConversationExtension,
  voice: VoiceExtension,
): void {
  const registry = useContext(ExtensionRegistryContext);
  useEffect(() => {
    registry?.register(conversation, voice);
  }, [registry, conversation, voice]);
  useEffect(() => {
    return () => registry?.clear();
  }, [registry]);
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

/** Backend readiness and approvals are polled sparingly (milliseconds). */
export const BACKEND_POLL_MS = 30_000;
export const APPROVALS_POLL_MS = 30_000;

export interface WorkspaceProviderProps {
  readonly children: React.ReactNode;
}

export function WorkspaceProvider({
  children,
}: WorkspaceProviderProps): React.JSX.Element {
  const auth = useAuth();
  usePauseWhenHidden();
  const { toasts, push: notify, dismiss: dismissToast } = useToasts();
  const [conversation, setConversation] =
    useState<ConversationExtension>(EMPTY_CONVERSATION);
  const [voice, setVoice] = useState<VoiceExtension>(EMPTY_VOICE);
  const registry = useMemo<ExtensionRegistry>(
    () => ({
      register: (c, v) => {
        setConversation(c);
        setVoice(v);
      },
      clear: () => {
        setConversation(EMPTY_CONVERSATION);
        setVoice(EMPTY_VOICE);
      },
    }),
    [],
  );

  const [backendState, setBackendState] = useState<BackendState>("checking");
  const [reason, setReason] = useState<string | undefined>(undefined);
  const [pendingApprovals, setPendingApprovals] = useState<number>(0);
  const mounted = useRef<boolean>(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const recheck = useCallback(async (): Promise<void> => {
    const result = await checkBackendReady();
    if (!mounted.current) return;
    setBackendState(result.ready ? "ready" : "unreachable");
    setReason(result.ready ? undefined : result.reason);
  }, []);

  useEffect(() => {
    void recheck();
    const timer = setInterval(() => {
      if (typeof document === "undefined" || !document.hidden) {
        void recheck();
      }
    }, BACKEND_POLL_MS);
    return () => clearInterval(timer);
  }, [recheck]);

  const isAuthenticated = auth.isAuthenticated;

  // Tell an expired session apart from the user signing out themselves.
  const wasAuthenticated = useRef<boolean>(false);
  const userSignedOut = useRef<boolean>(false);
  const [sessionExpired, setSessionExpired] = useState<boolean>(false);
  useEffect(() => {
    if (isAuthenticated) {
      wasAuthenticated.current = true;
      userSignedOut.current = false;
      setSessionExpired(false);
    } else if (wasAuthenticated.current && !auth.isLoading) {
      wasAuthenticated.current = false;
      if (!userSignedOut.current) setSessionExpired(true);
    }
  }, [isAuthenticated, auth.isLoading]);

  const logout = auth.logout;
  const signOut = useCallback(async (): Promise<void> => {
    userSignedOut.current = true;
    await logout();
  }, [logout]);

  const refreshPendingApprovals = useCallback(async (): Promise<void> => {
    if (!isAuthenticated) {
      setPendingApprovals(0);
      return;
    }
    try {
      const pending = await listApprovals("pending", 50);
      if (mounted.current) setPendingApprovals(pending.length);
    } catch {
      // Keep the last known count; the task card reports real errors.
    }
  }, [isAuthenticated]);

  useEffect(() => {
    void refreshPendingApprovals();
    if (!isAuthenticated) return undefined;
    const timer = setInterval(() => {
      if (typeof document === "undefined" || !document.hidden) {
        void refreshPendingApprovals();
      }
    }, APPROVALS_POLL_MS);
    return () => clearInterval(timer);
  }, [isAuthenticated, refreshPendingApprovals]);

  // Without voice, a finished task or one that needs the user shows a toast and a Tasks badge.
  const { unseen: unseenTaskUpdates, clear: clearTaskUpdates } =
    useTaskNotifications({
      enabled: isAuthenticated,
      voiceLive: voice.live === true,
      notify,
    });

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      auth,
      backend: { state: backendState, reason, recheck },
      pendingApprovals,
      refreshPendingApprovals,
      sessionExpired,
      signOut,
      notify,
      dismissToast,
      unseenTaskUpdates,
      clearTaskUpdates,
      conversation,
      voice,
    }),
    [
      auth,
      backendState,
      reason,
      recheck,
      pendingApprovals,
      refreshPendingApprovals,
      sessionExpired,
      signOut,
      notify,
      dismissToast,
      unseenTaskUpdates,
      clearTaskUpdates,
      conversation,
      voice,
    ],
  );

  return (
    <ExtensionRegistryContext.Provider value={registry}>
      <WorkspaceContext.Provider value={value}>
        {children}
        <ToastRegion toasts={toasts} onDismiss={dismissToast} />
      </WorkspaceContext.Provider>
    </ExtensionRegistryContext.Provider>
  );
}

export function useWorkspace(): WorkspaceContextValue {
  const value = useContext(WorkspaceContext);
  if (value === null) {
    throw new Error("useWorkspace must be used inside WorkspaceProvider");
  }
  return value;
}

/** Like useWorkspace, but null outside the provider (for views that only refresh a counter). */
export function useOptionalWorkspace(): WorkspaceContextValue | null {
  return useContext(WorkspaceContext);
}
