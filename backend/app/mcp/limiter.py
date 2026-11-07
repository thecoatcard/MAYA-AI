"""Vendor leaky-bucket rate limiting and HTTP 429 backoff handling."""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

DEFAULT_BASE_DELAY = 2.0
MAX_BACKOFF_DELAY = 60.0


class VendorRateLimitError(Exception):
    """Raised when vendor rate limits cannot be fulfilled within timeout bounds."""


class LeakyBucket:
    """Thread-safe and async-safe leaky bucket rate limiter."""

    def __init__(self, capacity: float, leak_rate: float) -> None:
        self.capacity = float(capacity)
        self.leak_rate = float(leak_rate)
        self._water: float = 0.0
        self._last_leak: float = time.monotonic()
        self._lock = asyncio.Lock()

    def _leak(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_leak
        self._water = max(0.0, self._water - elapsed * self.leak_rate)
        self._last_leak = now

    def try_acquire(self, amount: float = 1.0) -> bool:
        """Non-blocking attempt to acquire water capacity."""
        self._leak()
        if self._water + amount <= self.capacity:
            self._water += amount
            return True
        return False

    async def acquire(self, amount: float = 1.0) -> None:
        """Async wait until bucket capacity is available."""
        if amount > self.capacity:
            raise ValueError(
                f"Requested acquire amount ({amount}) exceeds bucket capacity ({self.capacity})"
            )
        while True:
            async with self._lock:
                self._leak()
                if self._water + amount <= self.capacity:
                    self._water += amount
                    return
                # Calculate sleep duration needed to leak excess
                excess = (self._water + amount) - self.capacity
                wait_time = excess / self.leak_rate if self.leak_rate > 0 else 0.1

            await asyncio.sleep(min(wait_time, 1.0))


class VendorRateLimiter:
    """Centralized rate limiter per MCP vendor with leaky-bucket and 429 backoff."""

    DEFAULT_PROFILES: dict[str, tuple[float, float]] = {
        # vendor: (capacity, leak_rate)
        "brave": (5.0, 1.0),
        "github": (10.0, 0.5),
        "notion": (5.0, 3.0),
        "google": (10.0, 5.0),
        "default": (10.0, 5.0),
    }

    def __init__(self) -> None:
        self._buckets: dict[str, LeakyBucket] = {}
        self._backoff_until: dict[str, float] = {}
        self._attempt_count: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def configure_vendor(self, vendor: str, rate_per_second: float, burst_capacity: float) -> None:
        """Explicitly configure or override rate limit parameters for a vendor."""
        norm = vendor.lower()
        self._buckets[norm] = LeakyBucket(capacity=burst_capacity, leak_rate=rate_per_second)

    async def acquire(self, vendor: str, amount: float = 1.0) -> None:
        """Acquire quota for vendor operation, waiting if currently rate-limited."""
        norm = vendor.lower()
        while True:
            until = self._backoff_until.get(norm, 0.0)
            now = time.monotonic()
            if now >= until:
                break
            await asyncio.sleep(until - now)
        bucket = self._get_bucket(norm)
        await bucket.acquire(amount)

    def _get_bucket(self, vendor: str) -> LeakyBucket:
        norm = vendor.lower()
        if norm not in self._buckets:
            cap, rate = self.DEFAULT_PROFILES.get(norm, self.DEFAULT_PROFILES["default"])
            self._buckets[norm] = LeakyBucket(capacity=cap, leak_rate=rate)
        return self._buckets[norm]

    def parse_retry_after(self, retry_after: str | None) -> float:
        """Parse HTTP Retry-After header as either seconds or RFC 2822 date."""
        if not retry_after:
            return DEFAULT_BASE_DELAY

        clean = retry_after.strip()
        # 1. Try parsing integer / float seconds
        try:
            return max(0.0, min(float(clean), MAX_BACKOFF_DELAY))
        except ValueError:
            pass

        # 2. Try parsing RFC 2822 HTTP-date
        try:
            parsed_date = email.utils.parsedate_to_datetime(clean)
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=UTC)
            now = datetime.now(UTC)
            delta = (parsed_date - now).total_seconds()
            return max(0.0, min(delta, MAX_BACKOFF_DELAY))
        except Exception:
            return DEFAULT_BASE_DELAY

    def record_429(self, vendor: str, retry_after: str | None = None) -> float:
        """Record upstream 429 response, advance backoff counter, and set backoff delay."""
        norm = vendor.lower()
        attempts = self._attempt_count.get(norm, 0) + 1
        self._attempt_count[norm] = attempts

        if retry_after:
            delay = self.parse_retry_after(retry_after)
        else:
            # Full jitter exponential backoff
            raw_backoff = min(MAX_BACKOFF_DELAY, DEFAULT_BASE_DELAY * (2 ** (attempts - 1)))
            delay = raw_backoff * random.uniform(0.5, 1.0)

        until = time.monotonic() + delay
        self._backoff_until[norm] = until
        logger.warning(
            "Vendor '%s' returned 429. Applying backoff for %.2fs (attempt %d)",
            norm,
            delay,
            attempts,
        )
        return delay

    def is_backoff_active(self, vendor: str) -> bool:
        """Check if active backoff delay is currently in effect for vendor."""
        norm = vendor.lower()
        until = self._backoff_until.get(norm, 0.0)
        return time.monotonic() < until

    @asynccontextmanager
    async def throttle(self, vendor: str) -> AsyncIterator[None]:
        """Async context manager metering access to a vendor endpoint."""
        norm = vendor.lower()

        # 1. Wait out active backoff window if present
        while True:
            until = self._backoff_until.get(norm, 0.0)
            now = time.monotonic()
            if now >= until:
                # Backoff expired
                break
            await asyncio.sleep(until - now)

        # 2. Acquire leaky-bucket token
        bucket = self._get_bucket(norm)
        await bucket.acquire(1.0)

        try:
            yield
            # Reset consecutive attempt count on successful execution
            self._attempt_count[norm] = 0
        except Exception:
            raise
