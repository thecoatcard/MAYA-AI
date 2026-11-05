# Authentication and session security

**Status:** implemented and tested locally (deterministic tests plus real PostgreSQL and
Redis), then reviewed by an independent read-only security reviewer whose confirmed findings
were fixed (see "Review history" below). The CI workflow now provisions PostgreSQL and Redis, but
GitHub CI has never run (no remote). On 2026-11-05 the owner reported running the SMTP check tool
against the real mail account and registering, verifying and logging in through the running API with real
e-mail; no agent observed this and no evidence file was kept. Decisions: [ADR-0005](../adr/0005-own-account-authentication.md)
and [ADR-0006](../adr/0006-tenant-isolation-with-row-level-security.md). Endpoint contract:
[Auth API](../api/AUTH.md). Schema and roles: [data model](../architecture/DATA_MODEL.md).

## What exists

Own accounts (no external identity provider; Google OIDC login is not implemented):
registration with e-mail verification by one-time code, username and password login,
password recovery by e-mailed code, refresh-token rotation, and logout. Code lives in
`backend/app/auth/` (flows and persistence) and `backend/app/core/security.py`,
`security_otp.py` and `security_tokens.py` (primitives).

## Passwords

- Argon2id through `argon2-cffi`, using its RFC 9106 low-memory profile (3 iterations,
  64 MiB, 4 lanes). Hashing runs on its own pool of four threads (not the event loop's default
  executor, which a hung SMTP server could otherwise starve) with a queue of 64; beyond that
  callers get `503` instead of unbounded memory use.
- Policy: 12 to 128 characters after Unicode NFKC normalization, no composition rules, and
  not equal to the username or e-mail address. Raw input is capped at 256 characters before
  normalization. No breached-password list is checked (not implemented).
- A stale hash (changed parameters) is upgraded transparently after a successful login, only if
  the password was not reset in the meantime.
- An unknown username still triggers one full verification against a placeholder hash, so
  response time does not separate "no such user" from "wrong password". Smaller timing
  differences (an extra database round trip) are not claimed to be eliminated.

## One-time codes

Six digits from `secrets.randbelow`. Only `HMAC-SHA256(key, purpose, challenge id, code)` is
stored, with a server-side key distinct from the token key; comparison is constant time.
A code expires after 10 minutes, allows at most 5 attempts, works once, and is superseded
when a new one is issued. At most one live code exists per account and purpose (a partial
unique index). Purposes cannot be swapped: a reset code does not verify an address and the
reverse. A wrong attempt is counted and committed before the failure is reported; the code is
never logged and appears only in the e-mail body. A new code is withheld during a 60-second
cooldown.

Redemption is atomic: attempts are counted by a conditional `UPDATE`, and exactly one of
several concurrent correct submissions consumes the code.

## Tokens and sessions

- **Access token:** HS256 JWT, 15 minutes. Claims `iss`, `aud`, `sub`, `jti`, `typ`, `iat`,
  `exp`, `workspace_id`, `role`, `sid`. Algorithm pinned; `none` and other algorithms are
  rejected; type and audience must match, so a refresh token cannot be used as an access token.
- **Refresh token:** signed JWT (`typ=refresh`) whose `jti` must equal the one stored in
  `auth_sessions` and which carries a generation number. Each use rotates it atomically
  (compare-and-swap) and raises the generation. Presenting any token of an **older generation**
  (not only the previous one) is treated as theft and revokes the whole session. Sliding
  lifetime 7 days, absolute limit 30 days from login. Two tabs refreshing with the same token at
  once look like a replay and end the session.
- **Liveness check:** every protected request re-reads the session row, so logout and
  password reset take effect immediately instead of at token expiry. The role comes from the
  database, not the token.
- **Reset:** a successful password reset changes the hash, marks the address verified,
  revokes every session of the user and voids other outstanding codes.
- **Login versus reset:** login remembers the password version (`password_changed_at`) it
  verified, and opens its session only if that version is still current, so a login that was
  in flight during a reset cannot create a live session or write back an old hash. This relies
  on a password change always getting a different timestamp (microsecond resolution).
