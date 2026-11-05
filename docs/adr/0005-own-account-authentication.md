# ADR-0005: Own-account authentication, one-time codes and sessions

- **Status:** Accepted (by the repository owner, 2026-11-05)
- **Date:** 2026-11-05
- **Deciders:** Repository owner
- **Related:** [Phase 02](../implementation/IMPLEMENTATION_PHASES/PHASE_02_AUTH_DATABASE_SECURITY.md), [ADR-0006](0006-tenant-isolation-with-row-level-security.md), [authentication security](../security/AUTHENTICATION.md), [Auth API](../api/AUTH.md), [START_HERE](../implementation/START_HERE.md) section 4

## Context

The owner decided that MAYA AI uses its own accounts: sign-up verified by an e-mailed one-time code, username and
password login with no code at login, and password recovery by an e-mailed code. This replaces the plan's Google
OIDC default (open decision O2). Google OAuth is needed only later, for the Gmail and Calendar integration. The plan
specifies JWT access and refresh tokens but not how refresh tokens are rotated or revoked, nor the code lifecycle.
The existing stack is FastAPI, SQLAlchemy/asyncpg, Redis and structured redacted logging; no authentication
library is installed.

## Decision

1. **Passwords:** Argon2id via `argon2-cffi` (RFC 9106 low-memory profile), off the event loop with bounded
   concurrency; length-based policy (12 to 128 characters after NFKC, no composition rules). `passlib` is not used.
2. **One-time codes:** six digits, stored only as `HMAC-SHA256(server key, purpose, challenge id, code)`, 10-minute
   expiry, five attempts, single use, superseded on resend, one live code per user and purpose (partial unique
   index), 60-second resend cooldown. Redemption uses conditional `UPDATE`s so concurrency cannot reuse a code, and the
   attempt counter is committed even when the attempt fails.
3. **Tokens:** HS256 JWTs through PyJWT, with separate keys for tokens (`MAYA_JWT_SIGNING_KEY`) and codes
   (`MAYA_OTP_HMAC_KEY`). Access tokens last 15 minutes and carry `sid`; the refresh token is a signed JWT whose `jti`
   must match the single current value in `auth_sessions` and carries a generation that rises on every rotation. Refresh
   rotates atomically; a token of any older generation revokes the session. Sliding lifetime 7 days, absolute 30 days.
   Every protected request checks the session row, so logout and password reset are immediate. A login opens its session
   only if the password version it verified is still current.
4. **Registration:** every account gets a personal workspace and the `admin` role; invitations are out of scope. The
   response is identical whether or not the address or username is taken; an unverified address is re-sent a code, and a
   password reset also verifies the address. **Verifying an address needs the code and the account's password**
   (decided after the security review): otherwise someone who pre-registered a victim's address with a password of their
   own would end up owning an account verified under the victim's address. The victim's confirmation then fails, and they
   take the account over by password recovery; the code e-mails name the username. The password is checked before the
   code is touched.
5. **Login:** a correct password on an unverified account returns a distinct `403`; every other failure is one generic
   `401`. An unknown username still costs one hash verification.
6. **Rate limits:** Redis fixed windows keyed by HMAC digests, failing closed; login attempts are counted atomically
   before the password is checked, per username and address pair (so an attacker cannot lock the owner out) and per
   address, and refunded on success. Hashing has its own bounded thread pool and queue. Values are in the security document.
7. **E-mail:** standard-library SMTP over mandatory TLS, sent from a background task after the response; unconfigured
   e-mail makes the e-mail endpoints answer 503 instead of pretending. There is no console or logging backend, because
   codes are never logged.
8. **Transport:** JSON bodies and bearer tokens; no cookies, so no CSRF surface yet. Where a browser stores tokens is decided
   with the login UI.
9. **Audit:** no `audit_log` rows are written in Phase 02. Events go to structured logs with fixed names and no identifiers;
   durable rows need the Phase 07 hash-chain writer (see ADR-0006).

## Alternatives considered

- **External identity provider (Google OIDC):** superseded by the owner's decision.
- **`passlib`, `bcrypt`, `python-jose`:** unmaintained or weaker choices than `argon2-cffi` and PyJWT.
- **`email-validator`:** a syntax check cannot prove an address; the code does. A strict ASCII pattern and the standard
  library's header-injection protection are enough, with one dependency fewer.
- **Redis for codes and sessions:** an AOF-backed cache is not the authority for revocation, and the reset must revoke
  sessions in the same transaction that changes the password.
- **Stateless access tokens only:** logout and reset would take effect only at expiry.
- **Opaque refresh tokens with a definer lookup:** a signed token lets the workspace be taken from a verified claim,
  needing no further `SECURITY DEFINER` surface.
- **Console e-mail backend for development:** violates "codes are never logged".

## Consequences

- Positive: no plaintext secrets or codes at rest; replay and brute force are bounded; enumeration is closed except the
  deliberate 403; no external dependency for login.
- Negative: three new dependencies (`alembic`, `argon2-cffi`, `pyjwt`); one extra database read per authenticated request;
  no durable audit trail, no second factor, no key rotation and no cleanup of unverified accounts yet; the e-mail
  background task is not durable; the verify step costs the user one more field and, for a squatted address, a detour
  through password recovery. Usernames can still be probed with a controlled mailbox (accepted, rate limited).
- Mitigations: each limit is listed in the security document and revisited in the owning later phases (audit in
  Phase 07, queues in Phase 06, deployment hardening in Phase 16).

## Security and compliance impact

Credentials, tokens and codes never reach logs, responses or e-mail headers (tests scan for them). Keys are `SecretStr`
settings of at least 32 bytes, required and different from each other; the example values are public and for loopback use.
Personal data stored: username, e-mail address, display name and workspace name.

## Implementation notes

`backend/app/auth/` (flows, store, schemas), `backend/app/core/security.py`, `security_otp.py`, `security_tokens.py`,
`ratelimit.py`, `email.py`; endpoints in `backend/app/api/v1/auth.py`. Verification record:
[authentication and row-level security tests](../testing/AUTH_AND_RLS.md).

## Review trigger

A second factor or step-up code for RESTRICTED actions (Phase 07), a browser login UI (which must collect the password
at the verification step), a trusted reverse proxy, multiple
API instances, key rotation, or a managed e-mail service (Phase 16).
