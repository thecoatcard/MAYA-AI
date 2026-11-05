import re
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

from app.auth.events import AuthEvents
from app.auth.models import TokenPair
from app.auth.policy import AuthPolicy, RateLimits
from app.auth.service import AuthService, RegisterCommand
from app.auth.store import AuthStore
from app.core.clock import Clock
from app.core.email import OutboundEmail
from app.core.ratelimit import Limit, RateLimiterUnavailable
from app.core.security import PasswordService
from app.core.security_tokens import TokenCodec
from tests.support.clock import ManualClock
from tests.support.passwords import fast_password_service

OTP_KEY = b"o" * 32
JWT_KEY = b"j" * 32
GOOD_PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "another sufficiently long passphrase"
ADDRESS = "203.0.113.7"
CODE_PATTERN = re.compile(r"is (\d{6})\.")


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[OutboundEmail] = []
        self.fail = False

    async def send(self, message: OutboundEmail) -> None:
        if self.fail:
            raise RuntimeError("smtp is down")
        self.sent.append(message)

    def codes_for(self, address: str) -> list[str]:
        return [
            match.group(1)
            for message in self.sent
            if message.to == address
            if (match := CODE_PATTERN.search(message.body)) is not None
        ]


class FakeRateLimiter:
    """Same fixed-window arithmetic as the Redis limiter, driven by the manual clock."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._counts: dict[str, int] = {}
        self.unavailable = False

    def _name(self, key: str, limit: Limit) -> str:
        return f"{key}:{int(self._clock.now().timestamp()) // limit.window_seconds}"

    async def hit(self, key: str, limit: Limit) -> bool:
        if self.unavailable:
            raise RateLimiterUnavailable
        name = self._name(key, limit)
        self._counts[name] = self._counts.get(name, 0) + 1
        return self._counts[name] <= limit.count

    async def release(self, key: str, limit: Limit) -> None:
        if self.unavailable:
            raise RateLimiterUnavailable
        name = self._name(key, limit)
        self._counts[name] = max(0, self._counts.get(name, 0) - 1)

    def count(self, key_prefix: str) -> int:
        return sum(count for name, count in self._counts.items() if name.startswith(key_prefix))


class RecordingEvents:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str | None]] = []

    def emit(self, event: str, *, outcome: str, reason: str | None = None) -> None:
        self.events.append((event, outcome, reason))


@dataclass
class Harness:
    service: AuthService
    store: AuthStore
    clock: ManualClock
    email: FakeEmailSender
    limiter: FakeRateLimiter
    events: RecordingEvents
    tokens: TokenCodec
    passwords: PasswordService
    policy: AuthPolicy
    _counter: int = field(default=0)

    def identity(self) -> tuple[str, str]:
        """A username and e-mail address that no other test in the database uses."""
        suffix = uuid4().hex[:12]
        return f"user_{suffix}", f"user.{suffix}@example.com"

    def command(self, **overrides: str | None) -> RegisterCommand:
        username, email = self.identity()
        values: dict[str, str | None] = {
            "username": username,
            "email": email,
            "password": GOOD_PASSWORD,
            "full_name": "Test Person",
            "workspace_name": None,
        }
        values.update(overrides)
        return RegisterCommand(**values)  # type: ignore[arg-type]

    async def register(self, **overrides: str | None) -> RegisterCommand:
        """Register and deliver the code, as the route's background task would."""
        command = self.command(**overrides)
        message = await self.service.register(command, ADDRESS)
        assert message is not None
        await self.service.deliver(message)
        return command

    async def register_and_verify(self, **overrides: str | None) -> RegisterCommand:
        command = await self.register(**overrides)
        code = self.email.codes_for(command.email)[-1]
        await self.service.verify_email(command.email, code, command.password, ADDRESS)
        return command

    async def login(self, command: RegisterCommand) -> TokenPair:
        return await self.service.login(command.username, command.password, ADDRESS)


def build_harness(
    store: AuthStore,
    *,
    email: bool = True,
    passwords: PasswordService | None = None,
    limits: RateLimits | None = None,
    events: AuthEvents | None = None,
) -> Harness:
    clock = ManualClock()
    sender = FakeEmailSender()
    limiter = FakeRateLimiter(clock)
    recording = RecordingEvents()
    tokens = TokenCodec(JWT_KEY, clock, timedelta(minutes=15))
    hasher = passwords or fast_password_service()
    policy = AuthPolicy(
        resend_cooldown=timedelta(seconds=60),
        refresh_ttl=timedelta(days=7),
        refresh_absolute_ttl=timedelta(days=30),
        limits=limits or RateLimits(),
    )
    service = AuthService(
        store=store,
        passwords=hasher,
        tokens=tokens,
        otp_key=OTP_KEY,
        clock=clock,
        limiter=limiter,
        events=events or recording,
        email_sender=sender if email else None,
        policy=policy,
    )
    return Harness(service, store, clock, sender, limiter, recording, tokens, hasher, policy)
