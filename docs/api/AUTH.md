# API: authentication

- **Base path:** `/api/v1/auth`
- **Authentication:** none for the first eight endpoints; `Authorization: Bearer <access token>` for `GET /me`
- **Status:** Implemented and tested locally. Browser login UI: planned (none exists).

Bodies are JSON with unknown fields rejected. Errors are `{"error": "<code>"}`; validation errors
are `{"error": "validation_error", "fields": ["<field>"]}` and never repeat submitted values.
The password, one-time code and tokens appear only in the documented request and response bodies.
Design rationale and limits: [authentication security](../security/AUTHENTICATION.md).

Normalization: usernames are trimmed and lower-cased (`a-z`, `0-9`, `_`, `.`, `-`, 3 to 32 characters);
e-mail addresses are trimmed and lower-cased ASCII; passwords are 12 to 128 characters after NFKC normalization.

## POST /register

Create an unverified account in a new workspace of its own and e-mail a verification code.
The registrant becomes the workspace `admin`.

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `username` | string | yes | Unique, normalized as above |
| `email` | string | yes | Unique |
| `password` | string | yes | Password policy above |
| `full_name` | string | yes | 1 to 255 characters, no control characters |
| `workspace_name` | string | no | Defaults to the full name followed by "'s workspace" |

```json
{ "username": "alice", "email": "alice@example.com", "password": "correct horse battery staple", "full_name": "Alice Example" }
```

Response `202`: `{"status": "accepted"}` for **every** outcome, including an address or username that is
already registered. If the address belongs to an unverified account a new code is sent instead.
A password that breaks the policy is answered `422` with `password_too_short`, `password_too_long` or
`password_matches_identifier`.

## POST /resend-verification

`{"email": "..."}` → `202 {"status": "accepted"}` whether or not the address exists or is verified.
A code is sent only for an unverified account and not within 60 seconds of the previous one.

## POST /verify-email

`{"email": "...", "code": "123456", "password": "..."}` → `200 {"status": "verified"}`, or
`400 invalid_or_expired_code` for any wrong code, wrong password, expired, used, exhausted or unknown case. Five
attempts per code, 10-minute lifetime. The password is the one chosen at registration; it protects the address
from being claimed by someone who pre-registered it. If it is not accepted, the address may have been registered
by someone else: use `POST /forgot-password`, which takes the account over (the e-mails name the username).

## POST /token

Username and password login. No code is needed at login.

```json
{ "username": "alice", "password": "correct horse battery staple" }
```

`200` (with `Cache-Control: no-store`):

```json
{ "access_token": "<jwt>", "refresh_token": "<jwt>", "token_type": "bearer", "expires_in": 900 }
```

`401 invalid_credentials` for an unknown user or a wrong password; `403 email_not_verified` for a correct
password on an unverified account; `429 rate_limited` with `Retry-After`.

## POST /forgot-password

`{"email": "..."}` → `202 {"status": "accepted"}` for known and unknown addresses alike.

## POST /reset-password

`{"email": "...", "code": "123456", "new_password": "..."}` → `200 {"status": "password_reset"}`.
The password is checked first (`422` with `password_too_short`, `password_too_long` or
`password_matches_identifier`) without using up an attempt. Success revokes every session of the user.
`400 invalid_or_expired_code` otherwise.

## POST /refresh

`{"refresh_token": "<jwt>"}` → `200` with a new token pair. The old refresh token stops working.
Reusing an already-used refresh token returns `401 invalid_token` **and** revokes the whole session.

## POST /logout

`{"refresh_token": "<jwt>"}` → `204`. Revokes that session only; idempotent; works with an expired
refresh token. `401 invalid_token` if the token is not a valid refresh token.

## GET /me

Returns `{"user_id": "<uuid>", "workspace_id": "<uuid>", "role": "admin"}` for a bearer token whose session
is live. Used by clients and tests to confirm the principal.

## Errors

| Status | `error` | Meaning |
| :--- | :--- | :--- |
| 400 | `invalid_or_expired_code` | Any one-time-code failure |
| 401 | `invalid_credentials` | Login failed |
| 401 | `invalid_token` / `not_authenticated` | Bad, expired, revoked or missing token (`WWW-Authenticate: Bearer`) |
| 403 | `email_not_verified` | Correct password, address not yet verified |
| 404 / 405 | `not_found` / `method_not_allowed` | Unknown route or method |
| 413 | `payload_too_large` | Request body over 64 KiB |
| 422 | `validation_error` or a password-policy code | Malformed input |
| 429 | `rate_limited` | Includes `Retry-After` seconds |
| 503 | `service_unavailable` | E-mail not configured, the rate limiter is down (fails closed), or too many password operations are queued |

## Compatibility

New in this version; `/me`, `/resend-verification`, `/refresh`, `/logout`, `/verify-email`, `/forgot-password`
and `/reset-password` are additions to the plan's single `/auth/token` endpoint. `/token` takes a JSON body
(not an OAuth2 form) and is a password login, not the OAuth code flow the plan sketched for an external provider.
