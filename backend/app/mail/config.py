"""Mailbox settings as one immutable object. The app password lives only here and in the login
call; it is never in ``repr``, in a log line, in a tool result or in an error."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class MailboxConfig:
    address: str
    password: str = field(repr=False)
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465

    @property
    def is_gmail(self) -> bool:
        return self.imap_host.lower().endswith(("gmail.com", "googlemail.com"))

    def scrub(self, text: str) -> str:
        """``text`` with the app password (and its grouped form) removed. Defence in depth."""
        if not text or not self.password:
            return text
        out = text.replace(self.password, "[removed]")
        grouped = " ".join(self.password[i : i + 4] for i in range(0, len(self.password), 4))
        return out.replace(grouped, "[removed]")


def mailbox_from_settings(settings: Settings) -> MailboxConfig | None:
    """The configured mailbox, or ``None`` when it is unset or switched off (MAYA_MAIL_ENABLED)."""
    password = settings.mail_app_password
    address = settings.mail_address
    if address is None or password is None or settings.mail_enabled is False:
        return None
    secret = password.get_secret_value()
    if not secret:
        return None
    return MailboxConfig(
        address=address,
        password=secret,
        imap_host=settings.mail_imap_host,
        imap_port=settings.mail_imap_port,
        smtp_host=settings.mail_smtp_host,
        smtp_port=settings.mail_smtp_port,
    )


def mask_address(address: str) -> str:
    """``a***@gmail.com``: enough to recognise the account, never the whole address."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"
