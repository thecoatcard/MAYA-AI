"""Gateway behavior when the provider blocks, omits data, or the budget refuses a call."""

import logging
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from google.genai import types
from google.genai.errors import ClientError
from pydantic import BaseModel, Field

from app.gateway.exceptions import (
    BudgetExceededError,
    ContentBlockedError,
    GatewayError,
    StructuredOutputError,
)
from app.gateway.registry import build_default_registry
from app.gateway.schemas import ToolDefinition
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.gateway_factory import make_gateway, make_tracker_mock
from tests.support.models import ORCH


class Scored(BaseModel):
    summary: str = Field(min_length=1)
    score: int = Field(ge=0, le=100)


@pytest.mark.asyncio
async def test_blocked_prompt_raises_without_retry_or_fallback_and_is_still_metered() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.block_prompt = True
    tracker = make_tracker_mock()
    gateway = make_gateway(fake, tracker=tracker)

    with pytest.raises(ContentBlockedError, match="blocked by the provider"):
        await gateway.generate_text("orchestrator", ["Blocked request"])

    # One provider call only: a block is neither retried nor sent to another model.
    assert [c["model"] for c in fake.aio.models.calls] == [ORCH]
    tracker.record_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_response_without_usage_metadata_is_estimated_not_reported_as_free() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.omit_usage = True
    fake.aio.models.default_text = "x" * 400
    gateway = make_gateway(fake)

    response = await gateway.generate_text("orchestrator", ["A reasonably sized prompt"])

    assert response.usage.is_usage_estimate is True
    assert response.usage.prompt_tokens > 0
    assert response.usage.candidates_tokens == 100
    assert response.usage.estimated_cost_usd is not None
    assert response.usage.estimated_cost_usd > Decimal(0)


@pytest.mark.asyncio
async def test_overridden_model_cost_is_unknown_not_zero() -> None:
    fake = FakeGeminiClient()
    gateway = make_gateway(
        fake, registry=build_default_registry({"orchestrator": "gemini-custom-model"})
    )

    response = await gateway.generate_text("orchestrator", ["hi"])

    assert response.model_id == "gemini-custom-model"
    assert response.usage.price_unknown is True
    assert response.usage.estimated_cost_usd is None
    assert response.estimated_cost_usd is None
    assert response.usage.is_estimated_cost_complete is False


@pytest.mark.asyncio
async def test_budget_is_checked_before_every_attempt_including_retries() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.enqueue_errors(ORCH, [ClientError(429, {"error": {}})])
    tracker = make_tracker_mock()
    gateway = make_gateway(fake, tracker=tracker)
    workspace_id, task_id = uuid4(), uuid4()

    await gateway.generate_text("orchestrator", ["hi"], task_id=task_id, workspace_id=workspace_id)

    assert tracker.acquire_call.await_count == 2  # first attempt + one retry
    tracker.acquire_call.assert_awaited_with(
        workspace_id, task_id, for_tool_call=False, price_known=True
    )


@pytest.mark.asyncio
async def test_exhausted_budget_stops_the_call_before_the_provider_is_contacted() -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    tracker.acquire_call.side_effect = BudgetExceededError(
        "Task '11111111-5003-4000-8000-429429429429' reached its financial budget cap: $2.500000"
    )
    gateway = make_gateway(fake, tracker=tracker)

    with pytest.raises(BudgetExceededError):
        await gateway.generate_text("orchestrator", ["hi"], task_id=uuid4(), workspace_id=uuid4())

    assert fake.aio.models.calls == []
    tracker.record_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_calls_use_the_tool_turn_budget_check_and_standard_json_schema() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.default_tool_calls = [{"name": "lookup", "args": {"q": "x"}}]
    tracker = make_tracker_mock()
    gateway = make_gateway(fake, tracker=tracker)
    workspace_id, task_id = uuid4(), uuid4()
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
        "additionalProperties": False,
    }

    response = await gateway.generate_with_tools(
        "orchestrator",
        ["find x"],
        [ToolDefinition(name="lookup", description="Look something up", parameters=schema)],
        task_id=task_id,
        workspace_id=workspace_id,
    )

    assert response.call_requests[0].tool_name == "lookup"
    tracker.acquire_call.assert_awaited_with(
        workspace_id, task_id, for_tool_call=True, price_known=True
    )
    declared = fake.aio.models.calls[0]["config"].tools[0].function_declarations[0]
    assert declared.parameters_json_schema == schema


@pytest.mark.asyncio
async def test_embedding_usage_is_flagged_as_an_estimate_and_names_the_alias() -> None:
    fake = FakeGeminiClient()
    gateway = make_gateway(fake)

    response = await gateway.embed_content("embed", "Some text to embed")

    assert response.usage.is_usage_estimate is True
    assert response.usage.prompt_tokens > 0
    assert response.alias_used == "embed"
    assert response.was_fallback is False


@pytest.mark.asyncio
async def test_embedding_response_without_vectors_is_an_error() -> None:
    fake = FakeGeminiClient()

    async def empty_embeddings(**_kwargs: object) -> types.EmbedContentResponse:
        return types.EmbedContentResponse(embeddings=[])

    fake.aio.models.embed_content = empty_embeddings  # type: ignore[method-assign]
    gateway = make_gateway(fake)

    with pytest.raises(GatewayError, match="no embedding vectors"):
        await gateway.embed_content("embed", "text")


@pytest.mark.asyncio
async def test_count_tokens_retries_rate_limits_on_the_same_model_only() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.enqueue_errors(ORCH, [ClientError(429, {"error": {}})])
    sleep = AsyncMock()
    gateway = make_gateway(fake, sleep=sleep)

    response = await gateway.count_tokens("orchestrator", ["Short text"])

    assert response.total_tokens == 42
    assert response.alias_used == "orchestrator"
    sleep.assert_awaited_once()
    assert {c["model"] for c in fake.aio.models.calls} == {ORCH}


@pytest.mark.asyncio
async def test_structured_output_failures_do_not_leak_model_output_into_logs_or_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private = "PRIVATE-CUSTOMER-VALUE"
    fake = FakeGeminiClient()
    fake.aio.models.default_text = f'{{"summary": "ok", "score": "{private}"}}'
    gateway = make_gateway(fake)

    with caplog.at_level(logging.DEBUG, logger="app.gateway"):
        with pytest.raises(StructuredOutputError) as exc_info:
            await gateway.generate_structured("orchestrator", ["Rate it"], Scored)

    assert private not in caplog.text
    assert private not in str(exc_info.value)
    assert "score" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gateway_without_client_has_no_client_to_close() -> None:
    gateway = make_gateway(FakeGeminiClient(), gemini_api_key=None)
    gateway._client = None
    assert gateway.client is None
    await gateway.aclose()  # must be a safe no-op
