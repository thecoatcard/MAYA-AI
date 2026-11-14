"""Builds the scheduler services from the application's resources (used by the lifespan and CLI)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.factory import build_email_sender
from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.core.email import EmailSender, SmtpEmailSender
from app.db.repositories.user_settings import UserSettingsStore
from app.db.tenant import tenant_session
from app.schedules.locks import LocalRunLock, RedisRunLock, RunLock
from app.schedules.recipients import RecipientVerifier
from app.schedules.runner import RunSubmitter, ScheduleRunner
from app.schedules.service import ScheduleService
from app.schedules.store import ScheduleStore, UserIndex
from app.schedules.task_probe import DbTaskProbe

_ACCOUNT = text("SELECT email, email_verified_at FROM users WHERE id = :user_id")


class DbAccountEmails:
    """The account's own e-mail, only when verified (it is then pre-verified as a recipient)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def email_for(self, workspace_id: UUID, user_id: UUID) -> str | None:
        try:
            async with tenant_session(self._engine, workspace_id, user_id) as session:
                row = (await session.execute(_ACCOUNT, {"user_id": user_id})).first()
        except Exception:  # noqa: BLE001 - unreadable means "not pre-verified"
            return None
        if row is None or row.email_verified_at is None:
            return None
        return str(row.email)


@dataclass
class ScheduleServices:
    service: ScheduleService
    verifier: RecipientVerifier
    accounts: DbAccountEmails
    sender: EmailSender | None
    runner: ScheduleRunner | None = None

    async def aclose(self) -> None:
        if self.runner is not None:
            await self.runner.stop()
        if isinstance(self.sender, SmtpEmailSender):
            await self.sender.aclose()


def build_services(
    settings: Settings,
    engine: AsyncEngine,
    redis: Any | None,
    *,
    clock: Clock | None = None,
) -> ScheduleServices:
    """The service and verifier (always) with MAYA's own system mailer (``MAYA_SMTP_*``)."""
    clock = clock or SystemClock()
    store = ScheduleStore(UserSettingsStore(engine), UserIndex(redis))
    service = ScheduleService(store, clock)
    sender = build_email_sender(settings)
    accounts = DbAccountEmails(engine)
    verifier = RecipientVerifier(
        store=store,
        sender=sender,
        clock=clock,
        accounts=accounts,
        hmac_key=settings.otp_hmac_key.get_secret_value().encode(),
    )
    return ScheduleServices(service=service, verifier=verifier, accounts=accounts, sender=sender)


def attach_runner(
    services: ScheduleServices,
    settings: Settings,
    engine: AsyncEngine,
    redis: Any | None,
    submitter: RunSubmitter,
    *,
    clock: Clock | None = None,
    lock: RunLock | None = None,
) -> ScheduleRunner:
    """Create the runner (not started). A Redis lock when Redis exists, else an in-process one."""
    runner = ScheduleRunner(
        service=services.service,
        submitter=submitter,
        probe=DbTaskProbe(engine),
        accounts=services.accounts,
        sender=services.sender,
        lock=lock or (RedisRunLock(redis) if redis is not None else LocalRunLock()),
        clock=clock or SystemClock(),
        poll_seconds=settings.schedules_poll_seconds,
        max_minutes=settings.schedules_run_max_minutes,
    )
    services.runner = runner
    return runner
