import json
import math
import struct
from dataclasses import replace

import pytest

from tests.spikes.spike_s3_gemini_live import (
    Event,
    analyze_session,
    build_plans,
    main,
    offline_report,
    pcm_fixture,
    replay_fixture,
)


def test_text_methods_are_planned_in_separate_sessions() -> None:
    client, realtime = build_plans()
    assert client.method == "send_client_content"
    assert realtime.method == "send_realtime_input"
    assert client.session_id != realtime.session_id
    assert client.max_seconds == realtime.max_seconds == 960
    assert not client.context_compression
    assert not realtime.context_compression
    assert client.text_arguments() == {
        "turns": [{"role": "user", "parts": [{"text": client.prompt}]}],
        "turn_complete": True,
    }
    assert realtime.text_arguments() == {"text": realtime.prompt}


def test_pcm_fixture_has_ground_truth_format_and_duration() -> None:
    pcm = pcm_fixture()
    assert len(pcm) == 4800  # 100 ms of mono 24 kHz signed 16-bit PCM.
    samples = struct.unpack("<2400h", pcm)
    assert samples[0] == 0
    assert max(samples) <= 8192
    assert min(samples) >= -8192
    assert any(sample != 0 for sample in samples)
    assert pcm == pcm_fixture()


def test_replay_measures_only_fixture_timestamps() -> None:
    plan = build_plans()[0]
    metrics = analyze_session(plan, replay_fixture())
    assert metrics.first_audio_latency_ms == pytest.approx(125)
    assert metrics.audio_duration_ms == pytest.approx(100)
    assert metrics.resumptions == 1
    assert metrics.go_away_events == 1
    assert metrics.observed_seconds == 960
    assert not metrics.provider_session_ended


@pytest.mark.parametrize("at", [-1.0, math.nan, math.inf, 961.0])
def test_invalid_event_time_fails(at: float) -> None:
    with pytest.raises(ValueError, match="timestamp"):
        analyze_session(build_plans()[0], [Event("setup_complete", at)])


@pytest.mark.parametrize(
    "events",
    [
        [],
        [Event("text_sent", 0)],
        [Event("setup_complete", 0), Event("audio", 1, pcm_fixture())],
        [Event("setup_complete", 1), Event("text_sent", 0)],
        [Event("setup_complete", 0), Event("setup_complete", 1)],
        [Event("setup_complete", 0), Event("text_sent", 1), Event("text_sent", 2)],
        [Event("setup_complete", 0), Event("resumed", 1)],
        [Event("setup_complete", 0), Event("turn_complete", 1)],
        [
            Event("setup_complete", 0),
            Event("text_sent", 1),
            Event("audio", 2, b"x"),
        ],
        [
            Event("setup_complete", 0),
            Event("text_sent", 1),
            Event("audio", 2, pcm_fixture(), sample_rate=16000),
        ],
        [
            Event("setup_complete", 0),
            Event("text_sent", 1),
            Event("disconnected", 2),
            Event("audio", 3, pcm_fixture()),
        ],
        [*replay_fixture(), Event("go_away", 960)],
    ],
)
def test_incomplete_or_inconsistent_replays_fail(events: list[Event]) -> None:
    with pytest.raises(ValueError):
        analyze_session(build_plans()[0], events)


def test_session_termination_is_distinct_from_observation_timeout() -> None:
    events = replay_fixture()
    events[-1] = Event("session_ended", 901)
    assert analyze_session(build_plans()[0], events).provider_session_ended


def test_no_events_after_disconnect_without_resumption() -> None:
    events = replay_fixture()
    events.insert(6, Event("go_away", 600.05))
    with pytest.raises(ValueError, match="disconnected"):
        analyze_session(build_plans()[0], events)


def test_invalid_plan_does_not_generate_success_report() -> None:
    with pytest.raises(ValueError, match="duration"):
        analyze_session(replace(build_plans()[0], max_seconds=math.nan), replay_fixture())


def test_offline_cli_is_labelled_synthetic(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--offline"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == offline_report()
    assert report["status"] == "offline_fixture_only"
    assert report["live_calls"] == 0
    assert report["live_acceptance_verified"] is False
    assert len(report["sessions"]) == 2


def test_live_cli_is_blocked_before_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--run"])
    assert error.value.code == 2
    assert "hard spending cap" in capsys.readouterr().err
