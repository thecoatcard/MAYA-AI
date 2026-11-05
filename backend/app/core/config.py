from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


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
        if value is None:
            return None
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

    @model_validator(mode="after")
    def separate_database_roles(self) -> Self:
        if self.migration_database_url is not None:
            runtime = make_url(self.database_url.get_secret_value())
            migration = make_url(self.migration_database_url.get_secret_value())
            if runtime.username == migration.username:
                raise ValueError("Runtime and migration database roles must differ")
        return self
