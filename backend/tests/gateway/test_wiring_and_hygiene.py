"""Gateway wiring into application resources, and log/error hygiene outside the call path."""

import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from app.core.resources import Resources
from app.gateway.exceptions import GatewayConfigurationError, GatewayStartupError
from app.gateway.registry import build_default_registry
from app.gateway.startup import verify_gateway_at_startup
from app.gateway.validator import parse_and_validate_json
from app.main import create_app
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.gateway_factory import make_settings


class Rated(BaseModel):
    title: str = Field(min_length=1)
    stars: int = Field(ge=1, le=5)


@pytest.fixture
def backing_services() -> Iterator[tuple[MagicMock, AsyncMock]]:
    """Replace the database engine and Redis client so Resources can be built offline."""
    with (
        patch("app.core.resources.create_database_engine") as database_factory,
        patch("app.core.resources.create_redis_client") as redis_factory,
    ):
        engine = MagicMock()
        engine.dispose = AsyncMock()
        engine.connect.return_value.__aenter__.return_value = AsyncMock()
        database_factory.return_value = engine
        redis = AsyncMock()
        redis.ping.return_value = True
        redis_factory.return_value = redis
        yield engine, redis


@pytest.mark.asyncio
async def test_without_a_key_the_gateway_exists_but_is_inert(
    backing_services: tuple[MagicMock, AsyncMock],
) -> None:
    resources = Resources(make_settings(gemini_api_key=None))

    assert resources.model_gateway.client is None
    with pytest.raises(GatewayConfigurationError, match="not initialized"):
        await resources.model_gateway.generate_text("orchestrator", ["hello"])
    await resources.close()  # nothing to close for the gateway; engine and redis still close


@pytest.mark.asyncio
async def test_close_releases_the_provider_client_redis_and_engine(
    backing_services: tuple[MagicMock, AsyncMock],
) -> None:
    engine, redis = backing_services
    client = MagicMock()
    client.aio.aclose = AsyncMock()

    with patch("app.gateway.model_gateway.create_client_from_settings", return_value=client):
        resources = Resources(make_settings())
    await resources.close()

    client.aio.aclose.assert_awaited_once()
    redis.aclose.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_still_closes_redis_and_engine_when_the_client_close_fails(
    backing_services: tuple[MagicMock, AsyncMock],
) -> None:
    engine, redis = backing_services
    client = MagicMock()
    client.aio.aclose = AsyncMock(side_effect=RuntimeError("already closed"))

    with patch("app.gateway.model_gateway.create_client_from_settings", return_value=client):
        resources = Resources(make_settings())
    await resources.close()

    redis.aclose.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_hands_the_gateway_client_to_startup_verification(
    backing_services: tuple[MagicMock, AsyncMock],
) -> None:
    fake = FakeGeminiClient()
    settings = make_settings()

    with (
        patch("app.gateway.model_gateway.create_client_from_settings", return_value=fake),
        patch.object(Resources, "verify_runtime_role", AsyncMock()),
        patch("app.main.verify_gateway_at_startup", new_callable=AsyncMock) as verify,
    ):
        app = create_app(settings, auth_service=AsyncMock())
        async with app.router.lifespan_context(app):
            pass

    verify.assert_awaited_once()
    assert verify.call_args.kwargs["client"] is fake


@pytest.mark.asyncio
async def test_lifespan_closes_the_factory_owned_auth_service() -> None:
    resources = AsyncMock()
    resources.registry = None
    service = AsyncMock()

    with (
        patch("app.main.Resources", return_value=resources),
        patch("app.main.build_auth_service", return_value=service),
    ):
        app = create_app(make_settings())
        async with app.router.lifespan_context(app):
            pass

    service.aclose.assert_awaited_once()
    resources.close.assert_awaited_once()


def test_validation_summary_names_the_field_but_never_echoes_the_value() -> None:
    model, error = parse_and_validate_json('{"title": "ok", "stars": "PRIVATE-VALUE"}', Rated)

    assert model is None
    assert error is not None
    assert "stars" in error
    assert "PRIVATE-VALUE" not in error


@pytest.mark.asyncio
async def test_startup_discovery_failure_reports_only_the_error_class(
    caplog: pytest.LogCaptureFixture,
) -> None:
    leaked_text = "fake-provider-detail-that-might-echo-request-content"
    fake = FakeGeminiClient()
    fake.aio.models.list = AsyncMock(side_effect=RuntimeError(leaked_text))  # type: ignore[method-assign]
    settings = make_settings(gemini_verify_models_on_startup=True)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(GatewayStartupError) as exc_info:
            await verify_gateway_at_startup(build_default_registry(), settings, client=fake)

    assert leaked_text not in str(exc_info.value)
    assert leaked_text not in caplog.text
    assert "RuntimeError" in str(exc_info.value)
