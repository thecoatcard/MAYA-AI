import re
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

MIN_SECRET_KEY_BYTES = 32
SENDER_ADDRESS = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}$")


def _validate_secret_key(value: SecretStr) -> SecretStr:
    if len(value.get_secret_value().encode()) < MIN_SECRET_KEY_BYTES:
        raise ValueError(f"Use at least {MIN_SECRET_KEY_BYTES} bytes of random key material")
    return value


def _validate_migration_url(value: SecretStr) -> SecretStr:
    try:
        url = make_url(value.get_secret_value())
        valid = (
            url.drivername == "postgresql+asyncpg"
            and bool(url.username and url.password and url.host and url.database)
            and (url.port is None or 0 < url.port <= 65535)
        )
    except (ArgumentError, ValueError):
        raise ValueError("Invalid migration database URL") from None
    if not valid:
        raise ValueError("Invalid migration database URL")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MAYA_",
        env_file=None,
        extra="ignore",
        hide_input_in_errors=True,
    )

    database_url: SecretStr
    redis_url: SecretStr
    migration_database_url: SecretStr | None = None
    probe_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # Authentication. The two keys protect different things and must differ.
    jwt_signing_key: SecretStr
    otp_hmac_key: SecretStr
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_seconds: int = Field(default=604_800, ge=3600, le=2_592_000)
    refresh_absolute_ttl_seconds: int = Field(default=2_592_000, ge=3600, le=7_776_000)
    otp_resend_cooldown_seconds: int = Field(default=60, ge=1, le=3600)

    # Outbound e-mail for one-time codes. All optional; absent means e-mail is unavailable.
    smtp_host: str | None = Field(default=None, min_length=1, max_length=253)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, min_length=1, max_length=255)
    smtp_password: SecretStr | None = None
    smtp_from_address: str | None = Field(default=None, max_length=320)
    smtp_security: Literal["starttls", "ssl"] = "starttls"

    @field_validator("jwt_signing_key", "otp_hmac_key")
    @classmethod
    def validate_secret_keys(cls, value: SecretStr) -> SecretStr:
        return _validate_secret_key(value)

    @field_validator("smtp_from_address")
    @classmethod
    def validate_sender_address(cls, value: str | None) -> str | None:
        if value is not None and SENDER_ADDRESS.fullmatch(value) is None:
            raise ValueError("Invalid sender address")
        return value

    @model_validator(mode="after")
    def validate_authentication_settings(self) -> Self:
        if self.jwt_signing_key.get_secret_value() == self.otp_hmac_key.get_secret_value():
            raise ValueError("The JWT and OTP keys must be different")
        if self.refresh_absolute_ttl_seconds < self.refresh_token_ttl_seconds:
            raise ValueError("The absolute session lifetime must cover the refresh lifetime")
        if (self.smtp_host is None) != (self.smtp_from_address is None):
            raise ValueError("Configure the SMTP host and sender address together")
        if (self.smtp_username is None) != (self.smtp_password is None):
            raise ValueError("Configure the SMTP username and password together")
        return self

    @property
    def email_configured(self) -> bool:
        return self.smtp_host is not None

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            valid = (
                url.drivername == "postgresql+asyncpg"
                and url.username not in (None, "", "postgres")
                and bool(url.password and url.host and url.database)
                and (url.port is None or 0 < url.port <= 65535)
            )
        except (ArgumentError, ValueError):
            raise ValueError("Invalid runtime database URL") from None
        if not valid:
            raise ValueError("Use an asyncpg URL with a non-admin application role")
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = urlsplit(value.get_secret_value())
            valid = url.scheme in ("redis", "rediss") and bool(url.hostname)
            valid = valid and (url.port is None or 0 < url.port <= 65535)
            valid = valid and not url.query and not url.fragment
            valid = valid and (url.path in ("", "/") or url.path.removeprefix("/").isdigit())
        except ValueError:
            raise ValueError("Invalid Redis URL") from None
        if not valid:
            raise ValueError("Use a redis or rediss connection URL")
        return value

    @field_validator("migration_database_url")
    @classmethod
    def validate_migration_url(cls, value: SecretStr | None) -> SecretStr | None:
        return None if value is None else _validate_migration_url(value)

    @model_validator(mode="after")
    def separate_database_roles(self) -> Self:
        if self.migration_database_url is not None:
            runtime = make_url(self.database_url.get_secret_value())
            migration = make_url(self.migration_database_url.get_secret_value())
            if runtime.username == migration.username:
                raise ValueError("Runtime and migration database roles must differ")
        return self


class MigrationSettings(BaseSettings):
    """Only what the schema migration tool needs; the API never uses these credentials."""

    model_config = SettingsConfigDict(
        env_prefix="MAYA_",
        env_file=None,
        extra="ignore",
        hide_input_in_errors=True,
    )

    migration_database_url: SecretStr

    @field_validator("migration_database_url")
    @classmethod
    def validate_migration_url(cls, value: SecretStr) -> SecretStr:
        return _validate_migration_url(value)
