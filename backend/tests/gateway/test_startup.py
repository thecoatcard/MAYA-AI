"""Unit tests for Model Gateway startup verification and remote discovery."""

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.gateway.exceptions import GatewayConfigurationError
from app.gateway.registry import ModelRegistry, ModelSpec, build_default_registry
from app.gateway.startup import verify_gateway_at_startup
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.models import HEAVY, LITE


def make_startup_settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "database_url": SecretStr("postgresql+asyncpg://maya_app:secret@127.0.0.1:15432/maya"),
        "redis_url": SecretStr("redis://127.0.0.1:16379/0"),
        "jwt_signing_key": SecretStr("1234567890123456789012345678901234567890"),
        "otp_hmac_key": SecretStr("abcdefghijklmnopqrstuvwxyz0123456789abcdef"),
        "gemini_api_key": SecretStr("example-fake-gemini-key-for-test"),
        "gemini_verify_models_on_startup": False,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_startup_offline_validation_catches_invalid_configuration() -> None:
    # Build invalid registry where an enabled alias references an unknown fallback
    specs = {
        "broken": ModelSpec(
            alias="broken",
            model_id="gemini-4-hydrogen",
            fallback_alias="non_existent",
            context_input_limit=1000,
            context_output_limit=1000,
            cost_per_1m_input=None,
            cost_per_1m_output=None,
            cost_per_1m_cached=None,
            enabled=True,
        )
    }
    registry = ModelRegistry(specs)
    settings = make_startup_settings()

    with pytest.raises(GatewayConfigurationError, match="unknown fallback alias"):
        await verify_gateway_at_startup(registry, settings)


@pytest.mark.asyncio
async def test_startup_remote_check_disabled_by_default() -> None:
    registry = build_default_registry()
    settings = make_startup_settings(gemini_verify_models_on_startup=False)
    fake_client = FakeGeminiClient()

    # Must pass without calling client.aio.models.list
    await verify_gateway_at_startup(registry, settings, client=fake_client)
    assert not any(c["method"] == "list" for c in fake_client.aio.models.calls)


@pytest.mark.asyncio
async def test_startup_remote_check_skipped_when_key_unset() -> None:
    registry = build_default_registry()
    # Remote verification enabled, but key is unset
    settings = make_startup_settings(
        gemini_verify_models_on_startup=True,
        gemini_api_key=None,
    )
    fake_client = FakeGeminiClient()

    await verify_gateway_at_startup(registry, settings, client=fake_client)
    assert not any(c["method"] == "list" for c in fake_client.aio.models.calls)


@pytest.mark.asyncio
async def test_startup_remote_check_success_when_models_present() -> None:
    registry = build_default_registry()
    settings = make_startup_settings(gemini_verify_models_on_startup=True)
    fake_client = FakeGeminiClient()

    # The fake client lists every model the default registry verifies at startup.
    await verify_gateway_at_startup(registry, settings, client=fake_client)
    assert any(c["method"] == "list" for c in fake_client.aio.models.calls)


@pytest.mark.asyncio
async def test_startup_remote_check_marks_missing_model_unavailable() -> None:
    registry = build_default_registry()
    settings = make_startup_settings(gemini_verify_models_on_startup=True)
    fake_client = FakeGeminiClient()

    # Remove the orchestrator model from the list
    fake_client.aio.models.available_models = [
        f"models/{HEAVY}",
        f"models/{LITE}",
    ]

    # Deliberate change: a missing model is logged and marked unavailable (fallback chains skip
    # it); it no longer stops the application from starting.
    await verify_gateway_at_startup(registry, settings, client=fake_client)
    assert registry.is_unavailable("orchestrator")
    assert registry.candidate_chain("heavy") == ["heavy", "lite"]
