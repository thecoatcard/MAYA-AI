"""Outbound e-mail for one-time codes. Message bodies carry codes and are never logged."""

import asyncio
import smtplib
import ssl
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Literal, Protocol

SMTP_TIMEOUT_SECONDS = 10.0


class EmailDeliveryError(Exception):
    """Delivery failed. Carries no detail: provider errors can echo addresses or credentials.

    ``category`` is one of a few fixed words derived from the exception *type*, never from its text,
    so operators can tell a bad login from a refused connection without any secret leaking.
    """

    def __init__(self, category: str = "other") -> None:
        super().__init__("E-mail delivery failed")
        self.category = category


def classify(error: Exception) -> str:
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "authentication"
    if isinstance(error, ssl.SSLError | smtplib.SMTPNotSupportedError):
        return "tls"
    if isinstance(error, smtplib.SMTPRecipientsRefused | smtplib.SMTPSenderRefused):
        return "refused"
    if isinstance(error, smtplib.SMTPServerDisconnected | smtplib.SMTPConnectError):
        return "connection"
    if isinstance(error, smtplib.SMTPException):  # an OSError subclass, so it is checked first
        return "smtp"
    if isinstance(error, OSError | TimeoutError):
        return "connection"
    if isinstance(error, ValueError):
        return "message"
    return "smtp"


@dataclass(frozen=True)
class OutboundEmail:
    to: str
    subject: str
    body: str = field(repr=False)
    html: str | None = field(default=None, repr=False)


class EmailSender(Protocol):
    async def send(self, message: OutboundEmail) -> None: ...


class SmtpEmailSender:
    """Standard-library SMTP over mandatory TLS (STARTTLS or implicit SSL), run in a thread."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        security: Literal["starttls", "ssl"],
        username: str | None,
        password: str | None,
        sender: str,
        timeout: float = SMTP_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host
        self._port = port
        self._security = security
        self._username = username
        self._password = password
        self._sender = sender
        self._timeout = timeout
        # Its own pool: a hung SMTP server must not starve password hashing or anything else.
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="maya-smtp")

    async def send(self, message: OutboundEmail) -> None:
        await asyncio.get_running_loop().run_in_executor(self._executor, self._send, message)

    async def aclose(self) -> None:
        """Stop accepting mail and wait for in-flight SMTP operations to finish."""
        shutdown = asyncio.get_running_loop().run_in_executor(None, self._executor.shutdown, True)
        try:
            await asyncio.shield(shutdown)
        except asyncio.CancelledError:
            await shutdown
            raise

    def _send(self, message: OutboundEmail) -> None:
        try:
            email = EmailMessage()
            email["From"] = self._sender
            email["To"] = message.to
            email["Subject"] = message.subject
            email["Date"] = formatdate(localtime=False)
            domain = parseaddr(self._sender)[1].rpartition("@")[2] or None
            email["Message-ID"] = make_msgid(domain=domain)
            email["Auto-Submitted"] = "auto-generated"
            email["X-Auto-Response-Suppress"] = "All"
            email.set_content(message.body, charset="utf-8")
            if message.html is not None:  # text/plain first, richest part last (RFC 2046)
                email.add_alternative(message.html, subtype="html", charset="utf-8")
            context = ssl.create_default_context()
            if self._security == "ssl":
                with smtplib.SMTP_SSL(
                    self._host, self._port, timeout=self._timeout, context=context
                ) as smtp:
                    self._deliver(smtp, email)
            else:
                with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                    smtp.starttls(context=context)
                    self._deliver(smtp, email)
        except (smtplib.SMTPException, OSError, ValueError) as error:
            raise EmailDeliveryError(classify(error)) from None

    def _deliver(self, smtp: smtplib.SMTP, email: EmailMessage) -> None:
        if self._username is not None and self._password is not None:
            smtp.login(self._username, self._password)
        smtp.send_message(email)
