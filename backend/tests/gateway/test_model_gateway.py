"""Unit tests for Model Gateway execution engine: 6 operations, contracts, and fallbacks."""

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from google.genai.errors import APIError
from pydantic import BaseModel, Field, SecretStr

from app.core.config import Settings
from app.gateway.cost_tracker import BudgetTracker
from app.gateway.exceptions import (
    EmbeddingDimensionMismatchError,
    GatewayConfigurationError,
    GatewayFallbackExhaustedError,
    StructuredOutputError,
    UnsupportedAliasTransportError,
)
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import build_default_registry
from app.gateway.resilience import ResiliencePolicy
from app.gateway.schemas import ToolDefinition
from tests.support.fake_gemini_client import FakeGeminiClient


def make_gateway_settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "database_url": SecretStr("postgresql+asyncpg://maya_app:secret@127.0.0.1:15432/maya"),
        "redis_url": SecretStr("redis://127.0.0.1:16379/0"),
        "jwt_signing_key": SecretStr("1234567890123456789012345678901234567890"),
        "otp_hmac_key": SecretStr("abcdefghijklmnopqrstuvwxyz0123456789abcdef"),
        "gemini_api_key": SecretStr("example-fake-gemini-key-for-test"),
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


class SampleOutput(BaseModel):
    summary: str = Field(min_length=1)
    score: int = Field(ge=0, le=100)


@pytest.fixture
def fake_client() -> FakeGeminiClient:
    return FakeGeminiClient()


@pytest.fixture
def mock_budget_tracker() -> BudgetTracker:
    tracker = AsyncMock(spec=BudgetTracker)
    tracker.record_usage = AsyncMock()
    return tracker


@pytest.fixture
def gateway(fake_client: FakeGeminiClient, mock_budget_tracker: BudgetTracker) -> ModelGateway:
    registry = build_default_registry()
    settings = make_gateway_settings()
    resilience = ResiliencePolicy(sleep=AsyncMock())
    return ModelGateway(
        registry=registry,
        settings=settings,
        client=fake_client,
        budget_tracker=mock_budget_tracker,
        resilience_policy=resilience,
    )


@pytest.mark.asyncio
async def test_generate_text_happy_path(
    gateway: ModelGateway, fake_client: FakeGeminiClient, mock_budget_tracker: AsyncMock
) -> None:
    fake_client.aio.models.default_text = "Generated text from orchestrator"
    fake_client.aio.models.prompt_token_count = 100
    fake_client.aio.models.candidates_token_count = 50

    workspace_id = uuid4()
    task_id = uuid4()

    response = await gateway.generate_text(
        alias="orchestrator",
        contents=["Hello world"],
        task_id=task_id,
        workspace_id=workspace_id,
    )

    assert response.text == "Generated text from orchestrator"
    assert response.alias_used == "orchestrator"
    assert response.model_id == "gemini-3.8-flash"
    assert response.was_fallback is False
    assert response.usage.prompt_tokens == 100
    assert response.usage.candidates_tokens == 50
    # orchestrator pricing: input $0.75 / 1M, output $3.75 / 1M
    # cost = (100 * 0.75 + 50 * 3.75) / 1M = (75 + 187.5) / 1M = 262.5 / 1M = 0.0002625
    assert response.usage.estimated_cost_usd == Decimal("0.0002625")
    assert response.usage.is_estimated_cost_complete is True

    # Budget tracker recorded usage
    mock_budget_tracker.record_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_text_with_fallback(
    gateway: ModelGateway, fake_client: FakeGeminiClient
) -> None:
    # Make gemini-3.8-flash (orchestrator) fail with 500
    fake_client.aio.models.enqueue_errors("gemini-3.8-flash", [APIError(500, "Crash")])
    fake_client.aio.models.default_text = "Fallback text from lite"

    response = await gateway.generate_text(
        alias="orchestrator",
        contents=["Hello"],
        allow_fallback=True,
    )

    assert response.text == "Fallback text from lite"
    assert response.alias_used == "lite"
    assert response.model_id == "gemini-3.5-flash-lite"
    assert response.was_fallback is True


@pytest.mark.asyncio
async def test_generate_structured_success_first_turn(
    gateway: ModelGateway, fake_client: FakeGeminiClient
) -> None:
    fake_client.aio.models.default_text = '```json\n{"summary": "All good", "score": 95}\n```'

    result = await gateway.generate_structured(
        alias="orchestrator",
        contents=["Analyze"],
        response_schema=SampleOutput,
    )

    assert isinstance(result, SampleOutput)
    assert result.summary == "All good"
    assert result.score == 95


@pytest.mark.asyncio
async def test_generate_structured_repaired_second_turn(
    gateway: ModelGateway, fake_client: FakeGeminiClient
) -> None:
    # First turn returns invalid JSON; second turn (repair turn) returns valid JSON
    fake_client.aio.models.queued_text_responses = [
        '{"summary": "Missing score"}',  # Schema validation failure
        '{"summary": "Repaired", "score": 80}',  # Valid
    ]

    result = await gateway.generate_structured(
        alias="orchestrator",
        contents=["Analyze"],
        response_schema=SampleOutput,
    )

    assert isinstance(result, SampleOutput)
    assert result.summary == "Repaired"
    assert result.score == 80


