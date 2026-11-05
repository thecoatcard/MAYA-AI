"""Chat settings: provisional defaults and bounds."""

import pytest
from pydantic import ValidationError

from tests.support.gateway_factory import make_settings


def test_chat_defaults_are_the_documented_provisional_values() -> None:
    settings = make_settings()
    assert settings.chat_max_message_chars == 8000
    assert settings.chat_rate_limit_per_minute == 20
    assert settings.chat_context_token_budget == 32_000


def test_chat_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAYA_CHAT_MAX_MESSAGE_CHARS", "500")
    monkeypatch.setenv("MAYA_CHAT_RATE_LIMIT_PER_MINUTE", "5")
    monkeypatch.setenv("MAYA_CHAT_CONTEXT_TOKEN_BUDGET", "4000")
    settings = make_settings()
    assert (settings.chat_max_message_chars, settings.chat_rate_limit_per_minute) == (500, 5)
    assert settings.chat_context_token_budget == 4000


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("chat_max_message_chars", 0),
        ("chat_max_message_chars", 100_001),
        ("chat_rate_limit_per_minute", 0),
        ("chat_context_token_budget", 999),
    ],
)
def test_chat_settings_reject_out_of_range_values(name: str, value: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{name: value})
