import ast
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.gateway.registry import ALLOWED_OVERRIDE_ALIASES, MODEL_ID_PATTERN

MIN_SECRET_KEY_BYTES = 32
SENDER_ADDRESS = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}$")


def _validate_secret_key(value: SecretStr) -> SecretStr:
    if len(value.get_secret_value().encode()) < MIN_SECRET_KEY_BYTES:
        raise ValueError(f"Use at least {MIN_SECRET_KEY_BYTES} bytes of random key material")
    return value


def _split_list(value: Any) -> Any:
    """Accept a JSON list or a comma-separated string for list settings."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                raise ValueError("Invalid list value") from None
            return parsed
        return [part.strip() for part in text.split(",") if part.strip()]
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
    auth_cookie_secure: bool = True
    otp_resend_cooldown_seconds: int = Field(default=60, ge=1, le=3600)

    # Deployment profile. Anything but "development" is treated as production-like: the
    # interactive API documentation is off unless explicitly enabled.
    environment: Literal["development", "production"] = "production"
    api_docs_enabled: bool | None = None
    # Hostnames the API answers to (Host header allow-list, enforced by the application).
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1"]
    )
    # Origins allowed to open the live voice WebSocket (compared to the Origin header).
    voice_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    # Directory of the exported web app. When set the API serves it (see core/static_web.py).
    web_static_dir: Path | None = None
    # Local-owner bootstrap for the desktop app (ADR-0014). Off unless explicitly enabled.
    local_owner_bootstrap: bool = False

    # Outbound e-mail for one-time codes. All optional; absent means e-mail is unavailable.
    smtp_host: str | None = Field(default=None, min_length=1, max_length=253)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, min_length=1, max_length=255)
    smtp_password: SecretStr | None = None
    smtp_from_address: str | None = Field(default=None, max_length=320)
    smtp_security: Literal["starttls", "ssl"] = "starttls"

    # Model Gateway (Phase 03)
    gemini_api_key: SecretStr | None = None
    gemini_model_overrides: Any = Field(default_factory=dict)
    gemini_verify_models_on_startup: bool = False
    gemini_request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    budget_enforcement_mode: Literal["log_only", "enforce"] = "log_only"
    gemini_budget_task_usd_alert: Decimal = Field(default=Decimal("1.50"), ge=0)
    gemini_max_tool_turns_alert: int = Field(default=10, ge=1)
    gemini_budget_task_usd_cap: Decimal | None = Field(default=None, gt=0)
    gemini_max_tool_turns_cap: int | None = Field(default=None, ge=1)

    # Chat (Phase 04). The owner approved the input, rate and context defaults on 2026-11-06.
    # The upper bound keeps a worst-case JSON body inside the 64 KiB request limit.
    chat_max_message_chars: int = Field(default=8000, ge=1, le=16_000)
    chat_rate_limit_per_minute: int = Field(default=20, ge=1, le=10_000)
    chat_context_token_budget: int = Field(default=32_000, ge=1_000, le=1_000_000)
    chat_max_concurrent_streams_per_user: int = Field(default=2, ge=1, le=100)
    chat_reply_deadline_seconds: int = Field(default=120, ge=1, le=900)
    chat_max_output_tokens: int = Field(default=2048, ge=1, le=32_768)

    @field_validator("jwt_signing_key", "otp_hmac_key")
    @classmethod
    def validate_secret_keys(cls, value: SecretStr) -> SecretStr:
        return _validate_secret_key(value)

    @field_validator("allowed_hosts", "voice_allowed_origins", mode="before")
    @classmethod
    def parse_lists(cls, value: Any) -> Any:
        return _split_list(value)

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, value: list[str]) -> list[str]:
        if not value or any(not host or "/" in host for host in value):
            raise ValueError("Use a non-empty list of host names without paths")
        return value

    @field_validator("voice_allowed_origins")
    @classmethod
    def validate_voice_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            parts = urlsplit(origin)
            if (
                parts.scheme not in ("http", "https")
                or not parts.hostname
                or parts.path not in ("", "/")
            ):
                raise ValueError("Each voice origin must look like https://host[:port]")
        return [origin.rstrip("/") for origin in value]

    @field_validator("web_static_dir", mode="before")
    @classmethod
    def empty_static_dir_is_unset(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("smtp_from_address")
    @classmethod
    def validate_sender_address(cls, value: str | None) -> str | None:
        if value is not None and SENDER_ADDRESS.fullmatch(value) is None:
            raise ValueError("Invalid sender address")
        return value

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def validate_gemini_api_key(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            if not value.strip():
                return None
            return SecretStr(value)
        if isinstance(value, SecretStr):
            if not value.get_secret_value().strip():
                return None
        return value

    @field_validator("gemini_model_overrides", mode="before")
    @classmethod
    def parse_model_overrides_before(cls, value: Any) -> Any:
        if value is None or value == "" or value == "{}":
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            s = value.strip()
            if not s or s in ("{}", "None"):
                return {}
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            cleaned = s.strip("{}").strip()
            if cleaned in ("gemini-3.5-flash-lite", "gemini-3.8-flash"):
                return {"orchestrator": cleaned}
            return {}
        return value

    @field_validator("gemini_model_overrides")
    @classmethod
    def validate_model_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        for alias, model_id in value.items():
            if alias not in ALLOWED_OVERRIDE_ALIASES:
                raise ValueError(
                    f"Invalid model override alias '{alias}'. Only enabled REST aliases "
                    f"can be overridden: {sorted(ALLOWED_OVERRIDE_ALIASES)}"
                )
            if not MODEL_ID_PATTERN.fullmatch(model_id):
                raise ValueError(f"Invalid model_id '{model_id}' for alias '{alias}' in overrides")
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

    @model_validator(mode="after")
    def validate_budget_settings(self) -> Self:
        if self.budget_enforcement_mode == "enforce":
            if self.gemini_budget_task_usd_cap is None or self.gemini_max_tool_turns_cap is None:
                raise ValueError(
                    "Configure gemini_budget_task_usd_cap and gemini_max_tool_turns_cap "
                    "when budget_enforcement_mode='enforce'"
                )
        return self

    @property
    def email_configured(self) -> bool:
        return self.smtp_host is not None

    @property
    def docs_enabled(self) -> bool:
        """Interactive docs and the OpenAPI schema: explicit setting, else development only."""
        if self.api_docs_enabled is not None:
            return self.api_docs_enabled
        return self.environment == "development"

    def fastapi_docs_kwargs(self) -> dict[str, str | None]:
        """Keyword arguments for ``FastAPI(...)`` that switch the documentation URLs on or off."""
        if self.docs_enabled:
            return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}

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
