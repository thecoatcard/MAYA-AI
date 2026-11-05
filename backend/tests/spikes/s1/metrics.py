"""Pure metric parsers and summaries shared by the runner and unit tests."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

_MEMORY_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)\s*$", re.I)
_UNIT_SCALE = {
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
    "tb": 1000**4,
    "tib": 1024**4,
}


def memory_to_bytes(value: str) -> int:
    """Convert one Docker stats memory quantity into bytes."""
    match = _MEMORY_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid Docker memory quantity: {value!r}")
    magnitude = float(match.group(1))
    unit = match.group(2).lower()
    return int(magnitude * _UNIT_SCALE[unit])


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """Return a nearest-rank percentile; empty input has no percentile."""
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if not all(math.isfinite(value) for value in ordered):
        raise ValueError("percentile values must be finite")
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def summarize(values: Sequence[float]) -> dict[str, float | int | None]:
    """Summarize measured values without inventing target thresholds."""
    return {
        "count": len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def parse_cpu_percent(value: str) -> float:
    """Parse Docker's CPU percentage field."""
    text = value.strip()
    if not text.endswith("%"):
        raise ValueError(f"invalid Docker CPU percentage: {value!r}")
    number = float(text[:-1])
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"invalid Docker CPU percentage: {value!r}")
    return number


def classify_cancellation_status(status: str | None) -> str:
    """Distinguish a terminal abort from a returned control request."""
    if status == "aborted":
        return "terminal_aborted"
    if status in {"completed", "failed"}:
        return "terminal_non_cancel_state"
    return "request_only_no_terminal_state_observed"
