import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

from app.core.resources import DependencyUnavailable, Resources
from tests.support.settings import make_settings


@pytest.fixture
def resources() -> Iterator[tuple[Resources, MagicMock, AsyncMock]]:
    with (
        patch("app.core.resources.create_database_engine") as database_factory,
        patch("app.core.resources.create_redis_client") as redis_factory,
    ):
        engine = MagicMock()
        engine.dispose = AsyncMock()
        connection = AsyncMock()
        engine.connect.return_value.__aenter__.return_value = connection
        database_factory.return_value = engine
        redis = AsyncMock()
        redis.ping.return_value = True
        redis_factory.return_value = redis
        settings = make_settings()
        yield Resources(settings), engine, redis


async def test_checks_use_a_database_query_and_redis_ping(
    resources: tuple[Resources, MagicMock, AsyncMock],
) -> None:
    manager, engine, redis = resources
    await manager.check()
    connection = engine.connect.return_value.__aenter__.return_value
    connection.execute.assert_awaited_once()
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"
    redis.ping.assert_awaited_once()


async def test_database_failure_is_mapped_to_generic_error(
    resources: tuple[Resources, MagicMock, AsyncMock],
) -> None:
    manager, engine, redis = resources
    engine.connect.return_value.__aenter__.side_effect = OperationalError(
        "private query", None, RuntimeError("private connection")
    )
    with pytest.raises(DependencyUnavailable, match="Required local service unavailable"):
        await manager.check()
    redis.ping.assert_not_awaited()


@pytest.mark.parametrize("failure", [RedisConnectionError("private"), TimeoutError()])
async def test_redis_failure_is_mapped_to_generic_error(
    resources: tuple[Resources, MagicMock, AsyncMock], failure: Exception
) -> None:
    manager, _, redis = resources
    redis.ping.side_effect = failure
    with pytest.raises(DependencyUnavailable):
        await manager.check()


async def test_unexpected_error_is_not_swallowed(
    resources: tuple[Resources, MagicMock, AsyncMock],
) -> None:
    manager, _, redis = resources
    redis.ping.side_effect = TypeError("programming error")
    with pytest.raises(TypeError, match="programming error"):
        await manager.check()


async def test_cancellation_is_propagated(
    resources: tuple[Resources, MagicMock, AsyncMock],
) -> None:
    manager, _, redis = resources
    redis.ping.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await manager.check()


async def test_false_ping_fails_readiness(
    resources: tuple[Resources, MagicMock, AsyncMock],
) -> None:
    manager, _, redis = resources
    redis.ping.return_value = False
    with pytest.raises(DependencyUnavailable):
        await manager.check()


async def test_both_pools_are_closed_even_if_redis_cleanup_fails(
    resources: tuple[Resources, MagicMock, AsyncMock],
) -> None:
    manager, engine, redis = resources
    redis.aclose.side_effect = RedisConnectionError("private")
    with pytest.raises(RedisConnectionError):
        await manager.close()
    engine.dispose.assert_awaited_once()