@pytest.mark.asyncio
async def test_generate_structured_repair_fails_raises_structured_output_error(
    gateway: ModelGateway, fake_client: FakeGeminiClient
) -> None:
    # Both turns return invalid JSON
    fake_client.aio.models.default_text = "This is not JSON at all"

    with pytest.raises(
        StructuredOutputError,
        match="Structured output schema validation failed after 1 self-repair attempt",
    ):
        await gateway.generate_structured(
            alias="orchestrator",
            contents=["Analyze"],
            response_schema=SampleOutput,
        )


@pytest.mark.asyncio
async def test_generate_with_tools_emits_call_requests(
    gateway: ModelGateway, fake_client: FakeGeminiClient, mock_budget_tracker: AsyncMock
) -> None:
    fake_client.aio.models.default_tool_calls = [
        {"name": "fetch_stock_price", "args": {"symbol": "GOOG"}}
    ]

    tools = [
        ToolDefinition(
            name="fetch_stock_price",
            description="Fetch current stock price",
            parameters={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        )
    ]

    workspace_id = uuid4()
    task_id = uuid4()

    response = await gateway.generate_with_tools(
        alias="orchestrator",
        contents=["What is GOOG stock price?"],
        tools=tools,
        task_id=task_id,
        workspace_id=workspace_id,
    )

    assert len(response.call_requests) == 1
    assert response.call_requests[0].tool_name == "fetch_stock_price"
    assert response.call_requests[0].arguments == {"symbol": "GOOG"}

    # Budget tracker recorded with is_tool_turn=True
    mock_budget_tracker.record_usage.assert_awaited_once()
    assert mock_budget_tracker.record_usage.call_args.kwargs["is_tool_turn"] is True


@pytest.mark.asyncio
async def test_embed_content_dimension_768_enforced(gateway: ModelGateway) -> None:
    response = await gateway.embed_content(
        alias="embed",
        contents="Hello text to embed",
    )
    assert len(response.vectors) == 1
    assert len(response.vectors[0]) == 768
    assert response.model_id == "gemini-embedding-2"


@pytest.mark.asyncio
async def test_embed_content_dimension_mismatch_raises(
    gateway: ModelGateway, fake_client: FakeGeminiClient
) -> None:
    fake_client.aio.models.embedding_dimension = 512

    with pytest.raises(EmbeddingDimensionMismatchError, match="expected exactly 768 dimensions"):
        await gateway.embed_content(
            alias="embed",
            contents="Vector length mismatch",
        )


@pytest.mark.asyncio
async def test_embed_content_no_fallback_by_default(
    gateway: ModelGateway, fake_client: FakeGeminiClient
) -> None:
    fake_client.aio.models.enqueue_errors("gemini-embedding-2", [APIError(500, "Embed error")])

    with pytest.raises(GatewayFallbackExhaustedError):
        # Default allow_fallback=False
        await gateway.embed_content(
            alias="embed",
            contents="Will fail without fallback",
        )


@pytest.mark.asyncio
async def test_embed_content_fallback_opt_in(
    gateway: ModelGateway, fake_client: FakeGeminiClient
) -> None:
    fake_client.aio.models.enqueue_errors("gemini-embedding-2", [APIError(500, "Embed error")])

    response = await gateway.embed_content(
        alias="embed",
        contents="Will succeed on fallback model",
        allow_fallback=True,
    )
    assert response.model_id == "gemini-embedding-001"


@pytest.mark.asyncio
async def test_count_tokens(gateway: ModelGateway) -> None:
    response = await gateway.count_tokens(alias="orchestrator", contents=["Short text"])
    assert response.total_tokens == 42
    assert response.model_id == "gemini-3.8-flash"


@pytest.mark.asyncio
async def test_unsupported_alias_transport_raises(gateway: ModelGateway) -> None:
    # 'live' uses websocket transport
    with pytest.raises(UnsupportedAliasTransportError, match="uses transport 'websocket'"):
        await gateway.generate_text(alias="live", contents=["Hi"])

    # 'research' uses managed_agent transport
    with pytest.raises(UnsupportedAliasTransportError, match="uses transport 'managed_agent'"):
        await gateway.generate_text(alias="research", contents=["Hi"])


@pytest.mark.asyncio
async def test_scope_validation_task_id_requires_workspace_id(gateway: ModelGateway) -> None:
    with pytest.raises(ValueError, match="workspace_id is required whenever task_id is specified"):
        await gateway.generate_text(
            alias="orchestrator",
            contents=["Hi"],
            task_id=uuid4(),
            workspace_id=None,
        )


@pytest.mark.asyncio
async def test_client_uninitialized_raises_gateway_configuration_error() -> None:
    registry = build_default_registry()
    settings = make_gateway_settings(gemini_api_key=None)
    gateway = ModelGateway(registry=registry, settings=settings, client=None)

    with pytest.raises(GatewayConfigurationError, match="Gemini client is not initialized"):
        await gateway.generate_text(alias="orchestrator", contents=["Hi"])
