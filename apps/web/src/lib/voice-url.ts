/**
 * Voice WebSocket URL resolver (Protocol v2).
 *
 * Always same-origin: ws:// or wss:// from window.location + /api/v1/voice/live.
 * No port guessing or external host overrides.
 */

export function getVoiceWebSocketUrl(sessionId?: string | null): string {
  if (typeof window === "undefined") {
    const query = sessionId
      ? `?session_id=${encodeURIComponent(sessionId)}`
      : "";
    return `ws://127.0.0.1:8000/api/v1/voice/live${query}`;
  }
  const isHttps = window.location.protocol === "https:";
  const protocol = isHttps ? "wss:" : "ws:";
  const host = window.location.host;
  const base = `${protocol}//${host}/api/v1/voice/live`;
  if (sessionId) {
    return `${base}?session_id=${encodeURIComponent(sessionId)}`;
  }
  return base;
}
