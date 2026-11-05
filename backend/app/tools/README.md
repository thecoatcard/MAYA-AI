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

## `gemini_smoke`: owner-run Gemini diagnostic check

Tests live API connectivity and model resolution for the configured Gemini key. **Agents must not run it**: it may contact external services and incur costs. Run it yourself:

```powershell
uv run --directory backend python -m app.tools.gemini_smoke --alias orchestrator
```

Safety gates:
- Free-tier verification by default (`--verify-free-tier`), stopping cleanly on 429 quota exhaustion.
- Any paid operation requires explicit owner confirmation: `--confirm-paid-calls-up-to-inr-100` (hard spend cap of INR 100).
- The tool never prints or logs the API key.
- Automated tests for this tool use an offline mock client (`tests.support.fake_gemini_client`) and never contact the network.
