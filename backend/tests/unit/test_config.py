from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

DATABASE_URL = "postgresql+asyncpg://maya_app:example-only@localhost:5432/maya"
REDIS_URL = "redis://localhost:6379/0"


def test_settings_hide_connection_values() -> None:
    settings = Settings(database_url=DATABASE_URL, redis_url=REDIS_URL)
    assert "example-only" not in repr(settings)
    assert DATABASE_URL not in settings.model_dump_json()
    assert settings.database_url.get_secret_value() == DATABASE_URL


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
        Settings(database_url=url, redis_url=REDIS_URL)


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
        Settings(database_url=DATABASE_URL, redis_url=url)


def test_required_configuration_is_not_silently_defaulted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAYA_DATABASE_URL", raising=False)
    monkeypatch.delenv("MAYA_REDIS_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_environment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAYA_DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("MAYA_REDIS_URL", REDIS_URL)
    assert Settings().database_url.get_secret_value() == DATABASE_URL


def test_dotenv_loading_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAYA_DATABASE_URL", raising=False)
    monkeypatch.delenv("MAYA_REDIS_URL", raising=False)
    path = tmp_path / "settings.txt"
    path.write_text(f"MAYA_DATABASE_URL={DATABASE_URL}\nMAYA_REDIS_URL={REDIS_URL}\n")
    settings = Settings(_env_file=path)
    assert settings.database_url.get_secret_value() == DATABASE_URL


@pytest.mark.parametrize("timeout", [0, -1, 31])
def test_probe_timeout_is_bounded(timeout: float) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=DATABASE_URL, redis_url=REDIS_URL, probe_timeout_seconds=timeout)


@pytest.mark.parametrize("url", ["invalid", DATABASE_URL])
def test_migration_configuration_must_be_valid_and_separate(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=DATABASE_URL, redis_url=REDIS_URL, migration_database_url=url)
