# API: authentication

- **Base path:** `/api/v1/auth`
- **Authentication:** none for the first eight endpoints; `Authorization: Bearer <access token>` for `GET /me`
- **Status:** Implemented locally. The web client uses cookie transport for refresh tokens; deterministic API tests cover cookie and legacy JSON modes.

Bodies are JSON with unknown fields rejected. Errors are `{"error": "<code>"}`; validation errors
are `{"error": "validation_error", "fields": ["<field>"]}` and never repeat submitted values.
The password and one-time code appear only in documented requests. Refresh tokens appear in JSON only for callers using legacy transport; cookie-mode responses never include one.
Design rationale and limits: [authentication security](../security/AUTHENTICATION.md).

Normalization: usernames are trimmed and lower-cased (`a-z`, `0-9`, `_`, `.`, `-`, 3 to 32 characters);
e-mail addresses are trimmed and lower-cased ASCII; passwords are 12 to 128 characters after NFKC normalization.

## POST /identify

Accepts a normalized username or e-mail identifier without checking account existence.
This endpoint exists for input validation and always returns the same neutral response.
The client must let the user choose sign-in or account creation; it must not use this response
to infer whether an account exists.

```json
{ "identifier": "alice@example.com" }
```

Response `200` for any valid identifier:

```json
{"status": "accepted"}
```

No account lookup is performed. Invalid identifier syntax still returns `422 validation_error`.

## POST /signup/otp

Generate and send an email verification OTP for a new user initiating registration.

```json
{ "email": "newuser@example.com" }
```

Response `202`: `{"status": "accepted"}` with `Retry-After` cooldown header.

## POST /signup/complete

Verify the OTP code received by the new user, create their workspace and account as verified, open an authenticated session, and return session tokens (or set refresh cookie).

| Field            | Type   | Required | Description                                 |
| :--------------- | :----- | :------- | :------------------------------------------ |
| `email`          | string | yes      | Normalized email address                    |
| `code`           | string | yes      | 6-digit verification code                   |
| `username`       | string | yes      | Chosen username                             |
| `password`       | string | yes      | Chosen password adhering to password policy |
| `full_name`      | string | yes      | 1 to 255 characters                         |
| `workspace_name` | string | no       | Optional workspace name                     |

Response `200`: `TokenResponse` or `CookieTokenResponse` (identical to `/token`), immediately signing the user into the application.

## POST /register

Create an unverified account in a new workspace of its own and e-mail a verification code.
The registrant becomes the workspace `admin`.

| Field            | Type   | Required | Description                                          |
| :--------------- | :----- | :------- | :--------------------------------------------------- |
| `username`       | string | yes      | Unique, normalized as above                          |
| `email`          | string | yes      | Unique                                               |
| `password`       | string | yes      | Password policy above                                |
| `full_name`      | string | yes      | 1 to 255 characters, no control characters           |
| `workspace_name` | string | no       | Defaults to the full name followed by "'s workspace" |

```json
{
  "username": "alice",
  "email": "alice@example.com",
  "password": "correct horse battery staple",
  "full_name": "Alice Example"
}
```

Response `202`: `{"status": "accepted"}` for **every** outcome, including an address or username that is
already registered. If the address belongs to an unverified account a new code is sent instead.
A password that breaks the policy is answered `422` with `password_too_short`, `password_too_long` or
`password_matches_identifier`.

## POST /resend-verification

`{"email": "..."}` → `202 {"status": "accepted"}` whether or not the address exists or is verified.
A code is sent only for an unverified account and not within `MAYA_OTP_RESEND_COOLDOWN_SECONDS` of the previous one. Successful responses include `Retry-After` with the configured cooldown.

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

Without the `X-Maya-Refresh-Transport: cookie` header, `200` (with `Cache-Control: no-store`):

```json
{
  "access_token": "<jwt>",
  "refresh_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 900
}
```

