/**
 * In-memory holder for the access token. The rotating refresh token is kept
 * only by the server in an HttpOnly cookie and is never read by JavaScript.
 */

type Listener = () => void;

export interface RefreshedSession {
  readonly accessToken: string;
  readonly expiresIn: number;
}

export class AuthSessionError extends Error {
  readonly status: number;

  constructor(status: number) {
    super("The authentication session could not be refreshed.");
    this.name = "AuthSessionError";
    this.status = status;
  }
}

let accessToken: string | null = null;
let accessExpiresAt = 0;
let tokenVersion = 0;
let identityVersion = 0;
let refreshInFlight: Promise<RefreshedSession | null> | null = null;
let identityChangeSequence = 0;
const listeners = new Set<Listener>();
const IDENTITY_CHANGE_STORAGE_KEY = "maya-auth-identity-change";
const AUTH_COOKIE_LOCK_NAME = "maya-auth-refresh-cookie";

function announceIdentityChange(): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    // This marker contains no credential. Storage events reach other tabs but
    // are not delivered back to the tab that wrote the value.
    identityChangeSequence += 1;
    window.localStorage.setItem(
      IDENTITY_CHANGE_STORAGE_KEY,
      `${Date.now()}-${identityChangeSequence}-${Math.random()}`,
    );
  } catch {
    // Storage can be disabled. Same-tab identity/version checks still apply.
  }
}

function clearAccessTokenForPeerChange(): void {
  if (accessToken === null) {
    return;
  }
  accessToken = null;
  accessExpiresAt = 0;
  tokenVersion += 1;
  identityVersion += 1;
  notifyTokenChange();
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event: StorageEvent) => {
    if (event.key === IDENTITY_CHANGE_STORAGE_KEY && event.newValue !== null) {
      clearAccessTokenForPeerChange();
    }
  });
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function getAccessTokenExpiresIn(): number | null {
  if (accessExpiresAt === 0) {
    return null;
  }
  return Math.max(0, Math.ceil((accessExpiresAt - Date.now()) / 1000));
}

/**
 * A number that increases each time the token changes. Compare it, never the
 * token, to ask "is this still the same sign-in?".
 */
export function getAccessTokenVersion(): number {
  return tokenVersion;
}

/** A number that changes for sign-in/out changes, but not a same-session refresh. */
export function getAuthIdentityVersion(): number {
  return identityVersion;
}

/**
 * Serialize requests whose responses can change the auth refresh cookie.
 * Web Locks coordinate same-origin tabs; unsupported browsers fall back to
 * the existing per-tab behavior.
 */
export function withAuthCookieLock<T>(operation: () => Promise<T>): Promise<T> {
  if (typeof navigator !== "undefined" && "locks" in navigator) {
    return Promise.resolve(
      navigator.locks.request(AUTH_COOKIE_LOCK_NAME, operation),
    );
  }
  return operation();
}

function notifyTokenChange(): void {
  for (const listener of Array.from(listeners)) {
    listener();
  }
}

export function setAccessToken(token: string | null, expiresIn?: number): void {
  const next = token === null || token === "" ? null : token;
  if (next !== null && expiresIn !== undefined) {
    accessExpiresAt = Date.now() + expiresIn * 1000;
  } else if (next === null) {
    accessExpiresAt = 0;
  }
  if (next === accessToken) {
    return;
  }
  accessToken = next;
  tokenVersion += 1;
  identityVersion += 1;
  announceIdentityChange();
  notifyTokenChange();
}

function setRefreshedAccessToken(token: string, expiresIn: number): void {
  accessExpiresAt = Date.now() + expiresIn * 1000;
  if (token === accessToken) {
    return;
  }
  accessToken = token;
  tokenVersion += 1;
  notifyTokenChange();
}

async function refreshFromCookie(
  identityAtStart: number,
): Promise<RefreshedSession | null> {
  let response: Response;
  try {
    response = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "X-Maya-Refresh-Transport": "cookie",
      },
    });
  } catch {
    if (identityVersion !== identityAtStart) {
      return null;
    }
    throw new AuthSessionError(0);
  }
  if (identityVersion !== identityAtStart) {
    return null;
  }
  if (response.status === 401) {
    setAccessToken(null);
    return null;
  }
  if (!response.ok) {
    throw new AuthSessionError(response.status);
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    if (identityVersion !== identityAtStart) {
      return null;
    }
    throw new AuthSessionError(response.status);
  }
  if (identityVersion !== identityAtStart) {
    return null;
  }
  if (
    typeof body !== "object" ||
    body === null ||
    !("access_token" in body) ||
    typeof body.access_token !== "string" ||
    body.access_token === "" ||
    !("expires_in" in body) ||
    typeof body.expires_in !== "number" ||
    body.expires_in <= 0 ||
    !("token_type" in body) ||
    body.token_type !== "bearer"
  ) {
    throw new AuthSessionError(response.status);
  }
  const session = {
    accessToken: body.access_token,
    expiresIn: body.expires_in,
  };
  setRefreshedAccessToken(session.accessToken, session.expiresIn);
  return session;
}

/** Rotate the refresh cookie once even when several requests need it together. */
export function refreshSession(): Promise<RefreshedSession | null> {
  if (refreshInFlight !== null) {
    return refreshInFlight;
  }
  const request = withAuthCookieLock(() => refreshFromCookie(identityVersion));
  const inFlight = Promise.resolve(request).finally(() => {
    refreshInFlight = null;
  });
  refreshInFlight = inFlight;
  return inFlight;
}

/**
 * Ensure an access token is available and fresh. If the current token is missing
 * or near expiry (less than minRemainingSeconds remaining), trigger a session
 * refresh using the existing Web Locks refresh flow.
 */
export async function ensureFreshAccessToken(
  minRemainingSeconds = 60,
): Promise<string | null> {
  const currentToken = getAccessToken();
  const remaining = getAccessTokenExpiresIn();
  if (
    currentToken !== null &&
    remaining !== null &&
    remaining > minRemainingSeconds
  ) {
    return currentToken;
  }
  const refreshed = await refreshSession();
  return refreshed?.accessToken ?? getAccessToken();
}

/** Subscribe to token changes (shape matches `useSyncExternalStore`). */
export function subscribeAccessToken(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
