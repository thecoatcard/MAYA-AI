"""Voice Activity Detection (VAD) module for speech vs. silence classification.

Provides energy and RMS-based detection with hangover smoothing to prevent clipped speech.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.voice.audio_converter import pcm16_to_samples


@dataclass(frozen=True)
class VADState:
    """State classification for an individual audio frame."""

    is_speech: bool
    energy: float
    rms: float
    confidence: float = 0.0


class VoiceActivityDetector:
    """Detects voice activity in 16-bit linear PCM audio frames."""

    def __init__(
        self,
        threshold_energy: float = 0.015,
        hangover_frames: int = 3,
        sample_rate: int = 16000,
    ) -> None:
        """Initialize the Voice Activity Detector.

        Args:
            threshold_energy: Normalized RMS threshold above which speech is detected.
            hangover_frames: Number of subsequent frames to retain speech state to avoid clipping.
            sample_rate: Sampling rate in Hz.
        """
        self.threshold_energy = threshold_energy
        self.hangover_frames = hangover_frames
        self.sample_rate = sample_rate
        self._hangover_count = 0

    def calculate_rms(self, frame: bytes) -> float:
        """Calculate normalized Root Mean Square (RMS) in range [0.0, 1.0]."""
        if not frame:
            return 0.0
        # Discard trailing odd byte if present
        if len(frame) % 2 != 0:
            frame = frame[:-1]
        if not frame:
            return 0.0

        samples = pcm16_to_samples(frame)
        if not samples:
            return 0.0

        sum_squares = sum(s * s for s in samples)
        mean_square = sum_squares / len(samples)
        rms = math.sqrt(mean_square)
        return float(rms / 32768.0)

    def calculate_energy(self, frame: bytes) -> float:
        """Calculate normalized energy in range [0.0, 1.0]."""
        if not frame:
            return 0.0
        if len(frame) % 2 != 0:
            frame = frame[:-1]
        if not frame:
            return 0.0

        samples = pcm16_to_samples(frame)
        if not samples:
            return 0.0

        sum_squares = sum(s * s for s in samples)
        mean_square = sum_squares / len(samples)
        return float(mean_square / (32768.0**2))

    def is_speech(self, frame: bytes, threshold: float | None = None) -> bool:
        """Determine if a frame contains speech exceeding the energy/RMS threshold."""
        thresh = threshold if threshold is not None else self.threshold_energy
        rms = self.calculate_rms(frame)
        return rms >= thresh

    def process_frame(self, frame: bytes) -> VADState:
        """Process a frame with hangover smoothing and return the VADState."""
        rms = self.calculate_rms(frame)
        energy = self.calculate_energy(frame)
        raw_speech = rms >= self.threshold_energy

        if raw_speech:
            self._hangover_count = self.hangover_frames
            is_speech = True
        else:
            if self._hangover_count > 0:
                self._hangover_count -= 1
                is_speech = True
            else:
                is_speech = False

        confidence = min(1.0, rms / max(self.threshold_energy, 1e-5)) if is_speech else 0.0
        return VADState(
            is_speech=is_speech,
            energy=energy,
            rms=rms,
            confidence=confidence,
        )

    def process_stream(self, frames: list[bytes], threshold: float | None = None) -> list[bool]:
        """Process a list of audio frames and return a boolean list indicating speech per frame."""
        self.reset()
        results: list[bool] = []
        for frame in frames:
            if threshold is not None:
                results.append(self.is_speech(frame, threshold=threshold))
            else:
                state = self.process_frame(frame)
                results.append(state.is_speech)
        return results

    def reset(self) -> None:
        """Reset internal hangover state."""
        self._hangover_count = 0
