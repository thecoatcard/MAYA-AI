"""Unit tests for MCP vendor rate limiting and HTTP 429 backoff."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.mcp.limiter import LeakyBucket, VendorRateLimiter


@pytest.mark.asyncio
async def test_leaky_bucket_burst_and_leak() -> None:
    # 2 requests burst capacity, leaks 10 per second
    bucket = LeakyBucket(capacity=2.0, leak_rate=10.0)

    # First two acquisitions succeed immediately
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True

    # Third acquisition without waiting is rejected
    assert bucket.try_acquire() is False

    # Wait 0.2s -> 2 tokens leak -> acquisition succeeds
    await asyncio.sleep(0.2)
    assert bucket.try_acquire() is True


@pytest.mark.asyncio
async def test_vendor_rate_limiter_throttle_context() -> None:
    limiter = VendorRateLimiter()

    # Acquiring for known vendor works cleanly
    async with limiter.throttle("github"):
        pass

    # Acquiring for unknown vendor creates default profile
    async with limiter.throttle("custom_vendor"):
        pass


def test_parse_retry_after_seconds_and_dates() -> None:
    limiter = VendorRateLimiter()

    # Plain seconds
    assert limiter.parse_retry_after("15") == 15.0
    assert limiter.parse_retry_after("0") == 0.0

    # Invalid string defaults to base backoff
    assert limiter.parse_retry_after("invalid_header") == 2.0

    # RFC 2822 Date format
    future_time = datetime.now(UTC) + timedelta(seconds=25)
    date_str = future_time.strftime("%a, %d %b %Y %H:%M:%S GMT")
    parsed = limiter.parse_retry_after(date_str)
    assert 20.0 <= parsed <= 30.0


@pytest.mark.asyncio
async def test_handle_429_applies_backoff_and_jitter() -> None:
    limiter = VendorRateLimiter()

    # Handling 429 sets vendor into backoff
    delay = limiter.record_429("github", retry_after="1")
    assert 1.0 <= delay <= 2.0
    assert limiter.is_backoff_active("github") is True

    # Sleep past backoff
    await asyncio.sleep(delay + 0.1)
    assert limiter.is_backoff_active("github") is False
