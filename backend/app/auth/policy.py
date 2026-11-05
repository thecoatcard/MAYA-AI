from dataclasses import dataclass, field
from datetime import timedelta

from app.core.ratelimit import Limit


@dataclass(frozen=True)
class RateLimits:
    """Initial values chosen by the owner's plan approval; ratchet them from measured traffic."""

    send_per_email: Limit = Limit(5, 3600)
    send_per_address: Limit = Limit(20, 3600)
    attempt_per_email: Limit = Limit(20, 3600)
    attempt_per_email_daily: Limit = Limit(50, 86400)
    attempt_per_address: Limit = Limit(60, 3600)
    # Counted before the password is checked (so parallel guesses cannot all slip through) and
    # given back when the password turns out to be right.
    login_attempts_per_account_and_address: Limit = Limit(10, 900)
    login_attempts_per_address: Limit = Limit(50, 900)


@dataclass(frozen=True)
class AuthPolicy:
    resend_cooldown: timedelta
    refresh_ttl: timedelta
    refresh_absolute_ttl: timedelta
    limits: RateLimits = field(default_factory=RateLimits)
