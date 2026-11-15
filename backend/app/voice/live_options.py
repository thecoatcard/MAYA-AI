"""Gemini Live session options that make the conversation natural and robust (Task V, item B).

Each option is a named constant with a documented default. All of them are fields of the installed
``google-genai`` types (checked in ``tests/unit/test_voice_live_config.py`` against the SDK models),
which is the only thing verified: **SDK-supported, not verified against the live service**. A field
the SDK accepts can still be ignored or rejected by a given Live model, so the owner's live spike
decides whether each value helps.

Applied:

* automatic activity detection: ``end_of_speech_sensitivity=LOW`` and ``silence_duration_ms`` so a
  user who pauses to think is not cut off, ``start_of_speech_sensitivity=HIGH`` and a short
  ``prefix_padding_ms`` so a new utterance (and a barge-in) is noticed quickly;
* ``activity_handling=START_OF_ACTIVITY_INTERRUPTS``: speech while MAYA talks interrupts her;
* input and output audio transcription (the web shows and persists the transcript);
* session resumption and a sliding-window context compression (long calls and GoAway);
* ``speech_config.language_code`` only when a language is set (default: not sent = automatic);
* a female voice: ``Leda`` (youthful, light) by default; ``MAYA_VOICE_NAME`` may pick another
  female voice from ``FEMALE_VOICE_NAMES`` (no per-user setting).

Not applied (the SDK has the field, but there is no evidence here that it helps this product or
the model family supports it): ``enable_affective_dialog``, ``proactivity.proactive_audio`` (MAYA
would speak unprompted), ``thinking_config`` (adds latency; the Live voice path wants the fastest
first word), ``explicit_vad_signal`` (the web client already sends its own VAD hints).
``MAYA_VOICE_NAME`` is checked against a fixed list of female voice names kept in this file (the
installed SDK has no list to validate against), so an unknown value falls back to the default.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

DEFAULT_VOICE_NAME: Final = "Leda"
# The user must be silent this long before the turn ends. The SDK default is short; 800 ms lets a
# person pause mid-sentence while thinking without MAYA jumping in, at a cost of about this much
# extra reply latency. Tune after the live spike.
VAD_SILENCE_DURATION_MS: Final = 800
# The end-of-speech silence can be tuned with MAYA_VOICE_END_OF_SPEECH_MS (clamped to 400..1200;
# a value that is not a whole number uses VAD_SILENCE_DURATION_MS). The default stays 800.
END_OF_SPEECH_ENV: Final = "MAYA_VOICE_END_OF_SPEECH_MS"
END_OF_SPEECH_MS_MIN: Final = 400
END_OF_SPEECH_MS_MAX: Final = 1200
# Speech must last this long before it counts as start of speech: short so barge-in is quick.
VAD_PREFIX_PADDING_MS: Final = 100
# "low" = less likely to decide the user has finished (so pauses do not end the turn).
VAD_END_SENSITIVITY: Final = "END_SENSITIVITY_LOW"
# "high" = more likely to notice that the user started talking (fast interruption).
VAD_START_SENSITIVITY: Final = "START_SENSITIVITY_HIGH"
ACTIVITY_HANDLING: Final = "START_OF_ACTIVITY_INTERRUPTS"


VOICE_NAME_ENV: Final = "MAYA_VOICE_NAME"
# MAYA's voice is female. These are Gemini's prebuilt female voices (named in Google's voice list);
# a value outside this set is ignored so a typo or a male voice name never reaches the service.
FEMALE_VOICE_NAMES: Final = frozenset(
    {
        "Leda",  # youthful (the default: light, sweet and friendly)
        "Zephyr",  # bright
        "Aoede",  # breezy
        "Achernar",  # soft
        "Vindemiatrix",  # gentle
        "Sulafat",  # warm
        "Laomedeia",  # upbeat
        "Autonoe",  # bright
        "Callirrhoe",  # easy-going
        "Despina",  # smooth
        "Erinome",  # clear
        "Pulcherrima",  # forward
        "Gacrux",  # mature
        "Kore",  # firm (the previous default)
    }
)


def voice_name_from_env(environ: Mapping[str, str] | None = None) -> str:
    """MAYA_VOICE_NAME if it names one of the female voices (any letter case), else the default."""
    raw = (os.environ if environ is None else environ).get(VOICE_NAME_ENV, "").strip()
    for name in FEMALE_VOICE_NAMES:
        if name.lower() == raw.lower():
            return name
    return DEFAULT_VOICE_NAME


def end_of_speech_ms_from_env(environ: Mapping[str, str] | None = None) -> int:
    """The end-of-speech silence in ms: MAYA_VOICE_END_OF_SPEECH_MS clamped to 400..1200."""
    raw = (os.environ if environ is None else environ).get(END_OF_SPEECH_ENV, "")
    try:
        value = int(raw.strip())
    except ValueError:
        return VAD_SILENCE_DURATION_MS
    return max(END_OF_SPEECH_MS_MIN, min(END_OF_SPEECH_MS_MAX, value))


@dataclass(frozen=True, slots=True)
class LiveOptions:
    """The tunable Live options. Defaults are the constants above (the silence may come from
    MAYA_VOICE_END_OF_SPEECH_MS, read when the options are created)."""

    voice_name: str = field(default_factory=voice_name_from_env)
    language_code: str | None = None  # None = automatic (the field is not sent)
    silence_duration_ms: int = field(default_factory=end_of_speech_ms_from_env)
    prefix_padding_ms: int = VAD_PREFIX_PADDING_MS
    end_sensitivity: str = VAD_END_SENSITIVITY
    start_sensitivity: str = VAD_START_SENSITIVITY
    activity_handling: str = ACTIVITY_HANDLING


def build_realtime_input_config(options: LiveOptions) -> Any:
    from google.genai import types

    return types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            start_of_speech_sensitivity=types.StartSensitivity(options.start_sensitivity),
            end_of_speech_sensitivity=types.EndSensitivity(options.end_sensitivity),
            prefix_padding_ms=options.prefix_padding_ms,
            silence_duration_ms=options.silence_duration_ms,
        ),
        activity_handling=types.ActivityHandling(options.activity_handling),
    )


def build_speech_config(options: LiveOptions) -> Any:
    from google.genai import types

    return types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=options.voice_name)
        ),
        language_code=options.language_code or None,
    )
