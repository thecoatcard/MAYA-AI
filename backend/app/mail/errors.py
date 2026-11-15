"""One place that turns every IMAP/SMTP/network failure into a plain sentence.

The sentence names the cause and what to do. It never contains the app password, the server's raw
reply (which can echo credentials or addresses) or a stack trace. Used by the read tools, the send
path and the sent-folder check."""

from __future__ import annotations

import imaplib
import smtplib
import socket
import ssl

from app.mail.config import MailboxConfig


class MailError(Exception):
    """A failure with a plain, safe message. ``code`` is a fixed word for logs and tests."""

    def __init__(self, message: str, code: str = "mail_error", *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


APP_PASSWORD_HELP = (
    "create an App Password at myaccount.google.com/apppasswords with 2-Step Verification on, then "
    "save it in Settings > Connections"
)
STALE_REF = "That message reference is no longer valid (the folder changed). List the mail again."


def _auth_message(config: MailboxConfig) -> str:
    if config.is_gmail:
        return f"Gmail rejected the login; {APP_PASSWORD_HELP}."
    return (
        "The mail server rejected the login. Check the address and app password in "
        "Settings > Connections (many providers need an app password, not the normal password)."
    )


def map_error(error: BaseException, config: MailboxConfig, *, action: str = "mail") -> MailError:
    """The single mapping from a library or network error to a :class:`MailError`."""
    if isinstance(error, MailError):
        return error
    raw = str(error).lower()
    if isinstance(error, smtplib.SMTPAuthenticationError) or (
        isinstance(error, imaplib.IMAP4.error)
        and any(w in raw for w in ("authenticationfailed", "invalid credentials", "login failed"))
    ):
        return MailError(_auth_message(config), "auth")
    if isinstance(error, imaplib.IMAP4.error) and any(
        w in raw for w in ("imap access is disabled", "imap is disabled", "web login required")
    ):
        return MailError(
            "IMAP is turned off for this mailbox. Enable IMAP in the mail provider's settings "
            "(Gmail: Settings > Forwarding and POP/IMAP), then try again.",
            "imap_disabled",
        )
    if isinstance(error, imaplib.IMAP4.error) and any(
        w in raw for w in ("too many", "rate", "throttl", "limit exceeded", "try again later")
    ):
        return MailError(
            "The mail server is limiting connections (too many sessions or requests). "
            "Wait a minute and try again.",
            "rate_limited",
            retryable=True,
        )
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return MailError(
            "The mail server refused the recipient address. Nothing was sent.", "refused"
        )
    if isinstance(error, smtplib.SMTPSenderRefused):
        return MailError("The mail server refused the sender. Nothing was sent.", "refused")
    if isinstance(error, smtplib.SMTPResponseException) and error.smtp_code in (421, 450, 451, 452):
        return MailError(
            "The mail server is limiting or delaying sending (try again in a few minutes).",
            "rate_limited",
            retryable=True,
        )
    if isinstance(error, ssl.SSLCertVerificationError | ssl.SSLError):
        return MailError(
            "The secure (TLS) connection to the mail server failed: its certificate could not be "
            "verified or the port does not speak TLS. Check the host and port settings.",
            "tls",
        )
    if isinstance(error, TimeoutError | socket.timeout):
        return MailError(
            "The mail server did not answer in 15 seconds. Try again in a moment.",
            "timeout",
            retryable=True,
        )
    if isinstance(error, socket.gaierror):
        return MailError(
            "The mail server name could not be found (DNS). Check the incoming and outgoing "
            "server names in Settings > Connections and the network.",
            "dns",
        )
    if isinstance(error, smtplib.SMTPResponseException):
        return MailError(
            f"The mail server rejected the message (SMTP code {error.smtp_code}). "
            "Nothing was sent.",
            "smtp_rejected",
        )
    if isinstance(error, smtplib.SMTPServerDisconnected | smtplib.SMTPConnectError | OSError):
        return MailError(
            "The mail server could not be reached (host or port unreachable, or the connection "
            "was dropped). Check the host, port and network.",
            "unreachable",
            retryable=True,
        )
    if isinstance(error, imaplib.IMAP4.error) and any(
        w in raw for w in ("nonexistent", "no such", "doesn't exist", "does not exist", "trycreate")
    ):
        return MailError("That mail folder was not found on this mailbox.", "folder_not_found")
    if isinstance(error, imaplib.IMAP4.abort):
        return MailError(
            "The mail server closed the connection unexpectedly. Try again.",
            "unreachable",
            retryable=True,
        )
    return MailError(f"The mail {action} failed ({type(error).__name__}). Nothing was changed.")
