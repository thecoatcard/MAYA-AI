# Connections: per-user credentials

How MAYA stores and uses the GitHub and Notion tokens a user adds in Settings > Connections ([ADR-0024](../adr/0024-per-user-connections.md)). Code: `backend/app/connections/`.

## Controls

| Threat                                | Control                                                                                                                                                                  |
| :------------------------------------ | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Database or backup disclosure         | AES-256-GCM (authenticated) with a fresh 96-bit nonce per value. The key is `MAYA_CONNECTIONS_ENCRYPTION_KEY`, never stored in the database.                              |
| One user's blob replayed for another  | The associated data is `workspace\|user\|provider`; a copied value fails authentication. Row level security on `user_settings` repeats the per-user scope.                 |
| No key configured                     | Fail closed: saving returns 503 `connections_not_configured`; nothing is stored in plain text. Environment defaults keep working.                                         |
| Wrong or rotated key                  | The value is reported as "Reconnect needed" and is NOT replaced by the shared environment token. Rotating the key means users reconnect.                                   |
| Secret in an API response or the page | No endpoint returns a token. Responses carry a masked status (state, account, scopes, database id) and `Cache-Control: no-store`. The web form clears the field after Save. |
| Secret in logs, errors, memory        | The token is a `SecretStr` in requests, excluded from `repr` in `ResolvedConnection`, never logged; error text is built from fixed sentences. `memory/scrubber.py` also redacts GitHub (classic and fine-grained) and Notion token shapes before storage. Validation errors name fields only. |
| Using another user's token at run time| Resolution always names the requesting task's workspace and user (job owner in agents, tool call scope in gateway tools). With no caller identity a tool refuses instead of guessing. |
| Abuse and outbound calls              | Provider hosts are fixed (`api.github.com`, `api.notion.com`), no redirects, 10 s timeout. Save, test and disconnect are limited to 10 per minute per user, reads to 60.   |

## Setting the key

Generate 32 random bytes, base64:

```bash
python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Put the result in the private `.env` as `MAYA_CONNECTIONS_ENCRYPTION_KEY`. An invalid value stops the server at start with a clear message (the value is not echoed). Back the key up with your other secrets: without it saved tokens cannot be read.

## Environment defaults

## Server-level connections belong to the deployment owner

`MAYA_GITHUB_TOKEN`, `MAYA_NOTION_TOKEN`, `MAYA_NOTION_DATABASE_ID`, the default mailbox (`MAYA_MAIL_*`) and the Google Calendar token (`MAYA_GOOGLE_ACCESS_TOKEN`) are the deployment owner's. They are used only by the accounts named in `MAYA_ENV_CONNECTIONS_USERS` (comma-separated account emails or user ids). Every other user, in chat, voice, tasks, agents, MCP client credentials, schedules and the Settings status, sees "Not connected" until they save their own connection; the status never says "server default" or `environment` to them.

- Fail closed: an empty or unset list means nobody, so a fresh deployment shares nothing. Any error while checking an account (for example the database) counts as "not the owner" and is never cached; a successful check is cached for 60 seconds.
- The caller's email is read from their own account row (row level security, no migration).
- A saved but unreadable connection never falls back to the server value, even for the owner.
- The owner must set `MAYA_ENV_CONNECTIONS_USERS=<their account email>` in the private env file, or save their own tokens in Settings > Connections. When server tokens exist but the list is empty, Settings > Connections tells the owner so.
- Not affected: the system mailers (`MAYA_SMTP_*` for sign-in codes and scheduled-report delivery).
- A test (`tests/unit/test_server_connections_owner.py`) fails if any module outside `app/connections/` reads these settings directly.

## Not covered

No key rotation tool (re-save needed), no audit row for connection changes (an `info` log line records provider and outcome only), and Google is still configured by the administrator.
