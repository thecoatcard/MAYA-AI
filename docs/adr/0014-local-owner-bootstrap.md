# ADR-0014: Local-owner bootstrap for the desktop app

- **Status:** Accepted (owner approved on 2026-11-08)
- **Date:** 2026-11-08
- **Deciders:** Repository owner
- **Related:** [ADR-0005](0005-own-account-authentication.md) (extends it), [ADR-0006](0006-tenant-isolation-with-row-level-security.md), [ADR-0009](0009-refresh-cookie-transport.md), [Auth API](../api/AUTH.md), [Configuration](../guides/CONFIGURATION.md)

## Context

ADR-0005 makes sign-up depend on an e-mailed one-time code and forbids a console or logging mail fallback. On a fresh desktop install no SMTP server exists, so registration answers 503 and nobody can create the first account. The desktop app runs the API on 127.0.0.1 for one person on one machine.

## Decision

1. Two endpoints, both under `/api/v1/auth`: `GET /bootstrap/status` returns `{"available": bool, "reason"?: string}`; `POST /bootstrap` takes `{full_name, email, password}`, creates the first workspace and its admin user with the e-mail marked verified (no code), and returns exactly the login response (JSON tokens, or the HttpOnly refresh cookie with `X-Maya-Refresh-Transport: cookie`).
2. They work only when **all** of these hold, checked on every call:
   - `MAYA_LOCAL_OWNER_BOOTSTRAP` is true (default **false**; the desktop entry point `python -m app.desktop` sets it);
   - the direct peer address is a literal loopback address (`127.0.0.0/8`, `::1`, `::ffff:127.x`). `X-Forwarded-For` and similar headers are never read (the API has no trusted-proxy setting, and `client_address` already uses only the socket peer);
   - the database holds **zero users in any workspace**.
3. Every refusal (flag off, remote peer, users exist, lost race, unreadable user count) is the same `404 {"error": "bootstrap_unavailable"}`. The status endpoint answers `{"available": false, "reason": "disabled"}` for flag-off and non-local peers alike, so a remote caller learns nothing about the database. For a local peer with the flag on it may say `already_initialised` or `not_ready`.
4. The same password policy as registration applies (length, common/contextual passwords). Calls are rate limited per peer address (5 per hour) and globally (20 per hour), counted before any work is done, failing closed when Redis is down.
5. The new workspace uses the fixed slug `local-owner`. The slug is unique, so two concurrent bootstraps cannot both succeed even if both saw an empty database.
6. The zero-users check crosses tenants, and every tenant table is under forced row-level security. An unscoped connection sees no rows, and the two existing definer lookups (`auth_find_account_by_username/email`) need a known value. The check therefore calls a new definer function `public.auth_any_account_exists()`. **This function does not exist yet; it needs a migration (Level 3).** Until it is applied the call fails, the bootstrap reports `not_ready` and refuses: the feature fails closed.
7. Also in this change: the pending-sign-up state (`/signup/otp` to `/signup/complete`) moves from process memory to Redis (keyed digest of the address, TTL equal to the code lifetime) so a restart or a second worker does not lose it. The in-memory implementation remains only as the test double.

### Required migration (not written; needs approval)

```sql
CREATE FUNCTION public.auth_any_account_exists() RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
  AS $$ SELECT EXISTS (SELECT 1 FROM public.users) $$;
ALTER FUNCTION public.auth_any_account_exists() OWNER TO maya_auth_definer;
REVOKE ALL ON FUNCTION public.auth_any_account_exists() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.auth_any_account_exists() TO maya_app;
```

It follows the pattern of migration 0004 (definer role already has `SELECT (id, ...)` on `users` and the `auth_definer_lookup` policy). It returns only a boolean.

## Alternatives considered

- **Console or file mail fallback:** forbidden by ADR-0005 (codes must never be logged).
- **Always-open first-user registration:** any process that can reach the port could claim the install; rejected.
- **Redis or file marker for "already initialised":** can drift from the database (flushed Redis, restored database) and would allow a second owner; rejected as the primary check. The unique slug stays as a backstop.
- **Counting users through the runtime role:** impossible under forced RLS without weakening a policy; rejected.

## Consequences

- Positive: a desktop user can create the first account with no mail server; the feature is inert in every server deployment.
- Negative: one new definer function, one more unauthenticated endpoint pair, and a code path that skips e-mail verification.
- Mitigations: the layered conditions above, uniform refusals, rate limits, tests for each condition, and no behaviour change when the flag is off.

## Security and compliance impact

Threat analysis:

| Threat                                                        | Control                                                                                              |
| :------------------------------------------------------------ | :--------------------------------------------------------------------------------------------------- |
| Remote attacker claims a server install                       | Flag off by default; loopback peer required; `X-Forwarded-For` ignored                                |
| Reverse proxy on the same host makes remote traffic look local | The deployment must not enable the flag behind a proxy; the flag is only set by `app.desktop`        |
| Another local user or malware races the real owner at first run | Cannot be fully excluded for a loopback service; mitigated by rate limits and by the app binding a random port per launch and creating the account immediately. Accepted residual risk |
| Browser page (DNS rebinding or cross-site POST) reaches the local port | `MAYA_ALLOWED_HOSTS` Host allow-list, no CORS, strict JSON bodies; the cookie path requires a custom header |
| Takeover of an existing install                               | Refused once any user exists; fixed unique slug blocks a race                                        |
| User enumeration through the endpoints                        | One uniform refusal body; status says "disabled" to non-local peers; no e-mail is sent or looked up  |
| Weak first password                                           | Registration password policy; rate limits                                                            |
| Unverified e-mail address on the owner account                | Accepted: possession of the machine is the proof; address is only used for sign-in and recovery     |

No credential is logged; events record only outcomes (`bootstrap` created/refused/failed).

## Implementation notes

`backend/app/auth/service.py` (`bootstrap`, `bootstrap_status`), `backend/app/auth/pending.py` (Redis sign-up state), `backend/app/api/v1/auth.py`, `backend/app/auth/postgres.py` (`any_account_exists`), `MAYA_LOCAL_OWNER_BOOTSTRAP` in `backend/app/core/config.py`. Tests: `backend/tests/auth/test_local_owner_bootstrap.py`, `backend/tests/integration/test_local_owner_bootstrap_api.py`. The migration above is **not** included.

## Review trigger

Revisit when a trusted-proxy setting is introduced, when the API is packaged for anything other than a single-user desktop, or when SMTP-free sign-up is needed elsewhere.
