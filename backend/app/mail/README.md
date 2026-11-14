# Mailbox (IMAP read, SMTP send)

The user's own mailbox, signed in with an app password. It is a per-user connection saved in
Settings > Connections (`app/connections/mailbox.py`: sealed with the connections key in the user's
`user_settings` row, no migration); the server's `MAYA_MAIL_*` mailbox is only the fallback for a
user who saved none (resolution: `ConnectionService.resolve_mailbox`). See
[CONFIGURATION](../../../docs/guides/CONFIGURATION.md)). Standard library only; blocking calls run in
threads. Separate from `app/core/email.py` (login codes) and the Gmail API path.

| Module | Role |
| :-- | :-- |
| `config.py` | `MailboxConfig` from settings (kill switch, password scrubbing, address masking) |
| `errors.py` | `map_error`: the one place every IMAP/SMTP/network failure becomes a plain sentence |
| `imap.py` | One short-lived read-only session (EXAMINE, `BODY.PEEK`), search quoting, folder discovery, 3-session limit |
| `body.py` | Plain-text extraction (HTML stripped, charsets decoded), attachment names only |
| `smtp.py` | Message building (UTF-8 headers, Date, Message-ID, reply headers) and TLS-only sending |
| `service.py` | Async service: list, search, read, send, Sent-folder check; opaque message refs |
| `tools.py` | Chat and voice tools, the untrusted-data envelope, approval-bound proposals, `execute_send` |

Rules: read tools are READ tier and change nothing; `mail_send` and `mail_reply` are
APPROVAL_REQUIRED and the SMTP send happens only in `execute_send`, called by the gateway after the
approval gate passed. Mail text is untrusted data: it can never trigger a send. Not yet checked
against a real Gmail account (tests use `tests/support/mail_fakes.py`).
