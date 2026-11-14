"""MAYA speaks with a female Gemini voice; MAYA_VOICE_NAME may pick another female voice only."""

from __future__ import annotations

import pytest

from app.voice.live_options import (
    DEFAULT_VOICE_NAME,
    FEMALE_VOICE_NAMES,
    LiveOptions,
    voice_name_from_env,
)


def test_the_default_voice_is_a_female_voice() -> None:
    assert DEFAULT_VOICE_NAME == "Leda"
    assert DEFAULT_VOICE_NAME in FEMALE_VOICE_NAMES
    assert voice_name_from_env({}) == "Leda"


@pytest.mark.parametrize("raw", ["aoede", "AOEDE", "  Aoede  "])
def test_a_listed_female_voice_is_accepted_in_any_letter_case(raw: str) -> None:
    assert voice_name_from_env({"MAYA_VOICE_NAME": raw}) == "Aoede"


@pytest.mark.parametrize("raw", ["Puck", "Charon", "Fenrir", "en-default", "", "Leda; drop"])
def test_a_male_unknown_or_malformed_name_falls_back_to_the_default(raw: str) -> None:
    assert voice_name_from_env({"MAYA_VOICE_NAME": raw}) == DEFAULT_VOICE_NAME


def test_the_live_options_use_the_environment_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAYA_VOICE_NAME", "Zephyr")
    assert LiveOptions().voice_name == "Zephyr"
    monkeypatch.delenv("MAYA_VOICE_NAME")
    assert LiveOptions().voice_name == "Leda"
