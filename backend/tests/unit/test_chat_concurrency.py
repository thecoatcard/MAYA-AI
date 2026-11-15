"""Redis-backed chat stream lease behavior."""

import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from dotenv import dotenv_values
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.chat_streams import ChatStreamLimiterUnavailable, RedisChatStreamLimiter

WORKSPACE_ID = UUID("b5714b26-39e2-490f-a1e2-10e43394d4c2")
USER_ID = UUID("4b0d2384-3c22-4fb0-969e-578b08ac67da")


def redis_mock() -> Mock:
    redis = Mock(spec=Redis)
    redis.eval = AsyncMock()
    return redis


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


@pytest.mark.asyncio
async def test_acquires_a_lease_scoped_to_workspace_and_user() -> None:
    redis = redis_mock()
    redis.eval.return_value = 1
    limiter = RedisChatStreamLimiter(redis)

    lease = await limiter.acquire(WORKSPACE_ID, USER_ID, limit=2, lease_seconds=150)

    assert lease is not None
    assert lease.key == f"maya:chat:streams:{WORKSPACE_ID}:{USER_ID}"
    assert len(lease.member) == 32
    script, keys, key, limit, ttl_ms, member = redis.eval.await_args.args
    assert "ZREMRANGEBYSCORE" in script
    assert (keys, key, limit, ttl_ms, member) == (1, lease.key, 2, 150_000, lease.member)


@pytest.mark.asyncio
async def test_returns_no_lease_when_the_concurrency_limit_is_full() -> None:
    redis = redis_mock()
    redis.eval.return_value = 0

    lease = await RedisChatStreamLimiter(redis).acquire(
        WORKSPACE_ID, USER_ID, limit=2, lease_seconds=150
    )

    assert lease is None


@pytest.mark.asyncio
async def test_release_removes_only_the_owned_lease() -> None:
    redis = redis_mock()
    redis.eval.side_effect = [1, 1]
    limiter = RedisChatStreamLimiter(redis)
    lease = await limiter.acquire(WORKSPACE_ID, USER_ID, limit=2, lease_seconds=150)
    assert lease is not None

    await limiter.release(lease)

    script, keys, key, member = redis.eval.await_args.args
    assert "ZREM" in script
    assert (keys, key, member) == (1, lease.key, lease.member)


@pytest.mark.asyncio
async def test_renew_extends_only_a_live_owned_lease() -> None:
    redis = redis_mock()
    redis.eval.side_effect = [1, 1]
    limiter = RedisChatStreamLimiter(redis)
    lease = await limiter.acquire(WORKSPACE_ID, USER_ID, limit=2, lease_seconds=150)
    assert lease is not None

    assert await limiter.renew(lease, lease_seconds=300)

    script, keys, key, member, ttl_ms = redis.eval.await_args.args
    assert "ZREMRANGEBYSCORE" in script and "ZSCORE" in script and "'XX'" in script
    assert (keys, key, member, ttl_ms) == (1, lease.key, lease.member, 300_000)


@pytest.mark.asyncio
async def test_renew_reports_a_lease_that_has_already_expired() -> None:
    redis = redis_mock()
    redis.eval.side_effect = [1, 0]
    limiter = RedisChatStreamLimiter(redis)
    lease = await limiter.acquire(WORKSPACE_ID, USER_ID, limit=2, lease_seconds=150)
    assert lease is not None

    assert not await limiter.renew(lease, lease_seconds=300)


@pytest.mark.asyncio
async def test_renew_redis_errors_fail_closed() -> None:
    redis = redis_mock()
    redis.eval.side_effect = [1, RedisError("unavailable")]
    limiter = RedisChatStreamLimiter(redis)
    lease = await limiter.acquire(WORKSPACE_ID, USER_ID, limit=2, lease_seconds=150)
    assert lease is not None

    with pytest.raises(ChatStreamLimiterUnavailable):
        await limiter.renew(lease, lease_seconds=300)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["acquire", "release"])
async def test_redis_errors_fail_closed(operation: str) -> None:
    redis = redis_mock()
    redis.eval.side_effect = (
        RedisError("unavailable") if operation == "acquire" else [1, RedisError("unavailable")]
    )
    limiter = RedisChatStreamLimiter(redis)

    with pytest.raises(ChatStreamLimiterUnavailable):
        if operation == "acquire":
            await limiter.acquire(WORKSPACE_ID, USER_ID, limit=2, lease_seconds=150)
        else:
            lease = await limiter.acquire(WORKSPACE_ID, USER_ID, limit=2, lease_seconds=150)
            assert lease is not None
            await limiter.release(lease)


@pytest.mark.asyncio
async def test_invalid_lease_configuration_is_rejected_before_redis_access() -> None:
    redis = redis_mock()
    limiter = RedisChatStreamLimiter(redis)

    with pytest.raises(ValueError):
        await limiter.acquire(WORKSPACE_ID, USER_ID, limit=0, lease_seconds=1)
    assert not redis.eval.called


@pytest.mark.redis
async def test_real_redis_enforces_the_user_cap_and_reclaims_expired_leases(
    real_redis: Redis,
) -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    limiter = RedisChatStreamLimiter(real_redis)
    first = await limiter.acquire(workspace_id, user_id, limit=2, lease_seconds=60)
    second = await limiter.acquire(workspace_id, user_id, limit=2, lease_seconds=60)
    assert first is not None and second is not None
    assert await limiter.acquire(workspace_id, user_id, limit=2, lease_seconds=60) is None

    await limiter.release(first)
    third = await limiter.acquire(workspace_id, user_id, limit=2, lease_seconds=60)
    assert third is not None
    await limiter.release(second)
    await limiter.release(third)

    expired = await limiter.acquire(workspace_id, user_id, limit=1, lease_seconds=60)
    assert expired is not None
    await real_redis.zadd(expired.key, {expired.member: 0})
    reclaimed = await limiter.acquire(workspace_id, user_id, limit=1, lease_seconds=60)
    assert reclaimed is not None
    await limiter.release(expired)
    await limiter.release(reclaimed)
