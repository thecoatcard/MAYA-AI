# Connections API (`/api/v1/settings/connections`)

The caller's own connections to outside services. Bearer-authenticated; every call acts on the caller's workspace and user only (there is no user id in any URL or body). Responses are `Cache-Control: no-store` and never contain a token. Design: [ADR-0024](../adr/0024-per-user-connections.md); threats: [security](../security/CONNECTIONS.md).

Providers: `mail` (the user's mailbox), `github`, `notion`. Unknown provider: 404 `{"error":"unknown_provider","message":...}`.

## Connection object

`provider`, `label`, `state` (`not_connected`, `saved`, `connected`, `error`, `server_default`, `unreadable`), `source` (`user`, `environment`, `null`), `message`, `account` (GitHub login or Notion integration name), `scopes` (GitHub classic tokens), `database_id`, `database_title`, `last_tested_at`, `takes_database_id`, `kind` (`token` or `mailbox`), `mailbox` (mailbox only: `address` masked like `m***@gmail.com`, `imap_host`, `imap_port`, `smtp_host`, `smtp_port`, `password_saved`; never the password).

**Mailbox (`PUT /mail`).** Body: `address`, `token` (the app password, write-only; spaces are removed), `imap_host`, `imap_port` (993), `smtp_host`, `smtp_port` (465 or 587). Omitted fields keep the saved value. Hosts must be public host names (no localhost, private or internal addresses): `422` with `host_format`, `host_forbidden`, `port_format`, `address_format` or `password_missing`. The test (`POST /mail/test`, and after every save) logs in over IMAP and authenticates over SMTP without sending anything; the result carries `steps` (`label`, `ok`, `message`) for the incoming and outgoing halves. At run time the signed-in user's saved mailbox is used first, else the server's `MAYA_MAIL_*` mailbox, else none.

## Endpoints

| Method and path                         | Body                                         | Answer                                                    |
| :-------------------------------------- | :------------------------------------------- | :-------------------------------------------------------- |
| `GET /`                                 |                                              | `{encryption_available, connections: [Connection]}`        |
| `PUT /{provider}`                       | `{token?, database_id?}`                      | `{connection, test}`: saves, then tests                   |
| `POST /{provider}/test`                 |                                              | `{connection, test}`                                      |
| `DELETE /{provider}`                    |                                              | `{connection}` (back to `not_connected` or server default) |

`token` is optional on a later `PUT` for Notion (change only the database); `database_id` accepts a Notion database URL or id (`""` clears it). A failed test still leaves the credentials saved.

`test`: `{ok, code, message, account, scopes, database_title}`. `code` is one of `ok`, `ok_no_database`, `token_missing`, `token_rejected` (401), `token_forbidden` (403), `database_not_shared` (404, share the database with the integration), `database_id_invalid` (a page, not a database), `database_id_format`, `rate_limited`, `timeout`, `network`, `provider_error`, `needs_reconnect`. `message` is a plain sentence that says what to do.

## Errors

| Status | `error`                       | When                                                              |
| :----- | :---------------------------- | :---------------------------------------------------------------- |
| 401    | `not_authenticated`           | no or bad bearer token                                            |
| 422    | `token_format`, `token_missing`, `database_id_format`, `validation_error` | unusable input (with `message`; `validation_error` names fields only) |
| 429    | `rate_limited`                | over 10 writes (save, test, disconnect) or 60 reads per minute    |
| 503    | `connections_not_configured`  | `MAYA_CONNECTIONS_ENCRYPTION_KEY` is not set                      |
