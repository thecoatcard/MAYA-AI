# ADR-0009: HttpOnly refresh-cookie transport for the browser client

- **Status:** Accepted (owner approved this exact design on 2026-11-06)
- **Date:** 2026-11-06
- **Deciders:** Repository owner
- **Related:** [ADR-0005](0005-own-account-authentication.md), [Auth API](../api/AUTH.md), [Phase 04](../implementation/IMPLEMENTATION_PHASES/PHASE_04_CHAT_CONVERSATIONS.md)

## Context

The browser uses the same-origin Next.js rewrite for `/api/v1/*`. Returning a refresh token to JavaScript would expose a long-lived credential to any script running in the origin, while a cookie can be scoped to the auth paths and protected from script access. Existing non-browser clients and tests must retain the current JSON-token contract.

The owner approved: **"HttpOnly cookie via proxy (Recommended)"** on 2026-11-06.

## Decision

1. Cookie transport is opt-in per request with `X-Maya-Refresh-Transport: cookie`. Requests without the header retain the existing `/token`, `/refresh`, and `/logout` JSON behavior.
2. Cookie-mode `/token` and `/refresh` set `maya_refresh` with `HttpOnly`, `SameSite=Strict`, `Path=/api/v1/auth`, `Max-Age` equal to the refresh-token TTL, and `Secure` controlled by `MAYA_AUTH_COOKIE_SECURE` (default `true`). Their JSON response contains only `access_token`, `token_type`, and `expires_in`; credentials are `no-store`.
3. Cookie-mode `/refresh` reads the refresh token from the cookie without a request body, rotates it, and clears the cookie on missing or invalid credentials. Cookie-mode `/logout` revokes the cookie's session when present, always clears the cookie, and returns `204`, including when no cookie exists.
4. The web client stores only the short-lived access token in memory. The refresh token is never exposed to JavaScript.
5. CSRF mitigation is `SameSite=Strict` plus the required custom transport header. A cross-origin page cannot send that header without a CORS preflight, and no CORS is configured.

## Alternatives considered

- **Refresh token in JSON and browser memory:** rejected for the browser because JavaScript can read and exfiltrate it; retained only for callers that do not opt into cookie transport.
- **Unscoped or JavaScript-readable cookie:** rejected because it unnecessarily exposes the credential to scripts and unrelated application routes.

## Consequences

- Positive: refresh credentials are unavailable to application JavaScript, and same-origin routing avoids cross-origin credential handling.
- Negative: cookie transport relies on the same-origin proxy and needs a `Secure=false` local setting only when the local browser cannot use secure cookies over plain HTTP.
- Mitigations: the transport is explicit and opt-in; token responses are not cached; the cookie is path-scoped, strict same-site, and HttpOnly; the API keeps its legacy JSON mode.

## Security and compliance impact

The browser's refresh credential is sent only as an HttpOnly cookie to the auth path. The access token remains in memory and is sent as a bearer token to chat endpoints. The required custom header and strict same-site policy mitigate cross-site request forgery for refresh-cookie actions. No token is logged.

## Implementation notes

Implemented by the auth API in `backend/app/api/v1/auth.py`, the `auth_cookie_secure` setting in `backend/app/core/config.py`, and the browser client in `apps/web/src/lib/auth-client.ts` and `apps/web/src/lib/auth-session.ts`. There is no database migration.

## Review trigger

Revisit if the browser stops using the same-origin proxy, CORS is introduced, the hosting topology changes, or cookie transport is needed by a non-browser client.
