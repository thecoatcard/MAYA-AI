from datetime import timedelta

from app.auth.events import LoggingAuthEvents
from app.auth.policy import AuthPolicy
from app.auth.postgres import PostgresAuthStore
from app.auth.service import AuthService
from app.core.clock import SystemClock
from app.core.config import Settings
from app.core.email import EmailSender, SmtpEmailSender
from app.core.ratelimit import RedisRateLimiter
from app.core.resources import Resources
from app.core.security import PasswordService
from app.core.security_tokens import TokenCodec


def build_email_sender(settings: Settings) -> EmailSender | None:
    if settings.smtp_host is None or settings.smtp_from_address is None:
        return None
    return SmtpEmailSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        security=settings.smtp_security,
        username=settings.smtp_username,
        password=None
        if settings.smtp_password is None
        else settings.smtp_password.get_secret_value(),
        sender=settings.smtp_from_address,
    )


def build_auth_service(settings: Settings, resources: Resources) -> AuthService:
    clock = SystemClock()
    return AuthService(
        store=PostgresAuthStore(resources.engine),
        passwords=PasswordService(),
        tokens=TokenCodec(
            settings.jwt_signing_key.get_secret_value().encode(),
            clock,
            timedelta(seconds=settings.access_token_ttl_seconds),
        ),
        otp_key=settings.otp_hmac_key.get_secret_value().encode(),
        clock=clock,
        limiter=RedisRateLimiter(resources.redis, clock),
        events=LoggingAuthEvents(),
        email_sender=build_email_sender(settings),
        policy=AuthPolicy(
            resend_cooldown=timedelta(seconds=settings.otp_resend_cooldown_seconds),
            refresh_ttl=timedelta(seconds=settings.refresh_token_ttl_seconds),
            refresh_absolute_ttl=timedelta(seconds=settings.refresh_absolute_ttl_seconds),
        ),
    )
