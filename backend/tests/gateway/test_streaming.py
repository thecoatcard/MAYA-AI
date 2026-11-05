"""Unit tests for Model Gateway streaming and interruption handling."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.gateway.cost_tracker import BudgetTracker
from app.gateway.exceptions import StreamInterruptedError
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import build_default_registry
from app.gateway.resilience import ResiliencePolicy
from tests.support.fake_gemini_client import FakeGeminiClient


def make_gateway_settings() -> Settings:
    return Settings(
        database_url=SecretStr("postgresql+asyncpg://maya_app:secret@127.0.0.1:15432/maya"),
        redis_url=SecretStr("redis://127.0.0.1:16379/0"),
        jwt_signing_key=SecretStr("1234567890123456789012345678901234567890"),
        otp_hmac_key=SecretStr("abcdefghijklmnopqrstuvwxyz0123456789abcdef"),
        gemini_api_key=SecretStr("example-fake-gemini-key-for-test"),
    )


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
async def test_streaming_normal_completion(
    gateway: ModelGateway, fake_client: FakeGeminiClient, mock_budget_tracker: AsyncMock
) -> None:
    fake_client.aio.models.default_text = "One two three four"
    workspace_id = uuid4()
    task_id = uuid4()

    chunks: list[str] = []
    async for chunk in gateway.generate_stream(
        alias="orchestrator",
        contents=["Count"],
        task_id=task_id,
        workspace_id=workspace_id,
    ):
        chunks.append(chunk.text)
        assert chunk.model_id == "gemini-3.8-flash"
        assert chunk.alias_used == "orchestrator"

    assert "".join(chunks) == "One two three four"
    # Usage recorded on completion
    mock_budget_tracker.record_usage.assert_awaited_once()
    usage = mock_budget_tracker.record_usage.call_args.kwargs["usage"]
    assert usage.is_usage_estimate is False


@pytest.mark.asyncio
async def test_streaming_midstream_drop_raises_stream_interrupted(
    gateway: ModelGateway, fake_client: FakeGeminiClient, mock_budget_tracker: AsyncMock
) -> None:
    fake_client.aio.models.default_text = "One two three four five six"
    # Drop stream after 2 chunks
    fake_client.aio.models.stream_error_after_chunk = 2

    workspace_id = uuid4()
    task_id = uuid4()

    received: list[str] = []
    with pytest.raises(StreamInterruptedError) as exc_info:
        async for chunk in gateway.generate_stream(
            alias="orchestrator",
            contents=["Stream this"],
            task_id=task_id,
            workspace_id=workspace_id,
        ):
            received.append(chunk.text)

    interrupted = exc_info.value
    assert interrupted.chunks_yielded == 2
    assert "One" in interrupted.partial_text
    assert interrupted.partial_usage is not None
    assert interrupted.partial_usage.is_usage_estimate is True

    # Budget tracker recorded the partial usage estimate
    mock_budget_tracker.record_usage.assert_awaited_once()
    usage = mock_budget_tracker.record_usage.call_args.kwargs["usage"]
    assert usage.is_usage_estimate is True
