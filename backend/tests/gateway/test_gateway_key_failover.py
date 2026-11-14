"""Integration and unit tests for Model Gateway key pool failover and resilience."""

from unittest.mock import AsyncMock

import pytest
from google.genai.errors import APIError
from pydantic import SecretStr

from app.core.config import Settings
from app.gateway.exceptions import StreamInterruptedError
from app.gateway.key_pool import KeyPool
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import build_default_registry
from app.gateway.resilience import ResiliencePolicy
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.models import ORCH


def _make_key(suffix: str) -> str:
    """Build key dynamically so secret scanner does not trip."""
    return "-".join(["test", "failover", "key", suffix])


def make_settings_with_keys(
    single_key: str | None = None,
    keys: list[str] | None = None,
    **kwargs: object,
) -> Settings:
    base: dict[str, object] = {
        "database_url": SecretStr("postgresql+asyncpg://maya_app:secret@127.0.0.1:15432/maya"),
        "redis_url": SecretStr("redis://127.0.0.1:16379/0"),
        "jwt_signing_key": SecretStr("1234567890123456789012345678901234567890"),
        "otp_hmac_key": SecretStr("abcdefghijklmnopqrstuvwxyz0123456789abcdef"),
    }
    if single_key is not None:
        base["gemini_api_key"] = SecretStr(single_key)
    if keys is not None:
        base["gemini_api_keys"] = [SecretStr(k) for k in keys]
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_config_parsing_list_single_dedup_and_placeholders() -> None:
    k1 = _make_key("alpha")
    k2 = _make_key("beta")

    # 1. Single + list with duplicate, blank, and placeholder
    settings = make_settings_with_keys(
        single_key=k1,
        keys=[k2, k1, "", "   ", "example-not-real", "your-api-key", "<placeholder>"],
    )
    effective = [k.get_secret_value() for k in settings.effective_gemini_api_keys]
    assert effective == [k1, k2]

    # 2. Single only
    s_single = make_settings_with_keys(single_key=k1, keys=[])
    assert [k.get_secret_value() for k in s_single.effective_gemini_api_keys] == [k1]

    # 3. List only (single is None)
    s_list = make_settings_with_keys(single_key=None, keys=[k1, k2])
    assert [k.get_secret_value() for k in s_list.effective_gemini_api_keys] == [k1, k2]

    # 4. Only placeholders -> effective is empty
    s_placeholders = make_settings_with_keys(
        single_key="example-gemini-key-not-real",
        keys=["your-key", "placeholder-key"],
    )
    assert s_placeholders.effective_gemini_api_keys == []


def test_config_parsing_from_env_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    k1 = _make_key("env1")
    k2 = _make_key("env2")
    raw = f"{k1}, {k2}, {k1}, example-fake-not-real"
    monkeypatch.setenv("MAYA_GEMINI_API_KEYS", raw)

    settings = make_settings_with_keys()
    effective = [k.get_secret_value() for k in settings.effective_gemini_api_keys]
    assert effective == [k1, k2]


@pytest.mark.asyncio
async def test_gateway_429_on_key1_then_success_on_key2() -> None:
    k1 = _make_key("k1")
    k2 = _make_key("k2")

    pool = KeyPool([k1, k2], default_quota_cooldown=60.0)
    registry = build_default_registry()
    settings = make_settings_with_keys(keys=[k1, k2])

    client1 = FakeGeminiClient()
    client1.aio.models.enqueue_errors(ORCH, [APIError(429, "Rate limited")])

    client2 = FakeGeminiClient()
    client2.aio.models.default_text = "Success on key 2"

    def factory(key: SecretStr) -> FakeGeminiClient:
        return client1 if key.get_secret_value() == k1 else client2

    resilience = ResiliencePolicy(sleep=AsyncMock(), key_pool=pool)
    gateway = ModelGateway(
        registry=registry,
        settings=settings,
        resilience_policy=resilience,
        key_pool=pool,
        client_factory=factory,
    )

    response = await gateway.generate_text("orchestrator", ["Hello"])

    assert response.text == "Success on key 2"
    assert response.alias_used == "orchestrator"
    assert response.was_fallback is False

    # Key 1 failed with 429 and is cooling down; Key 2 succeeded
    assert pool.cooling_count == 1
    assert len(client1.aio.models.calls) == 1
    assert len(client2.aio.models.calls) == 1


