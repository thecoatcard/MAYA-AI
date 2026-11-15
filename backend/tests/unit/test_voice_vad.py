"""Unit tests for Voice Activity Detection (VAD) energy, RMS, and state tracking."""

import math

from app.voice.audio_converter import samples_to_pcm16
from app.voice.vad import VADState, VoiceActivityDetector


def _generate_sine(
    amplitude: int,
    freq: float = 440.0,
    sample_rate: int = 16000,
    duration_ms: int = 100,
) -> bytes:
    num_samples = int(sample_rate * (duration_ms / 1000.0))
    samples = [
        int(amplitude * math.sin(2 * math.pi * freq * i / sample_rate)) for i in range(num_samples)
    ]
    return samples_to_pcm16(samples)


def _generate_silence(duration_ms: int = 100, sample_rate: int = 16000) -> bytes:
    num_samples = int(sample_rate * (duration_ms / 1000.0))
    return samples_to_pcm16([0] * num_samples)


def test_vad_silence_detection() -> None:
    detector = VoiceActivityDetector(threshold_energy=0.015)
    silence = _generate_silence(duration_ms=100)

    assert detector.calculate_rms(silence) == 0.0
    assert detector.calculate_energy(silence) == 0.0
    assert not detector.is_speech(silence)


def test_vad_active_speech_detection() -> None:
    detector = VoiceActivityDetector(threshold_energy=0.015)
    # Loud sine wave with amplitude 10,000 / 32768 ~= 0.30 amplitude
    speech = _generate_sine(amplitude=10000, duration_ms=100)

    rms = detector.calculate_rms(speech)
    energy = detector.calculate_energy(speech)

    assert rms > 0.1
    assert energy > 0.01
    assert detector.is_speech(speech)


def test_vad_low_level_noise_below_threshold() -> None:
    detector = VoiceActivityDetector(threshold_energy=0.015)
    # Noise with amplitude 200 / 32768 ~= 0.006 amplitude
    noise = _generate_sine(amplitude=200, duration_ms=100)

    rms = detector.calculate_rms(noise)
    assert rms < 0.015
    assert not detector.is_speech(noise)


def test_vad_process_frame_state_and_hangover() -> None:
    # Hangover of 2 frames: once speech ends, 2 frames stay marked as speech to prevent cutoff
    detector = VoiceActivityDetector(threshold_energy=0.015, hangover_frames=2)

    speech_frame = _generate_sine(amplitude=12000, duration_ms=100)
    silence_frame = _generate_silence(duration_ms=100)

    # Frame 1: speech
    state1 = detector.process_frame(speech_frame)
    assert isinstance(state1, VADState)
    assert state1.is_speech is True
    assert state1.rms > 0.05

    # Frame 2: silence -> hangover keeps is_speech=True
    state2 = detector.process_frame(silence_frame)
    assert state2.is_speech is True
    assert state2.rms == 0.0

    # Frame 3: silence -> hangover 2nd frame keeps is_speech=True
    state3 = detector.process_frame(silence_frame)
    assert state3.is_speech is True

    # Frame 4: silence -> hangover expired, now is_speech=False
    state4 = detector.process_frame(silence_frame)
    assert state4.is_speech is False


def test_vad_process_stream() -> None:
    detector = VoiceActivityDetector(threshold_energy=0.015, hangover_frames=0)
    frames = [
        _generate_silence(),
        _generate_sine(amplitude=15000),
        _generate_sine(amplitude=15000),
        _generate_silence(),
    ]
    results = detector.process_stream(frames)
    assert results == [False, True, True, False]


def test_vad_empty_and_odd_bytes() -> None:
    detector = VoiceActivityDetector()
    assert detector.calculate_rms(b"") == 0.0
    assert detector.calculate_energy(b"") == 0.0
    assert not detector.is_speech(b"")

    # Odd bytes should not crash calculate_rms
    rms = detector.calculate_rms(b"\x00\x10\x00")
    assert rms >= 0.0
