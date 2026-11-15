"""Building and sending one plain-text message over SMTP with TLS always on.

``send`` is the only function in the application that talks SMTP for the user's mailbox. It is
called from exactly one place, the approved-execution handler (``app.mail.tools.execute_send``) and
the approved email agent run; both require a recorded approval. Port 465 is implicit TLS, any
other port uses STARTTLS; certificates are always verified; there is no plaintext mode.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Final

from app.mail.config import MailboxConfig
from app.mail.errors import MailError, map_error

SMTP_TIMEOUT_SECONDS: Final = 15.0
MAX_RECIPIENTS: Final = 10
MAX_SUBJECT_CHARS: Final = 200
MAX_BODY_CHARS: Final = 50_000
_ADDRESS = re.compile(
    r"^[^@\s<>,;\"'()\[\]\\]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$"
)
_LINEBREAK = re.compile(r"[\r\n\x00]")


@dataclass(frozen=True, slots=True)
class OutgoingMail:
    to: tuple[str, ...]
    subject: str
    body: str
    cc: tuple[str, ...] = ()
    in_reply_to: str = ""
    references: str = ""


def valid_address(value: str) -> str:
    """One well-formed address, or a ``MailError``. Header-injection characters never pass."""
    cleaned = (value or "").strip()
    if not cleaned or _ADDRESS.match(cleaned) is None:
        raise MailError(
            f"'{cleaned[:80]}' is not a single valid email address.", "invalid_recipient"
        )
    return cleaned


_ESCAPED_BREAK: Final = re.compile(r"\\r\\n|\\n|\\r")


def fix_escaped_text(value: str) -> str:
    """Turn escaped line breaks into real ones.

    Models sometimes write a message's line breaks as the two characters backslash and ``n``
    (an escaped JSON string that was escaped twice), so a mail arrives as one long paragraph
    with a visible ``\\n\\n`` in it. This converts them only when the text is clearly escaped:
    at least two of them and no (or far fewer) real line breaks, so a Windows path such as
    ``C:\\new`` is left alone.
    """
    escaped = len(_ESCAPED_BREAK.findall(value or ""))
    if escaped < 2:
        return value
    real = value.count("\n")
    if real > 0 and escaped <= real * 2:
        return value
    return _ESCAPED_BREAK.sub("\n", value)


def clean_subject(value: str) -> str:
    return " ".join(_LINEBREAK.sub(" ", fix_escaped_text(value or "")).split())


def build_message(config: MailboxConfig, mail: OutgoingMail) -> EmailMessage:
    """The RFC 5322 message: From is the configured mailbox, Date and Message-ID are set, the
    subject is RFC 2047 encoded when it is not ASCII (Hindi, Hinglish), the body is UTF-8."""
    to = tuple(valid_address(a) for a in mail.to)
    cc = tuple(valid_address(a) for a in mail.cc)
    if not to:
        raise MailError("The email has no recipient.", "invalid_recipient")
    if len(to) + len(cc) > MAX_RECIPIENTS:
        raise MailError(f"At most {MAX_RECIPIENTS} recipients per email.", "too_many_recipients")
    subject = clean_subject(mail.subject)
    if not subject or not mail.body.strip():
        raise MailError("The email needs a subject and a body.", "empty_message")
    if len(subject) > MAX_SUBJECT_CHARS or len(mail.body) > MAX_BODY_CHARS:
        raise MailError("The email is too long. Shorten it.", "message_too_long")
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = config.address
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=config.address.rpartition("@")[2] or None)
    if mail.in_reply_to:
        message["In-Reply-To"] = clean_subject(mail.in_reply_to)
    if mail.references:
        message["References"] = clean_subject(mail.references)
    body = fix_escaped_text(mail.body).replace("\r\n", "\n")
    if body.isascii():
        message.set_content(body)
    else:
        message.set_content(body, charset="utf-8", cte="base64")
    return message


def send(config: MailboxConfig, message: EmailMessage, recipients: Sequence[str]) -> list[str]:
    """Deliver to the SMTP server (blocking: run it in a thread). Returns the recipients the
    server refused while accepting others (usually empty); raises ``MailError`` otherwise."""
    context = ssl.create_default_context()
    try:
        if config.smtp_port == 465:
            with smtplib.SMTP_SSL(
                config.smtp_host, config.smtp_port, timeout=SMTP_TIMEOUT_SECONDS, context=context
            ) as smtp:
                refused = _deliver(smtp, config, message, recipients)
        else:
            with smtplib.SMTP(
                config.smtp_host, config.smtp_port, timeout=SMTP_TIMEOUT_SECONDS
            ) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                refused = _deliver(smtp, config, message, recipients)
    except Exception as error:
        raise map_error(error, config, action="send") from None
    return sorted(refused)


def _deliver(
    smtp: smtplib.SMTP, config: MailboxConfig, message: EmailMessage, recipients: Sequence[str]
) -> dict[str, tuple[int, bytes]]:
    smtp.login(config.address, config.password)
    return smtp.send_message(message, from_addr=config.address, to_addrs=list(recipients))
