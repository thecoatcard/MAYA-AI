# Module: authentication

- **Status:** Implemented and tested locally (see the [verification record](../../../docs/testing/AUTH_AND_RLS.md))
- **Owner:** Repository owner
- **Location:** `backend/app/auth/`

## Responsibility

Own-account registration, e-mail verification, login, password recovery, refresh-token rotation, logout and the
authenticated-principal dependency. It is not an identity provider, authorization policy (permission tiers and
approvals are Phase 07) or audit writer. Cryptographic primitives live in `backend/app/core/security.py`,
`security_otp.py` and `security_tokens.py`.

## Interfaces

- `AuthService` (`service.py`): `register`, `resend_verification`, `verify_email` (code **and** the account's password), `login`, `request_password_reset`,
  `reset_password`, `refresh`, `logout`, `authenticate`, `deliver`. Methods that must send a code return an
  `OutboundEmail` for the caller to send after the response.
- `AuthStore` / `AuthTransaction` (`store.py`): the persistence port. `PostgresAuthStore` (`postgres.py`) is the real
  implementation; the tests also run the same scenarios against an in-memory store.
- Dependencies for routes (`dependencies.py`): `get_principal` (verified bearer token with a live session) and
  `get_tenant_database`, which opens transactions that carry the caller's workspace for row-level security.
- Endpoints: `backend/app/api/v1/auth.py`, documented in the [Auth API](../../../docs/api/AUTH.md).

```python
async def handler(database: Annotated[TenantDatabase, Depends(get_tenant_database)]) -> None:
    async with database.transaction() as connection:
        ...  # every query here sees only the caller's workspace
```

## Dependencies

- Depends on: `app.core` (settings, clock, security primitives, rate limiter, e-mail), `app.db.tenant`, PostgreSQL, Redis.
- Depended on by: every later phase that needs the caller's identity or a tenant-scoped transaction.

## Configuration

Keys, token lifetimes, the resend cooldown and SMTP settings are listed in the
[configuration reference](../../../docs/guides/CONFIGURATION.md). Rate-limit values are constants in `policy.py`.

## Data

Reads and writes `workspaces`, `users`, `auth_otp_challenges` and `auth_sessions`; schema in the
[data model](../../../docs/architecture/DATA_MODEL.md). The password hash is read only through the two definer functions.

## Failure behavior

Domain errors (`errors.py`) map to generic HTTP answers in `backend/app/api/v1/errors.py`. If Redis or e-mail is
unavailable the request fails closed with 503. Delivery failures after the response are logged as an event and are not
visible to the caller.

## Security considerations

See [authentication security](../../../docs/security/AUTHENTICATION.md): enumeration resistance, one-time-code handling,
token rotation, and what is deliberately not implemented. Never put an e-mail address, username, code, password or token in a log
line; `events.py` accepts only fixed names and outcomes.

## Testing

```powershell
uv run --directory backend pytest -q                       # deterministic tests; PostgreSQL/Redis tests are skipped
$env:MAYA_TEST_ENV_FILE = "..\.env.example"                 # use the local services
$env:MAYA_REQUIRE_POSTGRES_TESTS = "1"                      # fail instead of skipping
uv run --directory backend pytest -q
```

## Operations

Rotate `MAYA_JWT_SIGNING_KEY` and `MAYA_OTP_HMAC_KEY` by replacing them: every session and outstanding code becomes
invalid (no key rotation window exists yet).
