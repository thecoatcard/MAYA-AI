/**
 * In-memory holder for the access token.
 *
 * The token lives only in this module's memory. It is never written to
 * localStorage, sessionStorage, cookies or IndexedDB, so a page reload drops it
 * and nothing else on the page can read it back from storage.
 *
 * There is no sign-in screen in the product yet, so nothing in the shipped app
 * calls `setAccessToken`. The chat page therefore shows an honest "not built"
 * state. A future sign-in flow is expected to call `setAccessToken` after a
 * successful login and `setAccessToken(null)` on logout or on a 401.
 *
 * Who is signed in is identified by `getAccessTokenVersion`, which goes up every
 * time the token actually changes, from nothing to a token, between two different
 * tokens, or back to nothing. It exposes nothing secret. The chat resets on every
 * change. Because the token itself is the only identity here, a future silent
 * refresh of the SAME user's token would also look like a change; when sign-in is
 * built, the holder needs a separate user or session identity so a refresh does
 * not clear the conversation.
 */

type Listener = () => void;

let accessToken: string | null = null;
let tokenVersion = 0;
const listeners = new Set<Listener>();

export function getAccessToken(): string | null {
  return accessToken;
}

/**
 * A number that increases each time the token changes. Compare it, never the
 * token, to ask "is this still the same sign-in?".
 */
export function getAccessTokenVersion(): number {
  return tokenVersion;
}

export function setAccessToken(token: string | null): void {
  const next = token === null || token === "" ? null : token;
  if (next === accessToken) {
    return;
  }
  accessToken = next;
  tokenVersion += 1;
  for (const listener of Array.from(listeners)) {
    listener();
  }
}

/** Subscribe to token changes (shape matches `useSyncExternalStore`). */
export function subscribeAccessToken(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
