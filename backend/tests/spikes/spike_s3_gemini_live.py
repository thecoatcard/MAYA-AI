"""Dependency-free offline S3 plans/replay; isolated SDK adapter is separate."""

import argparse
import json
import math
import struct
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal

TextMethod = Literal["send_client_content", "send_realtime_input"]
EventKind = Literal[
    "setup_complete",
    "text_sent",
    "audio",
    "turn_complete",
    "go_away",
    "disconnected",
    "resumed",
    "session_ended",
    "observation_ended",
]


@dataclass(frozen=True)
class SessionPlan:
    session_id: str
    method: TextMethod
    max_seconds: float = 960
    context_compression: bool = False
    prompt: str = "Say the words: synthetic audio test."

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_seconds) or not 0 < self.max_seconds <= 960:
            raise ValueError("Session duration must be finite and between 0 and 960 seconds.")

    def text_arguments(self) -> dict[str, object]:
        if self.method == "send_client_content":
            return {
                "turns": [{"role": "user", "parts": [{"text": self.prompt}]}],
                "turn_complete": True,
            }
        if self.method == "send_realtime_input":
            return {"text": self.prompt}
        raise ValueError("Unsupported text-input method.")


@dataclass(frozen=True)
class Event:
    """Normalized fixture event, not an SDK response or Live API wire schema."""

    kind: EventKind
    at_seconds: float
    pcm: bytes = b""
    sample_rate: int = 24000


@dataclass(frozen=True)
class SessionMetrics:
    first_audio_latency_ms: float
    audio_duration_ms: float
    go_away_events: int
    resumptions: int
    observed_seconds: float
    provider_session_ended: bool


def build_plans() -> tuple[SessionPlan, SessionPlan]:
    return (
        SessionPlan("s3-client-content", "send_client_content"),
        SessionPlan("s3-realtime-text", "send_realtime_input"),
    )


def pcm_fixture() -> bytes:
    """100 ms, 440 Hz, mono little-endian signed PCM16 at 24 kHz."""
    samples = [round(8192 * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(2400)]
    return struct.pack("<2400h", *samples)


def replay_fixture() -> list[Event]:
    return [
        Event("setup_complete", 0),
        Event("text_sent", 0.1),
        Event("audio", 0.225, pcm_fixture()),
        Event("turn_complete", 0.4),
        Event("go_away", 599),
        Event("disconnected", 600),
        Event("resumed", 600.1),
        Event("observation_ended", 960),
    ]


def analyze_session(plan: SessionPlan, events: Sequence[Event]) -> SessionMetrics:
    plan.text_arguments()
    ready = False
    connected = False
    ended = False
    complete = False
    sent_at: float | None = None
    first_audio_at: float | None = None
    previous_at = 0.0
    audio_bytes = 0
    go_away_events = 0
    resumptions = 0
    provider_session_ended = False
    for event in events:
        at = event.at_seconds
        if not math.isfinite(at) or not previous_at <= at <= plan.max_seconds:
            raise ValueError("Event timestamp must be ordered, finite and within the bound.")
        if ended:
            raise ValueError("Events cannot follow session/observation termination.")
        previous_at = at
        if event.kind == "setup_complete":
            if ready:
                raise ValueError("Duplicate setup event.")
            ready = connected = True
            continue
        if not ready:
            raise ValueError("Session events require setup first.")
        if event.kind == "resumed":
            if connected:
                raise ValueError("Resumption requires a disconnected session.")
            connected = True
            resumptions += 1
            continue
        if event.kind in ("session_ended", "observation_ended"):
            ended = True
            provider_session_ended = event.kind == "session_ended"
            continue
        if not connected:
            raise ValueError("Session is disconnected; resumption is required.")
        if event.kind == "text_sent":
            if sent_at is not None:
                raise ValueError("One text method and one latency probe are allowed per session.")
            sent_at = at
        elif event.kind == "audio":
            if sent_at is None or complete:
                raise ValueError("Audio must follow the text probe and precede turn completion.")
            if event.sample_rate != 24000 or not event.pcm or len(event.pcm) % 2:
                raise ValueError("Output audio must be nonempty mono PCM16 at 24 kHz.")
            if first_audio_at is None:
                first_audio_at = at
            audio_bytes += len(event.pcm)
        elif event.kind == "turn_complete":
            if first_audio_at is None or complete:
                raise ValueError("Turn completion requires audio and cannot repeat.")
            complete = True
        elif event.kind == "go_away":
            go_away_events += 1
        elif event.kind == "disconnected":
            connected = False
        else:
            raise ValueError("Unsupported normalized event kind.")
    if not ended or not complete or sent_at is None or first_audio_at is None:
        raise ValueError("Replay requires setup, text, audio, turn completion and an end event.")
    return SessionMetrics(
        first_audio_latency_ms=(first_audio_at - sent_at) * 1000,
        audio_duration_ms=audio_bytes / (24000 * 2) * 1000,
        go_away_events=go_away_events,
        resumptions=resumptions,
        observed_seconds=previous_at,
        provider_session_ended=provider_session_ended,
    )


def offline_report() -> dict[str, object]:
    return {
        "status": "offline_fixture_only",
        "live_calls": 0,
        "live_acceptance_verified": False,
        "live_blocker": "Provider-side hard spending cap within INR 100 is not established.",
        "sessions": [
            {
                "plan": asdict(plan),
                "text_arguments": plan.text_arguments(),
                "synthetic_metrics": asdict(analyze_session(plan, replay_fixture())),
            }
            for plan in build_plans()
        ],
        "limitations": [
            "All timestamps, audio and lifecycle events are generated fixtures.",
            "This offline CLI never imports the separately locked SDK adapter.",
            "Resumption replay does not verify provider handles or conversation continuity.",
            "A bounded observation is not a measurement of unlimited session lifetime.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true", help="Analyze generated fixtures only.")
    mode.add_argument("--run", action="store_true", help="Blocked until live controls are ready.")
    args = parser.parse_args(argv)
    if args.run:
        parser.error("Live S3 blocked: enforceable INR 100 hard spending cap unverified.")
    print(json.dumps(offline_report(), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
