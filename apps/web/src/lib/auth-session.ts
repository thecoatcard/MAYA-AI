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
const listeners = new Set<Listener>();

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

async function refreshFromCookie(): Promise<RefreshedSession | null> {
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
    throw new AuthSessionError(0);
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
    throw new AuthSessionError(response.status);
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
  if (refreshInFlight === null) {
    refreshInFlight = refreshFromCookie().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

/** Subscribe to token changes (shape matches `useSyncExternalStore`). */
export function subscribeAccessToken(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
