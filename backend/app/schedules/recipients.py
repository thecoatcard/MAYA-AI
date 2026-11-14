"""Mail recipient verification for scheduled reports.

Any address may be entered, but a scheduled mail goes only to the account's own verified e-mail
or to an address that confirmed a one-time code once. The code is six digits, valid 10 minutes,
at most 5 wrong tries, at most 3 mails per address per hour (and 6 per user per hour across
addresses). Only an HMAC of the code is stored, never the code.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from typing import Final, Protocol
from uuid import UUID

from app.auth.errors import InvalidCode, RateLimited, ServiceUnavailable
from app.core.clock import Clock
from app.core.email import EmailDeliveryError, EmailSender
from app.schedules.messages import CODE_MINUTES, recipient_code_email
from app.schedules.models import Doc, PendingCode, VerifiedRecipient
from app.schedules.store import ScheduleStore
from app.schedules.validation import ScheduleValidationError, normalise_address

logger = logging.getLogger("maya.schedules")

CODE_TTL: Final = timedelta(minutes=CODE_MINUTES)
MAX_ATTEMPTS: Final = 5
PER_ADDRESS_PER_HOUR: Final = 3
PER_USER_PER_HOUR: Final = 6
MAX_VERIFIED: Final = 20


class AccountEmails(Protocol):
    async def email_for(self, workspace_id: UUID, user_id: UUID) -> str | None:
        """The account's own e-mail when it is verified, else ``None``."""
        ...


def is_verified(doc: Doc, address: str, account_email: str | None) -> bool:
    lowered = address.strip().lower()
    if account_email and lowered == account_email.strip().lower():
        return True
    return lowered in doc.verified


class RecipientVerifier:
    def __init__(
        self,
        *,
        store: ScheduleStore,
        sender: EmailSender | None,
        clock: Clock,
        accounts: AccountEmails,
        hmac_key: bytes,
    ) -> None:
        self._store = store
        self._sender = sender
        self._clock = clock
        self._accounts = accounts
        self._key = hmac_key

    def _hash(self, workspace_id: UUID, user_id: UUID, address: str, code: str) -> str:
        message = f"{workspace_id}|{user_id}|{address}|{code}".encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    async def request(self, workspace_id: UUID, user_id: UUID, email: str) -> dict[str, bool]:
        try:
            address = normalise_address(email)
        except ValueError as error:
            raise ScheduleValidationError(str(error), "email") from None
        account = await self._accounts.email_for(workspace_id, user_id)
        doc = await self._store.load(workspace_id, user_id)
        if is_verified(doc, address, account):
            return {"sent": False, "verified": True}
        if self._sender is None:
            raise ServiceUnavailable
        now = self._clock.now()
        hour_ago = now - timedelta(hours=1)
        code = f"{secrets.randbelow(1_000_000):06d}"
        digest = self._hash(workspace_id, user_id, address, code)

        def change(current: Doc) -> int | None:
            recent_all = sum(
                1 for p in current.pending_codes.values() for t in p.sent_at if t > hour_ago
            )
            pending = current.pending_codes.get(address)
            recent = [t for t in (pending.sent_at if pending else []) if t > hour_ago]
            if len(recent) >= PER_ADDRESS_PER_HOUR or recent_all >= PER_USER_PER_HOUR:
                oldest = min(recent) if recent else now
                return max(60, int((oldest + timedelta(hours=1) - now).total_seconds()))
            current.pending_codes[address] = PendingCode(
                code_hash=digest, expires_at=now + CODE_TTL, attempts=0, sent_at=[*recent, now]
            )
            # Forget codes that can no longer be used so the document stays small.
            for key in [k for k, p in current.pending_codes.items() if p.expires_at < hour_ago]:
                del current.pending_codes[key]
            return None

        retry_after = await self._store.update(workspace_id, user_id, change)
        if retry_after is not None:
            raise RateLimited(retry_after=retry_after)
        try:
            await self._sender.send(
                recipient_code_email(to=address, code=code, account_email=account or "a MAYA user")
            )
        except EmailDeliveryError:
            logger.warning("schedule_recipient_code_not_sent")
            raise ServiceUnavailable from None
        return {"sent": True}

    async def confirm(self, workspace_id: UUID, user_id: UUID, email: str, code: str) -> bool:
        try:
            address = normalise_address(email)
        except ValueError:
            raise InvalidCode from None
        digest = self._hash(workspace_id, user_id, address, (code or "").strip())
        now = self._clock.now()

        def change(doc: Doc) -> bool:
            pending = doc.pending_codes.get(address)
            if pending is None or pending.expires_at <= now or pending.attempts >= MAX_ATTEMPTS:
                doc.pending_codes.pop(address, None)
                return False
            if not hmac.compare_digest(pending.code_hash, digest):
                pending.attempts += 1
                if pending.attempts >= MAX_ATTEMPTS:
                    doc.pending_codes.pop(address, None)
                return False
            doc.pending_codes.pop(address, None)
            if len(doc.verified) >= MAX_VERIFIED and address not in doc.verified:
                oldest = min(doc.verified, key=lambda k: doc.verified[k].verified_at)
                del doc.verified[oldest]
            doc.verified[address] = VerifiedRecipient(verified_at=now)
            return True

        ok = await self._store.update(workspace_id, user_id, change)
        if not ok:
            raise InvalidCode
        return True
