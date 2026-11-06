const DEFAULT_API_PROXY_TARGET = "http://127.0.0.1:8000";
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

export function resolveApiProxyTarget(configured?: string): string {
  const value = configured?.trim() || DEFAULT_API_PROXY_TARGET;
  let target: URL;
  try {
    target = new URL(value);
  } catch {
    throw new Error(
      "MAYA_API_PROXY_TARGET must be a valid HTTPS origin or a loopback HTTP origin.",
    );
  }
  const host = target.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (
    target.username !== "" ||
    target.password !== "" ||
    target.pathname !== "/" ||
    target.search !== "" ||
    target.hash !== "" ||
    (target.protocol !== "https:" &&
      !(target.protocol === "http:" && LOOPBACK_HOSTS.has(host)))
  ) {
    throw new Error(
      "MAYA_API_PROXY_TARGET must be a valid HTTPS origin or a loopback HTTP origin.",
    );
  }
  return target.origin;
}

const DEFAULT_REPLY_DEADLINE_SECONDS = 120;
const MAX_REPLY_DEADLINE_SECONDS = 900;
const PROXY_GRACE_SECONDS = 30;

/**
 * How long the rewrite proxy waits on a silent upstream, in milliseconds.
 *
 * Next.js aborts a proxied request after 30 s without data, which would cut a
 * chat stream off long before the API's own reply deadline. The proxy must wait
 * longer than that deadline so the API, not the proxy, ends a silent reply and
 * the client sees `reply_deadline_exceeded`. It reads the same variable as the
 * API (`MAYA_CHAT_REPLY_DEADLINE_SECONDS`), with the same bounds.
 */
export function resolveProxyTimeoutMs(configuredDeadline?: string): number {
  const raw = configuredDeadline?.trim();
  let deadline = DEFAULT_REPLY_DEADLINE_SECONDS;
  if (raw) {
    if (!/^\d+$/.test(raw)) {
      throw new Error(
        "MAYA_CHAT_REPLY_DEADLINE_SECONDS must be a whole number of seconds.",
      );
    }
    deadline = Number(raw);
    if (deadline < 1 || deadline > MAX_REPLY_DEADLINE_SECONDS) {
      throw new Error(
        `MAYA_CHAT_REPLY_DEADLINE_SECONDS must be between 1 and ${MAX_REPLY_DEADLINE_SECONDS}.`,
      );
    }
  }
  return (deadline + PROXY_GRACE_SECONDS) * 1000;
}
