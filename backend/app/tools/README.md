# Operator tools

Small commands that a human runs by hand. They are not imported by the API.

## `smtp_smoke`: does the mail account really deliver?

Sends one fixed test message (no code, no account data) through the SMTP settings of a private environment file,
after you confirm the recipient. **Agents must not run it**: it contacts a real mail server with credentials that agents
may not read. Run it yourself:

```powershell
uv run --directory backend python -m app.tools.smtp_smoke --env-file ..\.env
```

It needs `MAYA_SMTP_HOST` and `MAYA_SMTP_FROM_ADDRESS`, plus `MAYA_SMTP_USERNAME` and `MAYA_SMTP_PASSWORD` if the server
requires a login (see the [configuration reference](../../../docs/guides/CONFIGURATION.md)). For a Gmail account use an
App Password, port 587 and `starttls`. The output says only whether the server accepted the message and, on failure, a
category (`authentication`, `connection`, `tls`, `refused`, `message`, `smtp`); it never prints the username, password or
server response. Exit status: 0 delivered, 1 delivery failed, 2 not run.

"Accepted by the server" is not "arrived": check the inbox. The automated tests for this tool use a mocked SMTP library
and never contact a server.
