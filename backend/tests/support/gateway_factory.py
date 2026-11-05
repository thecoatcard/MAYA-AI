"""Shared builders for Model Gateway tests (settings, fake-backed gateway, tracker mock)."""

from unittest.mock import AsyncMock

from pydantic import SecretStr

from app.core.config import Settings
from app.gateway.cost_tracker import BudgetTracker
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import ModelRegistry, build_default_registry
from app.gateway.resilience import ResiliencePolicy
from tests.support.fake_gemini_client import FakeGeminiClient


def make_settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "database_url": SecretStr("postgresql+asyncpg://maya_app:secret@127.0.0.1:15432/maya"),
        "redis_url": SecretStr("redis://127.0.0.1:16379/0"),
        "jwt_signing_key": SecretStr("1234567890123456789012345678901234567890"),
        "otp_hmac_key": SecretStr("abcdefghijklmnopqrstuvwxyz0123456789abcdef"),
        "gemini_api_key": SecretStr("example-fake-gemini-key-for-test"),
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def make_tracker_mock() -> AsyncMock:
    """A BudgetTracker double whose async methods are awaitable no-ops."""
    tracker = AsyncMock(spec=BudgetTracker)
    tracker.record_usage = AsyncMock()
    tracker.ensure_within_budget = AsyncMock()
    return tracker


def make_gateway(
    client: FakeGeminiClient,
    *,
    tracker: AsyncMock | None = None,
    sleep: AsyncMock | None = None,
    registry: ModelRegistry | None = None,
    **settings_kwargs: object,
) -> ModelGateway:
    return ModelGateway(
        registry=registry or build_default_registry(),
        settings=make_settings(**settings_kwargs),
        client=client,
        budget_tracker=tracker,
        resilience_policy=ResiliencePolicy(
            sleep=sleep or AsyncMock(), random_uniform=lambda _low, high: high
        ),
    )
