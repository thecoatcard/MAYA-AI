"""Contract tests pinning test doubles and client construction to the real google-genai SDK.

The streaming bug these guard against: the real ``generate_content_stream`` is a coroutine that
must be awaited before ``async for``. A double that is an async generator hides a gateway that
forgets the ``await`` and fails on every real call. Nothing here contacts the network.
"""

import inspect
from decimal import Decimal

import pytest
from google.genai.models import AsyncModels

from app.gateway.client_adapter import build_genai_client, create_client_from_settings
from app.gateway.exceptions import GatewayConfigurationError
from tests.support.fake_gemini_client import FakeAsyncModels, FakeGeminiClient
from tests.support.gateway_factory import make_gateway, make_settings

SDK_OPERATIONS = ["generate_content", "generate_content_stream", "embed_content", "count_tokens"]


@pytest.mark.parametrize("name", [*SDK_OPERATIONS, "list"])
def test_fake_models_follow_the_real_sdk_calling_convention(name: str) -> None:
    real = getattr(AsyncModels, name)
    fake = getattr(FakeAsyncModels, name)

    # Every SDK operation is `async def` returning a value; none is an async generator function.
    assert inspect.iscoroutinefunction(real), f"SDK {name} is expected to be a coroutine function"
    assert inspect.iscoroutinefunction(fake) == inspect.iscoroutinefunction(real)
    assert not inspect.isasyncgenfunction(fake)

    # The fake accepts only keyword arguments that the real method also accepts.
    real_params = set(inspect.signature(real).parameters) - {"self"}
    fake_params = set(inspect.signature(fake).parameters) - {"self"}
    assert fake_params <= real_params


def test_client_pins_gemini_developer_api_despite_ambient_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-ambient-google-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-ambient-gemini-key")

    client = build_genai_client(api_key="fake-explicit-key")

    assert client.vertexai is False
    assert client._api_client.api_key == "fake-explicit-key"


def test_client_disables_sdk_level_retries_and_sets_timeout() -> None:
    client = build_genai_client(api_key="fake-explicit-key", timeout_seconds=12.5)

    options = client._api_client._http_options
    # attempts=1 means no retries; retry_options=None would leave the SDK's implicit retry on.
    assert options.retry_options is not None
    assert options.retry_options.attempts == 1
    assert options.timeout == 12_500


@pytest.mark.parametrize("key", [None, "", "   "])
def test_client_requires_a_non_blank_key(key: str | None) -> None:
    with pytest.raises(GatewayConfigurationError, match="MAYA_GEMINI_API_KEY"):
        build_genai_client(api_key=key)


def test_no_client_is_built_without_a_configured_key() -> None:
    assert create_client_from_settings(make_settings(gemini_api_key=None)) is None
    assert create_client_from_settings(make_settings(gemini_api_key="   ")) is None


@pytest.mark.asyncio
async def test_thinking_and_tool_use_tokens_are_billed_from_real_sdk_usage_objects() -> None:
    fake = FakeGeminiClient()
    models = fake.aio.models
    models.prompt_token_count = 1_000
    models.cached_content_token_count = 200
    models.candidates_token_count = 500
    models.thoughts_token_count = 300
    models.tool_use_prompt_token_count = 100
    gateway = make_gateway(fake)

    response = await gateway.generate_text("orchestrator", ["Price this"])

    usage = response.usage
    assert usage.thoughts_token_count == 300
    assert usage.tool_use_prompt_token_count == 100
    assert usage.total_token_count == 1_900
    # orchestrator rates per 1M: input 0.75, cached 0.075, output 3.75
    #   input   (1000 - 200 cached) + 100 tool-use = 900 tokens -> 0.000675
    #   cached  200 tokens                                      -> 0.000015
    #   output  500 candidates + 300 thinking = 800 tokens      -> 0.003000
    assert usage.estimated_cost_usd == Decimal("0.003690")
    assert usage.is_estimated_cost_complete is True
    assert usage.is_usage_estimate is False
