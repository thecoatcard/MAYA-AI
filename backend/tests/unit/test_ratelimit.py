import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.ratelimit import Limit, RateLimiterUnavailable, RedisRateLimiter
from tests.support.auth import FakeRateLimiter
from tests.support.clock import START, ManualClock

LIMIT = Limit(count=3, window_seconds=60)


def fake_redis(count: int = 1) -> MagicMock:
    redis = MagicMock()
    pipeline = MagicMock()
    pipeline.__aenter__ = AsyncMock(return_value=pipeline)
    pipeline.__aexit__ = AsyncMock(return_value=False)
    pipeline.execute = AsyncMock(return_value=[count, True])
    redis.pipeline.return_value = pipeline
    redis.get = AsyncMock(return_value=None)
    return redis


async def test_hit_increments_a_window_key_and_sets_an_expiry() -> None:
    redis = fake_redis(count=1)
    limiter = RedisRateLimiter(redis, ManualClock())
    assert await limiter.hit("send:abc", LIMIT) is True
    pipeline = redis.pipeline.return_value
    name = pipeline.incr.call_args.args[0]
    assert name == f"maya:rl:send:abc:{int(START.timestamp()) // 60}"
    pipeline.expire.assert_called_once_with(name, 120)
    redis.pipeline.assert_called_once_with(transaction=True)


@pytest.mark.parametrize(("count", "allowed"), [(1, True), (3, True), (4, False), (99, False)])
async def test_hit_is_allowed_up_to_the_limit(count: int, allowed: bool) -> None:
    assert await RedisRateLimiter(fake_redis(count), ManualClock()).hit("k", LIMIT) is allowed


async def test_a_new_window_uses_a_new_key() -> None:
    redis, clock = fake_redis(), ManualClock()
    limiter = RedisRateLimiter(redis, clock)
    await limiter.hit("k", LIMIT)
    first = redis.pipeline.return_value.incr.call_args.args[0]
    clock.advance(seconds=59)
    await limiter.hit("k", LIMIT)
    assert redis.pipeline.return_value.incr.call_args.args[0] == first
    clock.advance(seconds=1)
    await limiter.hit("k", LIMIT)
    assert redis.pipeline.return_value.incr.call_args.args[0] != first


async def test_release_decrements_the_current_window_and_keeps_an_expiry() -> None:
    redis = fake_redis()
    redis.pipeline.return_value.execute = AsyncMock(return_value=[2, True])
    await RedisRateLimiter(redis, ManualClock()).release("k", LIMIT)
    pipeline = redis.pipeline.return_value
    name = pipeline.decr.call_args.args[0]
    assert name == f"maya:rl:k:{int(START.timestamp()) // 60}"
    pipeline.expire.assert_called_once_with(name, 120)


async def test_release_never_leaves_a_negative_count() -> None:
    redis = fake_redis()
    redis.pipeline.return_value.execute = AsyncMock(return_value=[-1, True])
    redis.incr = AsyncMock(return_value=0)
    await RedisRateLimiter(redis, ManualClock()).release("k", LIMIT)
    redis.incr.assert_awaited_once()


async def test_backend_failures_raise_unavailable_for_callers_to_fail_closed() -> None:
    redis = fake_redis()
    redis.pipeline.return_value.execute = AsyncMock(side_effect=RedisConnectionError("private"))
    limiter = RedisRateLimiter(redis, ManualClock())
    with pytest.raises(RateLimiterUnavailable):
        await limiter.hit("k", LIMIT)
    with pytest.raises(RateLimiterUnavailable):
        await limiter.release("k", LIMIT)


async def test_the_fake_limiter_used_by_other_tests_has_the_same_window_arithmetic() -> None:
    clock = ManualClock()
    fake = FakeRateLimiter(clock)
    assert [await fake.hit("k", LIMIT) for _ in range(5)] == [True, True, True, False, False]
    for _ in range(3):
        await fake.release("k", LIMIT)
    assert [await fake.hit("k", LIMIT) for _ in range(2)] == [True, False]
    clock.advance(seconds=60)
    assert await fake.hit("k", LIMIT) is True
    for _ in range(5):  # never below zero
        await fake.release("k", LIMIT)
    assert [await fake.hit("k", LIMIT) for _ in range(4)] == [True, True, True, False]


@pytest.fixture
async def real_redis() -> AsyncIterator[Redis]:
    url = os.environ.get("MAYA_TEST_REDIS_URL")
    env_file = os.environ.get("MAYA_TEST_ENV_FILE")
    if url is None and env_file:
        url = dotenv_values(env_file).get("MAYA_REDIS_URL")
    if not url:
        if os.environ.get("MAYA_REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Local Redis test target is not configured")
        pytest.skip("Local Redis test target is not configured")
    assert url.startswith(("redis://127.0.0.1", "redis://localhost"))
    client = Redis.from_url(url, decode_responses=True, socket_timeout=2)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.redis
async def test_against_real_redis_counts_expire_and_windows_roll(real_redis: Redis) -> None:
    clock = ManualClock()
    limiter = RedisRateLimiter(real_redis, clock)
    key = f"test-{uuid4().hex}"
    names: list[str] = []
    try:
        assert [await limiter.hit(key, LIMIT) for _ in range(5)] == [True, True, True, False, False]
        names = [name async for name in real_redis.scan_iter(f"maya:rl:{key}:*")]
        assert len(names) == 1
        assert 0 < await real_redis.ttl(names[0]) <= 120
        for _ in range(3):
            await limiter.release(key, LIMIT)
        assert [await limiter.hit(key, LIMIT) for _ in range(2)] == [True, False]
        clock.advance(seconds=60)
        assert await limiter.hit(key, LIMIT) is True
        await limiter.release(key, LIMIT)
        await limiter.release(key, LIMIT)  # below zero is corrected, and the key keeps expiring
        assert int(await real_redis.get(names[0]) or 0) >= 0
        names = [name async for name in real_redis.scan_iter(f"maya:rl:{key}:*")]
        assert len(names) == 2
    finally:
        if names:
            await real_redis.delete(*names)