With `X-Maya-Refresh-Transport: cookie`, the response omits `refresh_token` and sets the `maya_refresh` cookie. The cookie is `HttpOnly`, `SameSite=Strict`, scoped to `/api/v1/auth`, has `Max-Age` equal to the refresh-token TTL, and is `Secure` when `MAYA_AUTH_COOKIE_SECURE=true`. The JSON contains only `access_token`, `token_type`, and `expires_in`.

`401 invalid_credentials` for unknown identifiers, wrong passwords, and correct passwords on
unverified accounts; `429 rate_limited` with `Retry-After`. No constant-time response guarantee
is made.

## POST /forgot-password

`{"email": "..."}` → `202 {"status": "accepted"}` for known and unknown addresses alike.

## POST /reset-password

`{"email": "...", "code": "123456", "new_password": "..."}` → `200 {"status": "password_reset"}`.
The password is checked first (`422` with `password_too_short`, `password_too_long` or
`password_matches_identifier`) without using up an attempt. Success revokes every session of the user.
`400 invalid_or_expired_code` otherwise.

## POST /refresh

Without the transport header, `{"refresh_token": "<jwt>"}` → `200` with a new token pair. The old refresh token stops working.
With `X-Maya-Refresh-Transport: cookie`, no body is needed: the cookie is rotated and the response omits the refresh token from JSON. Reusing an already-used refresh token returns `401 invalid_token` **and** revokes the whole session; cookie mode also clears the cookie.

## POST /logout

Without the transport header, `{"refresh_token": "<jwt>"}` → `204`. Revokes that session only; idempotent; works with an expired
refresh token. `401 invalid_token` if the token is not a valid refresh token.
With `X-Maya-Refresh-Transport: cookie`, the endpoint revokes the cookie's session when a cookie is present, clears it in every case, and returns `204`, including when no cookie is present.

## GET /me

Returns `{"user_id": "<uuid>", "workspace_id": "<uuid>", "role": "admin"}` for a bearer token whose session
is live. Used by clients and tests to confirm the principal.

## Errors

| Status    | `error`                                      | Meaning                                                                                                    |
| :-------- | :------------------------------------------- | :--------------------------------------------------------------------------------------------------------- |
| 400       | `invalid_or_expired_code`                    | Any one-time-code failure                                                                                  |
| 401       | `invalid_credentials`                        | Login failed                                                                                               |
| 401       | `invalid_token` / `not_authenticated`        | Bad, expired, revoked or missing token (`WWW-Authenticate: Bearer`)                                        |
| 404 / 405 | `not_found` / `method_not_allowed`           | Unknown route or method                                                                                    |
| 413       | `payload_too_large`                          | Request body over 64 KiB                                                                                   |
| 422       | `validation_error` or a password-policy code | Malformed input                                                                                            |
| 429       | `rate_limited`                               | Includes `Retry-After` seconds                                                                             |
| 503       | `service_unavailable`                        | E-mail not configured, the rate limiter is down (fails closed), or too many password operations are queued |

## Compatibility

New in this version; `/me`, `/resend-verification`, `/refresh`, `/logout`, `/verify-email`, `/forgot-password`
and `/reset-password` are additions to the plan's single `/auth/token` endpoint. `/token` takes a JSON body
(not an OAuth2 form) and is a password login, not the OAuth code flow the plan sketched for an external provider.

## Browser CSRF and rate-limit notes

Cookie-mode refresh and logout require `X-Maya-Refresh-Transport: cookie` and use `SameSite=Strict`. A cross-origin page cannot send the custom header without a CORS preflight; this API does not configure CORS. Do not enable permissive CORS without revisiting the CSRF design.

Behind the Next.js rewrite, `request.client.host` is the proxy peer (`127.0.0.1`) for all forwarded requests. `client_address` deliberately ignores `X-Forwarded-For`: Next.js preserves a client-supplied value, so it cannot identify a trustworthy source address for per-address limits. Those limits therefore share one proxy-peer bucket until the hosting target is selected. At that time, a front reverse proxy that overwrites `X-Forwarded-For` can become the trusted source; no behavior change is made before then.