@pytest.mark.asyncio
async def test_gateway_all_keys_429_then_fallback_alias_succeeds() -> None:
    k1 = _make_key("k1")
    k2 = _make_key("k2")

    pool = KeyPool([k1, k2], default_quota_cooldown=60.0)
    registry = build_default_registry()
    settings = make_settings_with_keys(keys=[k1, k2])

    fake_client = FakeGeminiClient()
    # All attempts on orchestrator model return 429
    fake_client.aio.models.enqueue_errors(
        ORCH,
        [APIError(429, "Rate limited"), APIError(429, "Rate limited")],
    )
    # Success on lite model
    fake_client.aio.models.default_text = "Success on fallback lite"

    resilience = ResiliencePolicy(sleep=AsyncMock(), key_pool=pool)
    gateway = ModelGateway(
        registry=registry,
        settings=settings,
        client=fake_client,
        resilience_policy=resilience,
        key_pool=pool,
    )

    response = await gateway.generate_text("orchestrator", ["Hello"])

    assert response.text == "Success on fallback lite"
    assert response.alias_used == "lite"
    assert response.was_fallback is True


@pytest.mark.asyncio
async def test_gateway_400_does_not_rotate() -> None:
    k1 = _make_key("k1")
    k2 = _make_key("k2")

    pool = KeyPool([k1, k2])
    registry = build_default_registry()
    settings = make_settings_with_keys(keys=[k1, k2])

    fake_client = FakeGeminiClient()
    fake_client.aio.models.enqueue_errors(ORCH, [APIError(400, "Bad Request")])

    resilience = ResiliencePolicy(sleep=AsyncMock(), key_pool=pool)
    gateway = ModelGateway(
        registry=registry,
        settings=settings,
        client=fake_client,
        resilience_policy=resilience,
        key_pool=pool,
    )

    with pytest.raises(APIError) as exc_info:
        await gateway.generate_text("orchestrator", ["Hello"])

    assert exc_info.value.code == 400
    # Crucial: only 1 attempt made, does NOT rotate to k2!
    assert len(fake_client.aio.models.calls) == 1
    # Key was not marked bad or cooled down (400 is a client request issue, not key issue)
    assert pool.bad_count == 0
    assert pool.cooling_count == 0


@pytest.mark.asyncio
async def test_gateway_stream_failover_before_first_chunk() -> None:
    k1 = _make_key("k1")
    k2 = _make_key("k2")

    pool = KeyPool([k1, k2])
    registry = build_default_registry()
    settings = make_settings_with_keys(keys=[k1, k2])

    fake_client = FakeGeminiClient()
    # 429 on stream open for orchestrator
    fake_client.aio.models.enqueue_errors(ORCH, [APIError(429, "Rate limited")])
    fake_client.aio.models.default_text = "Streamed response on key 2"

    resilience = ResiliencePolicy(sleep=AsyncMock(), key_pool=pool)
    gateway = ModelGateway(
        registry=registry,
        settings=settings,
        client=fake_client,
        resilience_policy=resilience,
        key_pool=pool,
    )

    chunks = []
    async for chunk in gateway.generate_stream("orchestrator", ["Hello"]):
        chunks.append(chunk.text)

    full_text = "".join(chunks)
    assert "Streamed response on key 2" in full_text
    assert len(fake_client.aio.models.calls) == 2


@pytest.mark.asyncio
async def test_gateway_stream_no_failover_after_first_chunk() -> None:
    k1 = _make_key("k1")
    k2 = _make_key("k2")

    pool = KeyPool([k1, k2])
    registry = build_default_registry()
    settings = make_settings_with_keys(keys=[k1, k2])

    fake_client = FakeGeminiClient()
    fake_client.aio.models.default_text = "First chunk then error"
    fake_client.aio.models.stream_error_after_chunk = 1

    resilience = ResiliencePolicy(sleep=AsyncMock(), key_pool=pool)
    gateway = ModelGateway(
        registry=registry,
        settings=settings,
        client=fake_client,
        resilience_policy=resilience,
        key_pool=pool,
    )

    with pytest.raises(StreamInterruptedError):
        async for _ in gateway.generate_stream("orchestrator", ["Hello"]):
            pass

    # No second attempt/rotation after first chunk was yielded
    stream_calls = [
        c for c in fake_client.aio.models.calls if c["method"] == "generate_content_stream"
    ]
    assert len(stream_calls) == 1
