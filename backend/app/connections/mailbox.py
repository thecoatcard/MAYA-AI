"""The mailbox as a per-user connection: input checks, provider presets and the live test.

The credential is one document (address, app password, hosts, ports) sealed as a single AES-GCM
blob by ``ConnectionService`` in the caller's ``user_settings`` row: no schema change. This module
only validates input and performs the test. The test logs in over IMAP and authenticates over SMTP
(EHLO, TLS, LOGIN) and quits: it never sends a message and never selects or reads a folder.

Hosts come from the user (Custom preset), so they are checked: a plain DNS name or a public IP
literal only, never localhost, a private/loopback/link-local address or an internal-looking name.
Ports are limited to the TLS ports of IMAP (993) and SMTP (465 implicit TLS, 587 STARTTLS).
Every failure is the plain sentence from ``app.mail.errors``; the password is never in a message.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from app.connections.providers import ConnectionInputError, TestResult
from app.mail.config import MailboxConfig, mask_address
from app.mail.errors import MailError, map_error
from app.mail.imap import ImapSession

# Presets the web form offers (it carries the same table; the server re-validates every field).
MAIL_PRESETS: Final[dict[str, tuple[str, int, str, int]]] = {
    "gmail": ("imap.gmail.com", 993, "smtp.gmail.com", 465),
    "outlook": ("outlook.office365.com", 993, "smtp.office365.com", 587),
    "yahoo": ("imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465),
    "icloud": ("imap.mail.me.com", 993, "smtp.mail.me.com", 587),
}
IMAP_PORTS: Final = (993,)
SMTP_PORTS: Final = (465, 587)
_TIMEOUT: Final = 15.0
_ADDRESS = re.compile(
    r"^[^@\s<>,;\"'()\[\]\\]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$"
)
_HOST = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$"
)
_INTERNAL_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home", ".corp", ".intranet")


def normalize_app_password(raw: str) -> str:
    """Google shows an app password in groups with spaces; the server wants it without."""
    value = "".join((raw or "").split())
    if not value:
        raise ConnectionInputError("password_missing", "Paste your app password first.")
    if not value.isascii() or len(value) > 256:
        raise ConnectionInputError(
            "password_format",
            "That does not look like an app password (odd characters). Copy it again.",
        )
    return value


def normalize_address(raw: str) -> str:
    value = (raw or "").strip()
    if not value or _ADDRESS.match(value) is None or len(value) > 320:
        raise ConnectionInputError(
            "address_format", "Enter the full email address of the mailbox, like name@gmail.com."
        )
    return value


def normalize_host(raw: str, *, label: str) -> str:
    value = (raw or "").strip().lower().rstrip(".")
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        ip = None
    if ip is not None:
        if not ip.is_global:
            raise ConnectionInputError(
                "host_forbidden",
                f"The {label} server cannot be a private or local address. Use the provider's "
                "public host name.",
            )
        return value
    if _HOST.match(value) is None or value.endswith(_INTERNAL_SUFFIXES) or value == "localhost":
        raise ConnectionInputError(
            "host_format",
            f"Enter the {label} server as a public host name, like imap.example.com.",
        )
    return value


def normalize_port(raw: int | None, allowed: tuple[int, ...], *, label: str) -> int:
    if raw is None or raw not in allowed:
        ports = " or ".join(str(p) for p in allowed)
        raise ConnectionInputError("port_format", f"The {label} port must be {ports}.")
    return int(raw)


@dataclass(frozen=True, slots=True)
class MailboxInput:
    address: str
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int


def validate_mailbox_input(
    *,
    address: str,
    imap_host: str | None,
    imap_port: int | None,
    smtp_host: str | None,
    smtp_port: int | None,
) -> MailboxInput:
    """All fields checked together; the first problem raises ``ConnectionInputError``."""
    return MailboxInput(
        address=normalize_address(address),
        imap_host=normalize_host(imap_host or MAIL_PRESETS["gmail"][0], label="incoming (IMAP)"),
        imap_port=normalize_port(imap_port or IMAP_PORTS[0], IMAP_PORTS, label="incoming (IMAP)"),
        smtp_host=normalize_host(smtp_host or MAIL_PRESETS["gmail"][2], label="outgoing (SMTP)"),
        smtp_port=normalize_port(smtp_port or 465, SMTP_PORTS, label="outgoing (SMTP)"),
    )


# ------------------------------------------------------------------------------------- the test


def _check_imap(config: MailboxConfig) -> None:
    with ImapSession(config, timeout=_TIMEOUT):
        return  # the login in __enter__ is the whole check


def _check_smtp(config: MailboxConfig) -> None:
    """EHLO, TLS and LOGIN, then QUIT. No MAIL FROM, so nothing can be sent."""
    context = ssl.create_default_context()
    try:
        if config.smtp_port == 465:
            with smtplib.SMTP_SSL(
                config.smtp_host, config.smtp_port, timeout=_TIMEOUT, context=context
            ) as smtp:
                smtp.ehlo()
                smtp.login(config.address, config.password)
        else:
            with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=_TIMEOUT) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(config.address, config.password)
    except Exception as error:
        raise map_error(error, config, action="login") from None


async def _step(fn: Callable[[MailboxConfig], None], config: MailboxConfig) -> tuple[bool, str]:
    try:
        await asyncio.to_thread(fn, config)
    except MailError as error:
        return False, config.scrub(str(error))
    except Exception as error:  # noqa: BLE001 - one plain sentence, never the raw error
        return False, config.scrub(str(map_error(error, config, action="login")))
    return True, "Signed in."


async def check_mailbox(config: MailboxConfig) -> TestResult:
    """Test incoming and outgoing separately so the user sees which half needs fixing."""
    imap_ok, imap_msg = await _step(_check_imap, config)
    smtp_ok, smtp_msg = await _step(_check_smtp, config)
    steps = (("Incoming (IMAP)", imap_ok, imap_msg), ("Outgoing (SMTP)", smtp_ok, smtp_msg))
    if imap_ok and smtp_ok:
        return TestResult(
            True,
            "ok",
            f"Connected: MAYA can read and send as {mask_address(config.address)}.",
            account=mask_address(config.address),
            steps=steps,
        )
    first_failed = next(msg for _label, ok, msg in steps if not ok)
    code = "imap_failed" if not imap_ok else "smtp_failed"
    return TestResult(False, code, first_failed, account=mask_address(config.address), steps=steps)
