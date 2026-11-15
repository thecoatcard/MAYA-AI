"""The mailbox as one async service: list, search, read, reply context, send and sent-check.

Every call opens one short-lived IMAP or SMTP session in a worker thread (blocking stdlib code
never runs on the event loop), bounded by a 15 second socket timeout and a process-wide session
limit. A rate-limit or timeout failure of a READ is retried once; a send is never retried.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
from typing import Any, Final, TypeVar

from app.mail import smtp
from app.mail.config import MailboxConfig
from app.mail.errors import STALE_REF, MailError, map_error
from app.mail.imap import (
    LOGICAL_FOLDERS,
    Criteria,
    FullMessage,
    ImapSession,
    Summary,
    build_criteria,
    logical_folder,
    quote,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")
MAX_LIMIT: Final = 25
ATTACHMENT_SCAN_WINDOW: Final = 50
ATTACHMENT_SCAN_MAX: Final = 200
_REF_PREFIX: Final = "mref_"
# Waits (seconds) between the checks of the Sent folder after a send: short and bounded, because
# the approved step runs under a time limit.
SENT_CHECK_WAITS: Final = (1.0, 2.0)
SENT_CHECK_TIMEOUT: Final = 6.0
SENT_CHECK_BUDGET: Final = 12.0


def encode_ref(folder: str, validity: int, uid: int) -> str:
    raw = f"v1|{folder}|{validity}|{uid}".encode()
    return _REF_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_ref(ref: str) -> tuple[str, int, int]:
    """(logical folder, UIDVALIDITY, UID). A malformed or forged ref is a plain 'stale' error."""
    value = (ref or "").strip()
    if not value.startswith(_REF_PREFIX) or len(value) > 200:
        raise MailError(STALE_REF, "stale_ref")
    try:
        body = value[len(_REF_PREFIX) :]
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("ascii")
        version, folder, validity, uid = raw.split("|")
        if version != "v1" or folder not in LOGICAL_FOLDERS:
            raise ValueError("bad ref")
        return folder, int(validity), int(uid)
    except (ValueError, UnicodeError):
        raise MailError(STALE_REF, "stale_ref") from None


@dataclass(slots=True)
class MailPage:
    folder: str
    validity: int
    items: list[Summary]
    next_before_uid: int | None
    scanned_all: bool = True

    def ref(self, item: Summary) -> str:
        return encode_ref(self.folder, self.validity, item.uid)


@dataclass(slots=True)
class MailRead:
    folder: str
    full: FullMessage

    @property
    def headers(self) -> Message[str, Any] | None:
        return self.full.message if self.full.message is not None else self.full.headers


@dataclass(frozen=True, slots=True)
class SendOutcome:
    message_id: str
    recipients: tuple[str, ...]
    refused: tuple[str, ...]
    in_sent: bool | None  # True found, False not found, None could not be checked


class MailService:
    def __init__(
        self,
        config: MailboxConfig,
        *,
        sent_check_waits: tuple[float, ...] = SENT_CHECK_WAITS,
    ) -> None:
        self.config = config
        self._waits = sent_check_waits

    # ------------------------------------------------------------------------------ plumbing

    async def _read(self, fn: Callable[[], T]) -> T:
        """Run a blocking read in a thread; retry once on a retryable failure."""
        for attempt in (0, 1):
            try:
                return await asyncio.to_thread(fn)
            except MailError as error:
                if error.retryable and attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
                raise
            except Exception as error:  # noqa: BLE001 - mapped to one plain sentence
                mapped = map_error(error, self.config)
                if mapped.retryable and attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
                raise mapped from None
        raise MailError("The mail request failed.")  # pragma: no cover - loop always returns

    # ------------------------------------------------------------------------------- reading

    async def list_page(
        self,
        folder: str,
        criteria: Criteria,
        *,
        limit: int,
        before_uid: int | None = None,
        require_attachment: bool = False,
    ) -> MailPage:
        logical = logical_folder(folder)
        limit = max(1, min(MAX_LIMIT, limit))
        return await self._read(
            lambda: self._list(logical, criteria, limit, before_uid, require_attachment)
        )

    def _list(
        self,
        logical: str,
        criteria: Criteria,
        limit: int,
        before_uid: int | None,
        require_attachment: bool,
    ) -> MailPage:
        with ImapSession(self.config) as session:
            _name, validity = session.select(logical)
            uids = session.search(criteria)
            if before_uid:
                uids = [u for u in uids if u < before_uid]
            newest_first = uids[::-1]
            if not require_attachment:
                page = newest_first[:limit]
                items = session.summaries(page)
                more = len(newest_first) > len(page)
                return MailPage(logical, validity, items, page[-1] if more and page else None)
            kept: list[Summary] = []
            scanned = 0
            last_seen: int | None = None
            for start in range(
                0, min(len(newest_first), ATTACHMENT_SCAN_MAX), ATTACHMENT_SCAN_WINDOW
            ):
                window = newest_first[start : start + ATTACHMENT_SCAN_WINDOW]
                for item in session.summaries(window, structure=True):
                    scanned += 1
                    last_seen = item.uid
                    if item.has_attachment:
                        kept.append(item)
                        if len(kept) == limit:
                            break
                if len(kept) == limit:
                    break
            more = last_seen is not None and any(u < last_seen for u in newest_first)
            return MailPage(
                logical, validity, kept, last_seen if more else None, scanned_all=not more
            )

    async def read(self, ref: str) -> MailRead:
        logical, validity, uid = decode_ref(ref)

        def run() -> MailRead:
            with ImapSession(self.config) as session:
                _name, current = session.select(logical)
                if current != validity:
                    raise MailError(STALE_REF, "stale_ref")
                full = session.message(uid)
                if full.message is None and not full.too_large:
                    raise MailError(STALE_REF, "stale_ref")
                return MailRead(logical, full)

        return await self._read(run)

    # -------------------------------------------------------------------------------- sending

    async def send(self, mail: smtp.OutgoingMail) -> SendOutcome:
        """Deliver ``mail`` and, if possible, find it in the Sent folder. Never retried.

        Call this only from the approved-execution path."""
        message = smtp.build_message(self.config, mail)
        recipients = (*mail.to, *mail.cc)
        refused = await asyncio.to_thread(smtp.send, self.config, message, recipients)
        message_id = str(message["Message-ID"])
        in_sent = await self._confirm_in_sent(message_id)
        return SendOutcome(message_id, tuple(recipients), tuple(refused), in_sent)

    async def _confirm_in_sent(self, message_id: str) -> bool | None:
        """True when the message is in the Sent folder, False when it is not there after a short
        bounded wait, None when the check itself failed. Never raises."""

        def look() -> bool:
            with ImapSession(self.config, timeout=SENT_CHECK_TIMEOUT) as session:
                session.select("sent")
                found = session.search(
                    Criteria(("HEADER", "Message-ID", quote(message_id.strip())))
                )
                return bool(found)

        try:
            async with asyncio.timeout(SENT_CHECK_BUDGET):
                for wait in (0.0, *self._waits):
                    if wait:
                        await asyncio.sleep(wait)
                    if await asyncio.to_thread(look):
                        return True
            return False
        except Exception as error:  # noqa: BLE001 - the send already happened; only the check failed
            logger.info("mail_sent_check_failed error=%s", type(error).__name__)
            return None


def safe_reference(value: str) -> str:
    """A Message-ID / References value as one line (no control characters)."""
    return re.sub(r"[\x00-\x1f\x7f]", " ", value or "").strip()


__all__ = [
    "MailPage",
    "MailRead",
    "MailService",
    "SendOutcome",
    "build_criteria",
    "decode_ref",
    "encode_ref",
    "safe_reference",
]
