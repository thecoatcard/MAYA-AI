"""Offline fakes for the scheduler tests: settings backend, clock, mailer, submitter, task probe."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.core.email import EmailDeliveryError, OutboundEmail
from app.schedules.factory import ScheduleServices
from app.schedules.locks import LocalRunLock
from app.schedules.recipients import RecipientVerifier
from app.schedules.runner import ScheduleRunner
from app.schedules.service import ScheduleService
from app.schedules.store import ScheduleStore, UserIndex
from app.schedules.task_probe import TaskOutcome

WS = UUID(int=1)
USER = UUID(int=2)
ACCOUNT = "owner@example.com"


class FakeBackend:
    """In-memory user_settings with the same compare-and-set contract."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.fail_next_cas = 0

    async def get_text(self, workspace_id: UUID, user_id: UUID, key: str) -> str | None:
        return self.data.get(key)

    async def compare_and_set_text(
        self, workspace_id: UUID, user_id: UUID, key: str, expected: str | None, value: str
    ) -> bool:
        if self.fail_next_cas:
            self.fail_next_cas -= 1
            return False
        if self.data.get(key) != expected:
            return False
        self.data[key] = value
        return True


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


class FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[OutboundEmail] = []
        self.fail = fail

    async def send(self, message: OutboundEmail) -> None:
        if self.fail:
            raise EmailDeliveryError("smtp")
        self.sent.append(message)


class FakeAccounts:
    def __init__(self, email: str | None = ACCOUNT) -> None:
        self.email = email

    async def email_for(self, workspace_id: UUID, user_id: UUID) -> str | None:
        return self.email


@dataclass
class FakeSubmitter:
    calls: list[dict[str, Any]] = field(default_factory=list)
    refuse: bool = False
    raise_error: bool = False

    async def submit(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.raise_error:
            raise RuntimeError("boom")
        if self.refuse:
            return type("R", (), {"ok": False, "reason": "Not supported here."})()
        return type("S", (), {"task_id": uuid4(), "ok": True})()


class FakeProbe:
    def __init__(self) -> None:
        self.outcome = TaskOutcome("running")
        self.cancelled: list[str] = []

    async def check(self, workspace_id: UUID, user_id: UUID, task_id: str) -> TaskOutcome:
        return self.outcome

    async def cancel(self, workspace_id: UUID, user_id: UUID, task_id: str) -> None:
        self.cancelled.append(task_id)


@dataclass
class Rig:
    backend: FakeBackend
    clock: FakeClock
    store: ScheduleStore
    service: ScheduleService
    sender: FakeSender | None
    accounts: FakeAccounts
    submitter: FakeSubmitter
    probe: FakeProbe
    runner: ScheduleRunner
    verifier: RecipientVerifier
    services: ScheduleServices


def make_rig(start: datetime | None = None, *, sender: FakeSender | None | bool = True) -> Rig:
    backend = FakeBackend()
    clock = FakeClock(start or datetime(2026, 11, 13, 7, 0, tzinfo=UTC))
    store = ScheduleStore(backend, UserIndex())
    service = ScheduleService(store, clock)
    mailer: FakeSender | None = FakeSender() if sender is True else (sender or None)
    accounts = FakeAccounts()
    submitter, probe = FakeSubmitter(), FakeProbe()
    runner = ScheduleRunner(
        service=service,
        submitter=submitter,
        probe=probe,
        accounts=accounts,
        sender=mailer,
        lock=LocalRunLock(),
        clock=clock,
        max_minutes=15,
    )
    verifier = RecipientVerifier(
        store=store, sender=mailer, clock=clock, accounts=accounts, hmac_key=b"k" * 32
    )
    services = ScheduleServices(
        service=service,
        verifier=verifier,
        accounts=accounts,  # type: ignore[arg-type]
        sender=mailer,
        runner=runner,
    )
    return Rig(
        backend,
        clock,
        store,
        service,
        mailer,
        accounts,
        submitter,
        probe,
        runner,
        verifier,
        services,
    )
