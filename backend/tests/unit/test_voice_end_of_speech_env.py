"""MAYA_VOICE_END_OF_SPEECH_MS: default 800 ms, clamped to 400..1200 ms."""

from __future__ import annotations

import pytest

from app.voice import live_options
from app.voice.live_options import LiveOptions, end_of_speech_ms_from_env


def test_default_stays_800_ms() -> None:
    assert live_options.VAD_SILENCE_DURATION_MS == 800
    assert end_of_speech_ms_from_env({}) == 800
    assert LiveOptions(silence_duration_ms=800).silence_duration_ms == 800


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", 800),
        ("fast", 800),
        ("0", 400),
        ("399", 400),
        ("400", 400),
        ("650", 650),
        (" 1200 ", 1200),
        ("1201", 1200),
        ("99999", 1200),
    ],
)
def test_environment_value_is_clamped(raw: str, expected: int) -> None:
    assert end_of_speech_ms_from_env({"MAYA_VOICE_END_OF_SPEECH_MS": raw}) == expected


def test_live_options_read_the_environment_when_created(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAYA_VOICE_END_OF_SPEECH_MS", "5000")
    assert LiveOptions().silence_duration_ms == 1200
    monkeypatch.setenv("MAYA_VOICE_END_OF_SPEECH_MS", "500")
    assert LiveOptions().silence_duration_ms == 500
    assert LiveOptions(silence_duration_ms=900).silence_duration_ms == 900
