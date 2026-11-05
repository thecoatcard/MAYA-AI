from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import MigrationSettings, Settings
from tests.support.settings import DATABASE_URL, JWT_KEY, OTP_KEY, REDIS_URL, make_settings

MIGRATION_URL = "postgresql+asyncpg://maya_migrator:example-migration@localhost:5432/maya"


def test_settings_hide_connection_values() -> None:
    settings = make_settings()
    assert "example-only" not in repr(settings)
    assert DATABASE_URL not in settings.model_dump_json()
    assert settings.database_url.get_secret_value() == DATABASE_URL


def test_settings_hide_keys_and_smtp_password() -> None:
    settings = make_settings(smtp_username="mailer", smtp_password="smtp-fake-password")
    rendered = repr(settings) + settings.model_dump_json()
    for secret in (JWT_KEY, OTP_KEY, "smtp-fake-password"):
        assert secret not in rendered


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "postgresql://maya_app:example-only@localhost/maya",
        "postgresql+asyncpg://postgres:example-only@localhost/maya",
        "postgresql+asyncpg://maya_app@localhost/maya",
        "postgresql+asyncpg://maya_app:example-only@localhost:99999/maya",
    ],
)
def test_reject_invalid_runtime_database_url(url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(database_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "redis://",
        "redis://localhost:99999/0",
        "redis://localhost/invalid",
        "redis://localhost/0?socket_timeout=0",
    ],
)
def test_reject_invalid_redis_url(url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(redis_url=url)


def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MAYA_DATABASE_URL",
        "MAYA_REDIS_URL",
        "MAYA_JWT_SIGNING_KEY",
        "MAYA_OTP_HMAC_KEY",
        "MAYA_MIGRATION_DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_required_configuration_is_not_silently_defaulted(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_environment(monkeypatch)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("missing", ["MAYA_JWT_SIGNING_KEY", "MAYA_OTP_HMAC_KEY"])
def test_each_authentication_key_is_required(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    _clear_environment(monkeypatch)
    environment = {
        "MAYA_DATABASE_URL": DATABASE_URL,
        "MAYA_REDIS_URL": REDIS_URL,
        "MAYA_JWT_SIGNING_KEY": JWT_KEY,
        "MAYA_OTP_HMAC_KEY": OTP_KEY,
    }
    del environment[missing]
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings()


def test_environment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_environment(monkeypatch)
    monkeypatch.setenv("MAYA_DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("MAYA_REDIS_URL", REDIS_URL)
    monkeypatch.setenv("MAYA_JWT_SIGNING_KEY", JWT_KEY)
    monkeypatch.setenv("MAYA_OTP_HMAC_KEY", OTP_KEY)
    assert Settings().database_url.get_secret_value() == DATABASE_URL


def test_dotenv_loading_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_environment(monkeypatch)
    path = tmp_path / "settings.txt"
    path.write_text(
        f"MAYA_DATABASE_URL={DATABASE_URL}\nMAYA_REDIS_URL={REDIS_URL}\n"
        f"MAYA_JWT_SIGNING_KEY={JWT_KEY}\nMAYA_OTP_HMAC_KEY={OTP_KEY}\n"
    )
    settings = Settings(_env_file=path)
    assert settings.database_url.get_secret_value() == DATABASE_URL


@pytest.mark.parametrize("timeout", [0, -1, 31])
def test_probe_timeout_is_bounded(timeout: float) -> None:
    with pytest.raises(ValidationError):
        make_settings(probe_timeout_seconds=timeout)


@pytest.mark.parametrize("url", ["invalid", DATABASE_URL])
def test_migration_configuration_must_be_valid_and_separate(url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(migration_database_url=url)


def test_valid_separate_migration_configuration_is_accepted() -> None:
    settings = make_settings(migration_database_url=MIGRATION_URL)
    assert settings.migration_database_url is not None


def test_migration_settings_need_only_the_migration_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_environment(monkeypatch)
    with pytest.raises(ValidationError):
        MigrationSettings()
    monkeypatch.setenv("MAYA_MIGRATION_DATABASE_URL", MIGRATION_URL)
    assert MigrationSettings().migration_database_url.get_secret_value() == MIGRATION_URL
    monkeypatch.setenv("MAYA_MIGRATION_DATABASE_URL", "postgresql://wrong-driver")
    with pytest.raises(ValidationError):
        MigrationSettings()


@pytest.mark.parametrize("field", ["jwt_signing_key", "otp_hmac_key"])
@pytest.mark.parametrize("key", ["", "short", "x" * 31])
def test_short_authentication_keys_are_rejected(field: str, key: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: key})


def test_key_length_is_measured_in_bytes() -> None:
    # 16 characters of two bytes each is exactly 32 bytes.
    assert make_settings(jwt_signing_key="é" * 16).jwt_signing_key is not None
    with pytest.raises(ValidationError):
        make_settings(jwt_signing_key="é" * 15)


def test_authentication_keys_must_differ() -> None:
    with pytest.raises(ValidationError):
        make_settings(jwt_signing_key=JWT_KEY, otp_hmac_key=JWT_KEY)


@pytest.mark.parametrize(
    "overrides",
    [
        {"access_token_ttl_seconds": 59},
        {"access_token_ttl_seconds": 3601},
        {"refresh_token_ttl_seconds": 3599},
        {"refresh_token_ttl_seconds": 2_592_001},
        {"refresh_absolute_ttl_seconds": 7_776_001},
        {"refresh_token_ttl_seconds": 100_000, "refresh_absolute_ttl_seconds": 99_999},
        {"otp_resend_cooldown_seconds": 0},
        {"otp_resend_cooldown_seconds": 3601},
    ],
)
def test_token_and_cooldown_lifetimes_are_bounded(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_defaults_match_the_approved_values() -> None:
    settings = make_settings()
    assert settings.access_token_ttl_seconds == 900
    assert settings.refresh_token_ttl_seconds == 604_800
    assert settings.refresh_absolute_ttl_seconds == 2_592_000
    assert settings.otp_resend_cooldown_seconds == 60
    assert settings.smtp_security == "starttls"
    assert settings.email_configured is False


def test_smtp_configuration_is_all_or_nothing() -> None:
    with pytest.raises(ValidationError):
        make_settings(smtp_host="smtp.example.com")
    with pytest.raises(ValidationError):
        make_settings(smtp_from_address="mailer@example.com")
    with pytest.raises(ValidationError):
        make_settings(smtp_username="mailer")
    with pytest.raises(ValidationError):
        make_settings(smtp_password="fake-secret-value")
    configured = make_settings(
        smtp_host="smtp.example.com",
        smtp_from_address="mailer@example.com",
        smtp_username="mailer",
        smtp_password="fake-secret-value",
    )
    assert configured.email_configured is True


@pytest.mark.parametrize(
    "address",
    ["not-an-address", "a@b", "a b@example.com", "a@example.com\r\nBcc: x@example.com", ""],
)
def test_invalid_sender_addresses_are_rejected(address: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(smtp_host="smtp.example.com", smtp_from_address=address)


@pytest.mark.parametrize("security", ["plain", "none", "STARTTLS"])
def test_plaintext_smtp_is_not_selectable(security: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(smtp_security=security)