- Token responses carry `Cache-Control: no-store`.
- Expiry is evaluated against one injected clock, in code and in SQL parameters.

## Not revealing which accounts exist

| Operation | Same answer for |
| :--- | :--- |
| Register | new account, e-mail already verified, e-mail unverified, username taken |
| Resend verification, forgot password | known and unknown addresses |
| Verify e-mail, reset password | wrong code, **wrong password (verify only)**, expired, used, exhausted and unknown-account |
| Login | unknown username and wrong password |

The one deliberate exception: a correct password on an unverified account returns
`403 email_not_verified`. That reveals only that someone who already knows the password has
not verified the address. Mail is sent after the response (background task), so SMTP latency
does not distinguish the cases. Registering an address that exists but is unverified re-sends
a code instead of overwriting anything.

**Pre-hijacking.** Verifying an address needs the code **and the account's password**. Without that,
someone could register a victim's address with a password of their own and, when the victim later
registered and confirmed the code mailed to them, end up owning an account verified under the
victim's address. Now the victim's confirmation fails (their password is not the squatter's), the
squatter cannot confirm (the code is only in the victim's mailbox), and the victim takes the account
over through password recovery, which sets their password, verifies the address and revokes all
sessions. The code e-mails state the account's username, since the squatter chose it. The password is
checked before the code is touched, so guessing it burns no code attempt, and a wrong password looks
exactly like a wrong code.

## Rate limits (initial values, to be ratcheted from measured traffic)

Keyed by HMAC digests of the address or e-mail, never the raw value, in Redis fixed windows
(a boundary can allow up to twice the limit). If Redis fails the request fails closed (503).

| Counter | Limit |
| :--- | :--- |
| Code e-mails (register, resend, forgot) per e-mail / per source address | 5 / 20 per hour |
| Code attempts (verify, reset) per e-mail / per source address | 20 per hour (and 50 per day) / 60 per hour |
| Login attempts per username and source address / per source address | 10 / 50 per 15 minutes |

Login attempts are counted atomically **before** the password is checked (so a burst of parallel
guesses is stopped at the limit instead of all being verified) and the count is given back when
the password is right. Counting per username and address pair, not per username, means an
attacker cannot lock the account's owner out; the cost is that guessing one account from many
addresses is bounded by hashing capacity and the per-address limit rather than by an account
limit. The source address is the direct peer: the server is started with
`proxy_headers=False`, so a client-supplied `X-Forwarded-For` has no effect (tested). Behind a
real reverse proxy this needs an approved trusted-proxy setting, otherwise every user would
share one address.

Request bodies over 64 KiB are refused with `413` before validation.

## Tenant isolation

Every table, including `workspaces`, has forced row-level security keyed by a
transaction-local `app.workspace_id`. The API runs as the non-owner `maya_app` role and
refuses to start as any role that is a superuser, can bypass row-level security, create roles or
databases, replicate, or owns objects. Account lookup before the workspace is known uses two
narrowly scoped `SECURITY DEFINER` functions. The runtime role cannot read `hashed_password`
directly; only the by-username function returns it (login needs it) and the by-e-mail function
does not, but any SQL run as `maya_app` can call the former, so this limits accidents and
queries, not a compromised connection. On `auth_sessions` and `auth_otp_challenges` the role may
update only the state columns (never `user_id`, `workspace_id`, `code_hmac`, expiry columns).
Row-level security separates workspaces. Separating users **within** a workspace is done by
repository queries that always carry the user identifier (tested for the authentication
tables); the database does not enforce it. Details and tests: [data model](../architecture/DATA_MODEL.md)
and [verification record](../testing/AUTH_AND_RLS.md).

## Logging and audit

Authentication events (`register`, `login`, `refresh`, `password_reset` and so on) are
structured log lines with a fixed event name and outcome. They never contain addresses,
usernames, codes, passwords or tokens (a test scans the log for all of them). Durable,
workspace-bound `audit_log` rows are **not** written yet: that needs the hash-chain writer
planned for Phase 07, and events about unknown identifiers have no workspace to belong to.
Until then these log lines are the only record.

## Review history

An independent read-only review (no code run) of this layer produced 14 findings. Fixed, each
with a regression test: refresh reuse detection that only recognised the immediate predecessor;
login throttling that counted after the expensive check; a login racing a password reset (and a
hash upgrade overwriting a reset); table-wide `UPDATE` on the authentication tables; the by-e-mail
lookup returning a hash it never needed; trusting `X-Forwarded-For` through uvicorn defaults;
shared thread pool and unbounded hashing queue; no request-size cap; missing `no-store`;
cumulative OTP guessing over a day. Afterwards, on the owner's decisions: pre-hijacking was closed
by requiring the password to verify an address, foreign keys were made composite, and testing the
migrations as a **non-superuser** owner found and fixed a real defect (the function owner needs
`CREATE` on the schema while ownership is transferred; a superuser skips that check).

## Decisions on the review's remaining findings (owner, 2026-11-05)

| Finding | Decision |
| :--- | :--- |
| Pre-hijacking of unverified registrations | **Fixed** (option A): `/verify-email` also takes the account's password. A squatter's account cannot be activated by the victim's confirmation, and the victim takes it over with password recovery; the code e-mails state the username |
| Foreign keys not composite | **Implemented** in the migrations: every reference to a user, session, task or subtask is `(workspace_id, id)`, so a cross-workspace reference or cascade is impossible at the constraint level (deviation from the plan's DDL) |
| Migrator must be a superuser | **Fixed and tested** with a non-superuser owner; a role that cannot `SET ROLE` to `maya_auth_definer` now fails early with a clear message and must be granted it first (Phase 16 role split) |
| Per-user row-level security (`app.user_id`) | **Deferred.** A setting the runtime role can change itself protects only against missing filters, which the repository SQL already carries (tested for the authentication tables); revisit when Phase 04 adds user-owned tables with consumers |
| Recovery denial of service | **Accepted.** Per-e-mail counters are what stop e-mail bombing; the window is an hour |
| Timing side channel | **Accepted.** One password hash is always spent and mail is sent after the response; per-target sample counts are capped by the rate limits (5 sends and 20 attempts per hour per address), which makes a statistical attack impractical |
| Username probing through registration | **Accepted** as a residual risk (option A does not close it); it needs a mailbox the attacker controls and is rate limited. Closing it needs pending registrations (a new table) |

## Known limits and residual risks

- **Username existence can be probed** (see the table above).
- **Same-workspace users.** The database separates workspaces, not users. `maya_app` can read the
  authentication rows of every user in its workspace (including `refresh_jti` and `code_hmac`)
  and set any user's password hash or insert users with any role. Repositories must always filter
  by the user.
- **Unverified registrations are never deleted** (no cleanup job), so usernames and addresses can
  be squatted; the rate limits bound the speed, and a squatted address is recoverable by its owner.
- **Logging scope.** Redaction covers the `maya` logger. Uvicorn's own error log, on an
  unhandled 500, is not redacted, and a PostgreSQL error detail could contain an address; no
  current code path is known to trigger either.
- Row-level security protects against missing filters and application bugs. A connection
  that can run arbitrary SQL as `maya_app` could call `set_config` itself; the controls
  against that are parameterized SQL (a test rejects non-literal statements) and least
  privilege, not the policy.
- Signing keys are static; rotation (a `kid` header and a key list) is not implemented.
  `MAYA_JWT_SIGNING_KEY` and `MAYA_OTP_HMAC_KEY` in the root example are public placeholders
  for loopback use only and pass validation.
- No second factor, no breached-password check, no account lockout notification e-mail.
- Tokens are returned in JSON; where a browser client keeps them (and any cookie, CSRF or
  CORS policy) is decided with the login UI, which does not exist yet.
- E-mail goes out from a background task without a queue: if the process stops between the
  response and the send, the code is lost and the user must request another. Real SMTP delivery
  was verified by the owner (reported 2026-11-05) with the
  [SMTP check tool](../../backend/app/tools/README.md); re-run it after any change to the mail account.
- The interactive API documentation at `/docs` is enabled; disable it before any shared deployment.
- Code redemption and the rate limits trust the server clock of one process; multiple
  instances need synchronized clocks. The login-versus-reset guard relies on a password change
  always receiving a different timestamp.
