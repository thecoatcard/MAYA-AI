"""Unit tests for audio conversion, byte manipulation, and resampling."""

import math
import struct

import pytest

from app.voice.audio_converter import (
    AudioConverter,
    chunk_pcm16,
    pcm16_to_samples,
    resample_linear,
    resample_pcm,
    samples_to_pcm16,
)


def test_pcm16_to_samples_and_roundtrip() -> None:
    original_samples = [0, 100, -100, 32767, -32768]
    raw_bytes = struct.pack(f"<{len(original_samples)}h", *original_samples)

    samples = pcm16_to_samples(raw_bytes)
    assert samples == original_samples

    reconstructed_bytes = samples_to_pcm16(samples)
    assert reconstructed_bytes == raw_bytes


def test_pcm16_to_samples_invalid_length() -> None:
    with pytest.raises(ValueError, match="even number of bytes"):
        pcm16_to_samples(b"\x00\x01\x02")


def test_samples_to_pcm16_clamping() -> None:
    samples = [40000, -40000, 32767, -32768, 0]
    raw_bytes = samples_to_pcm16(samples)
    unpacked = pcm16_to_samples(raw_bytes)
    assert unpacked == [32767, -32768, 32767, -32768, 0]


def test_chunk_pcm16_16khz() -> None:
    # 16 kHz, 100 ms chunk duration => 1600 samples => 3200 bytes per chunk
    sample_rate = 16000
    duration_ms = 100
    expected_chunk_size = 3200

    # 3 full chunks + 1 partial chunk
    total_samples = 1600 * 3 + 500
    raw_data = struct.pack(f"<{total_samples}h", *([1000] * total_samples))

    chunks = chunk_pcm16(raw_data, chunk_duration_ms=duration_ms, sample_rate=sample_rate)
    assert len(chunks) == 4
    assert len(chunks[0]) == expected_chunk_size
    assert len(chunks[1]) == expected_chunk_size
    assert len(chunks[2]) == expected_chunk_size
    assert len(chunks[3]) == 500 * 2


def test_chunk_pcm16_empty() -> None:
    assert chunk_pcm16(b"") == []


def test_resample_linear_same_rate() -> None:
    data = b"\x00\x00\x10\x00\x20\x00"
    assert resample_linear(data, 16000, 16000) == data
    assert resample_pcm(data, 16000, 16000) == data


def test_resample_linear_empty() -> None:
    assert resample_linear(b"", 16000, 24000) == b""


def test_resample_linear_upsample_16k_to_24k() -> None:
    # 1600 samples at 16 kHz = 100 ms
    # Resampled to 24 kHz should yield 2400 samples = 4800 bytes
    samples_16k = [int(10000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(1600)]
    pcm_16k = samples_to_pcm16(samples_16k)
    assert len(pcm_16k) == 3200

    pcm_24k = resample_linear(pcm_16k, from_rate=16000, to_rate=24000)
    assert len(pcm_24k) == 4800

    samples_24k = pcm16_to_samples(pcm_24k)
    assert len(samples_24k) == 2400
    # Boundary checks
    assert max(samples_24k) <= 10000
    assert min(samples_24k) >= -10000


def test_resample_linear_downsample_24k_to_16k() -> None:
    # 2400 samples at 24 kHz = 100 ms => downsampled to 16 kHz yields 1600 samples (3200 bytes)
    samples_24k = [int(10000 * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(2400)]
    pcm_24k = samples_to_pcm16(samples_24k)
    assert len(pcm_24k) == 4800

    pcm_16k = resample_linear(pcm_24k, from_rate=24000, to_rate=16000)
    assert len(pcm_16k) == 3200

    samples_16k = pcm16_to_samples(pcm_16k)
    assert len(samples_16k) == 1600


def test_audio_converter_class_wrapper() -> None:
    converter = AudioConverter()
    samples = [10, 20, 30]
    pcm = converter.samples_to_pcm16(samples)
    assert converter.pcm16_to_samples(pcm) == samples
    chunks = converter.chunk_pcm16(pcm, chunk_duration_ms=10, sample_rate=1000)
    assert len(chunks) >= 1
    resampled = converter.resample_pcm(pcm, 16000, 24000)
    assert isinstance(resampled, bytes)
