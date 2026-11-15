"""Unit and integration tests for ContextCacheManager and 404 recovery."""

import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from redis.asyncio import Redis

from app.gateway.cache_manager import (
    CACHE_KEY_PREFIX,
    ContextCacheManager,
    compute_content_hash,
)


def test_compute_content_hash_deterministic() -> None:
    text = "System instruction for coding assistant"
    hash1 = compute_content_hash(text)
    hash2 = compute_content_hash(text)
    assert hash1 == hash2
    assert len(hash1) == 64

    # Dict hashing is order-insensitive
    d1 = {"a": 1, "b": 2}
    d2 = {"b": 2, "a": 1}
    assert compute_content_hash(d1) == compute_content_hash(d2)


@pytest.mark.asyncio
async def test_cache_key_structure() -> None:
    mock_redis = AsyncMock(spec=Redis)
    manager = ContextCacheManager(mock_redis)

    workspace_id = uuid4()
    model_id = "gemini-4-hydrogen"
    content_hash = "abc123hash"

    key = manager.make_cache_key(workspace_id, model_id, content_hash)
    assert key == f"{CACHE_KEY_PREFIX}:{workspace_id}:{model_id}:{content_hash}"


@pytest.mark.asyncio
async def test_get_and_set_cached_content_name() -> None:
    mock_redis = AsyncMock(spec=Redis)
    mock_redis.get = AsyncMock(return_value="cachedContents/res-12345")
    mock_redis.set = AsyncMock()

    manager = ContextCacheManager(mock_redis)
    workspace_id = uuid4()
    model_id = "gemini-4-hydrogen"
    content_hash = "abc123hash"

    # Set
    await manager.set_cached_content_name(
        workspace_id=workspace_id,
        model_id=model_id,
        content_hash=content_hash,
        cache_name="cachedContents/res-12345",
        ttl_seconds=1800,
    )
    expected_key = manager.make_cache_key(workspace_id, model_id, content_hash)
    mock_redis.set.assert_awaited_once_with(expected_key, "cachedContents/res-12345", ex=1800)

    # Get
    res = await manager.get_cached_content_name(workspace_id, model_id, content_hash)
    assert res == "cachedContents/res-12345"
    mock_redis.get.assert_awaited_once_with(expected_key)


@pytest.mark.asyncio
async def test_workspace_id_mandatory_for_cache_operations() -> None:
    mock_redis = AsyncMock(spec=Redis)
    manager = ContextCacheManager(mock_redis)

    with pytest.raises(ValueError, match="workspace_id is mandatory"):
        await manager.get_cached_content_name(None, "model", "hash")

    with pytest.raises(ValueError, match="workspace_id is mandatory"):
        await manager.set_cached_content_name(None, "model", "hash", "cache_name")

    with pytest.raises(ValueError, match="workspace_id is mandatory"):
        await manager.invalidate_cache(None, "model", "hash")


@pytest.mark.asyncio
async def test_handle_provider_cache_404_purges_local_key() -> None:
    mock_redis = AsyncMock(spec=Redis)
    mock_redis.delete = AsyncMock()

    manager = ContextCacheManager(mock_redis)
    workspace_id = uuid4()
    model_id = "gemini-4-hydrogen"
    content_hash = "abc123hash"

    await manager.handle_provider_cache_404(workspace_id, model_id, content_hash)
    expected_key = manager.make_cache_key(workspace_id, model_id, content_hash)
    mock_redis.delete.assert_awaited_once_with(expected_key)


# -----------------------------------------------------------------------------
# Real Redis Integration Test
# -----------------------------------------------------------------------------


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
@pytest.mark.asyncio
async def test_context_cache_against_real_redis(real_redis: Redis) -> None:
    manager = ContextCacheManager(real_redis)
    workspace_id = uuid4()
    model_id = "gemini-4-hydrogen"
    content_hash = f"test_{uuid4().hex}"
    key = manager.make_cache_key(workspace_id, model_id, content_hash)

    try:
        assert await manager.get_cached_content_name(workspace_id, model_id, content_hash) is None

        await manager.set_cached_content_name(
            workspace_id, model_id, content_hash, "cachedContents/real-12345", ttl_seconds=3600
        )
        cached = await manager.get_cached_content_name(workspace_id, model_id, content_hash)
        assert cached == "cachedContents/real-12345"

        ttl = await real_redis.ttl(key)
        assert 0 < ttl <= 3600

        await manager.handle_provider_cache_404(workspace_id, model_id, content_hash)
        assert await manager.get_cached_content_name(workspace_id, model_id, content_hash) is None
    finally:
        await real_redis.delete(key)
