"""Tests for Gateway settings, overrides, and logging redaction."""

import json
import logging
from decimal import Decimal

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.logging import JsonFormatter, configure_logging


def make_valid_settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "database_url": SecretStr("postgresql+asyncpg://maya_app:secret@127.0.0.1:15432/maya"),
        "redis_url": SecretStr("redis://127.0.0.1:16379/0"),
        "jwt_signing_key": SecretStr("1234567890123456789012345678901234567890"),
        "otp_hmac_key": SecretStr("abcdefghijklmnopqrstuvwxyz0123456789abcdef"),
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_gateway_default_settings() -> None:
    settings = make_valid_settings()
    assert settings.gemini_api_key is None
    assert settings.gemini_model_overrides == {}
    assert settings.gemini_verify_models_on_startup is False
    assert settings.gemini_request_timeout_seconds == 30.0
    assert settings.budget_enforcement_mode == "log_only"
    assert settings.gemini_budget_task_usd_alert == Decimal("1.50")
    assert settings.gemini_max_tool_turns_alert == 10
    assert settings.gemini_budget_task_usd_cap is None
    assert settings.gemini_max_tool_turns_cap is None


def test_blank_or_whitespace_gemini_api_key_is_none() -> None:
    assert make_valid_settings(gemini_api_key="").gemini_api_key is None
    assert make_valid_settings(gemini_api_key="   ").gemini_api_key is None
    assert make_valid_settings(gemini_api_key=SecretStr("")).gemini_api_key is None
    assert make_valid_settings(gemini_api_key=SecretStr(" \t ")).gemini_api_key is None
    valid = make_valid_settings(gemini_api_key="valid-key")
    assert valid.gemini_api_key is not None
    assert valid.gemini_api_key.get_secret_value() == "valid-key"


def test_budget_enforce_mode_requires_caps() -> None:
    # log_only requires no caps
    log_only = make_valid_settings(budget_enforcement_mode="log_only")
    assert log_only.budget_enforcement_mode == "log_only"

    # enforce without caps raises validation error
    with pytest.raises(ValidationError, match="gemini_budget_task_usd_cap"):
        make_valid_settings(budget_enforcement_mode="enforce")

    # enforce with caps succeeds
    enforced = make_valid_settings(
        budget_enforcement_mode="enforce",
        gemini_budget_task_usd_cap=Decimal("2.00"),
        gemini_max_tool_turns_cap=15,
    )
    assert enforced.budget_enforcement_mode == "enforce"
    assert enforced.gemini_budget_task_usd_cap == Decimal("2.00")
    assert enforced.gemini_max_tool_turns_cap == 15


def test_model_overrides_validation() -> None:
    # Valid override
    valid = make_valid_settings(gemini_model_overrides={"orchestrator": "gemini-3.8-flash-v2"})
    assert valid.gemini_model_overrides == {"orchestrator": "gemini-3.8-flash-v2"}

    # Unknown alias
    with pytest.raises(ValidationError, match="Invalid model override alias 'unknown'"):
        make_valid_settings(gemini_model_overrides={"unknown": "gemini-3.8-flash"})

    # Non-REST alias cannot be overridden
    for non_rest in ("live", "live-thinking", "research", "tts"):
        with pytest.raises(ValidationError, match=f"Invalid model override alias '{non_rest}'"):
            make_valid_settings(gemini_model_overrides={non_rest: "some-model"})

    # Invalid model_id pattern
    with pytest.raises(ValidationError, match="Invalid model_id"):
        make_valid_settings(gemini_model_overrides={"orchestrator": "invalid model with spaces"})


def test_model_overrides_from_env_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAYA_GEMINI_MODEL_OVERRIDES", '{"orchestrator": "gemini-3.8-flash-env"}')
    settings = make_valid_settings()
    assert settings.gemini_model_overrides == {"orchestrator": "gemini-3.8-flash-env"}


def test_logging_redacts_gemini_api_key() -> None:
    fake_key = "example-fake-gemini-key-for-test"
    settings = make_valid_settings(gemini_api_key=fake_key)
    configure_logging(settings)
    logger = logging.getLogger("maya")
    formatter = logger.handlers[0].formatter
    assert isinstance(formatter, JsonFormatter)

    record = logging.LogRecord(
        name="maya",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=f"Calling Gemini with key {fake_key} now",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    parsed = json.loads(formatted)
    assert fake_key not in parsed["message"]
    assert "[redacted]" in parsed["message"]
