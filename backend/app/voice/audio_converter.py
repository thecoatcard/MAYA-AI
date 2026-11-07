"""Audio conversion, PCM manipulation, and resampling utilities.

Provides utilities for 16-bit linear PCM conversion, chunking, and sample rate conversion.
"""

from __future__ import annotations

import struct


def pcm16_to_samples(raw_bytes: bytes) -> list[int]:
    """Convert raw 16-bit little-endian signed PCM bytes to a list of integer samples.

    Raises:
        ValueError: If byte count is odd.
    """
    if len(raw_bytes) % 2 != 0:
        raise ValueError("raw_bytes must contain an even number of bytes for 16-bit PCM.")
    num_samples = len(raw_bytes) // 2
    if num_samples == 0:
        return []
    return list(struct.unpack(f"<{num_samples}h", raw_bytes))


def samples_to_pcm16(samples: list[int]) -> bytes:
    """Convert integer samples to raw 16-bit little-endian signed PCM bytes (clamped)."""
    if not samples:
        return b""
    clamped = [max(-32768, min(32767, int(s))) for s in samples]
    return struct.pack(f"<{len(clamped)}h", *clamped)


def chunk_pcm16(data: bytes, chunk_duration_ms: int = 100, sample_rate: int = 16000) -> list[bytes]:
    """Split 16-bit mono PCM audio data into chunks of a given duration in milliseconds.

    Args:
        data: Raw PCM audio bytes.
        chunk_duration_ms: Duration of each chunk in ms (default 100 ms).
        sample_rate: Sample rate in Hz (default 16000 Hz).

    Returns:
        List of byte chunks. The last chunk may be shorter if remainder exists.
    """
    if not data:
        return []
    if chunk_duration_ms <= 0 or sample_rate <= 0:
        raise ValueError("chunk_duration_ms and sample_rate must be positive integers.")

    bytes_per_sample = 2
    chunk_size = int(sample_rate * (chunk_duration_ms / 1000.0) * bytes_per_sample)
    if chunk_size <= 0:
        chunk_size = bytes_per_sample

    # Ensure chunk boundary aligns to 2 bytes
    if chunk_size % 2 != 0:
        chunk_size += 1

    chunks: list[bytes] = []
    for i in range(0, len(data), chunk_size):
        chunks.append(data[i : i + chunk_size])
    return chunks


def resample_linear(data: bytes, from_rate: int, to_rate: int) -> bytes:
    """Resample 16-bit signed linear mono PCM using linear interpolation.

    Args:
        data: Input PCM bytes.
        from_rate: Input sample rate in Hz.
        to_rate: Desired output sample rate in Hz.

    Returns:
        Resampled PCM bytes.
    """
    if not data:
        return b""
    if from_rate == to_rate:
        return data
    if from_rate <= 0 or to_rate <= 0:
        raise ValueError("from_rate and to_rate must be positive.")

    # Even out any odd byte count safely
    if len(data) % 2 != 0:
        data = data[:-1]

    in_samples = pcm16_to_samples(data)
    n_in = len(in_samples)
    if n_in == 0:
        return b""

    n_out = max(1, round(n_in * (to_rate / from_rate)))
    if n_in == 1 or n_out == 1:
        return samples_to_pcm16([in_samples[0]] * n_out)

    out_samples: list[int] = []
    ratio = (n_in - 1) / (n_out - 1)
    for j in range(n_out):
        t = j * ratio
        i0 = int(t)
        i1 = min(i0 + 1, n_in - 1)
        alpha = t - i0
        val = round((1.0 - alpha) * in_samples[i0] + alpha * in_samples[i1])
        out_samples.append(val)

    return samples_to_pcm16(out_samples)


def resample_pcm(data: bytes, from_rate: int, to_rate: int) -> bytes:
    """Alias for resample_linear."""
    return resample_linear(data, from_rate, to_rate)


class AudioConverter:
    """Convenience class providing audio conversion methods."""

    @staticmethod
    def pcm16_to_samples(raw_bytes: bytes) -> list[int]:
        return pcm16_to_samples(raw_bytes)

    @staticmethod
    def samples_to_pcm16(samples: list[int]) -> bytes:
        return samples_to_pcm16(samples)

    @staticmethod
    def chunk_pcm16(
        data: bytes, chunk_duration_ms: int = 100, sample_rate: int = 16000
    ) -> list[bytes]:
        return chunk_pcm16(data, chunk_duration_ms, sample_rate)

    @staticmethod
    def resample_pcm(data: bytes, from_rate: int, to_rate: int) -> bytes:
        return resample_linear(data, from_rate, to_rate)
