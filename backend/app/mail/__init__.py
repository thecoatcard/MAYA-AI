"""The user's own mailbox over IMAP (read) and SMTP (send), signed in with an app password.

Separate from ``app.core.email`` (the system mailer for one-time login codes) and from the Gmail
API path (``mcp_servers/google_adapter``). Reading never changes the mailbox (read-only select and
``BODY.PEEK``). Sending happens only in the approved-execution path (see ``app.mail.tools``).
"""
