"""Offline regressions for gateway accounting and existing budget behavior."""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from decimal import Decimal
from typing import Any, Literal, TypedDict
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from google.genai import types
from google.genai.errors import ServerError
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.gateway.cost_tracker import BudgetTracker
from app.gateway.exceptions import (
    BudgetExceededError,
    ContentBlockedError,
    EmbeddingDimensionMismatchError,
    GatewayError,
    StreamInterruptedError,
)
from app.gateway.registry import ModelRegistry, build_default_registry
from app.gateway.schemas import GatewayStreamChunk
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.gateway_factory import make_gateway, make_settings, make_tracker_mock


class _EnforceSettings(TypedDict):
    budget_enforcement_mode: Literal["enforce"]
    gemini_budget_task_usd_cap: Decimal
    gemini_max_tool_turns_cap: int


ENFORCE: _EnforceSettings = {
    "budget_enforcement_mode": "enforce",
    "gemini_budget_task_usd_cap": Decimal("2.00"),
    "gemini_max_tool_turns_cap": 3,
}


async def collect(stream: AsyncIterator[GatewayStreamChunk]) -> list[GatewayStreamChunk]:
    return [chunk async for chunk in stream]


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_usage", [True, False], ids=["reported", "estimated"])
async def test_blocked_first_chunk_is_metered_once(reported_usage: bool) -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    gateway = make_gateway(fake, tracker=tracker)
    closed = False

    async def blocked(model: str) -> AsyncIterator[types.GenerateContentResponse]:
        nonlocal closed
        try:
            usage = (
                types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=17, candidates_token_count=3
                )
                if reported_usage
                else None
            )
            yield types.GenerateContentResponse(
                prompt_feedback=types.GenerateContentResponsePromptFeedback(
                    block_reason=types.BlockedReason.SAFETY
                ),
                usage_metadata=usage,
            )
        finally:
            closed = True

    fake.aio.models._stream_chunks = blocked  # type: ignore[method-assign]
    workspace_id, task_id = uuid4(), uuid4()

    with pytest.raises(ContentBlockedError):
        await collect(
            gateway.generate_stream(
                "orchestrator", ["Blocked input"], workspace_id=workspace_id, task_id=task_id
            )
        )

    tracker.record_usage.assert_awaited_once()
    recorded = tracker.record_usage.call_args.kwargs
    usage = recorded["usage"]
    assert recorded["workspace_id"] == workspace_id
    assert recorded["task_id"] == task_id
    assert usage.is_usage_estimate is not reported_usage
    if reported_usage:
        assert usage.prompt_tokens == 17
        assert usage.candidates_tokens == 3
    else:
        assert usage.prompt_tokens > 0
    assert closed
    assert len(fake.aio.models.calls) == 1


@pytest.mark.asyncio
async def test_blocked_first_chunk_keeps_block_when_accounting_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    tracker.record_usage.side_effect = RedisConnectionError("private accounting details")
    gateway = make_gateway(fake, tracker=tracker)
    closed = False

    async def blocked(model: str) -> AsyncIterator[types.GenerateContentResponse]:
        nonlocal closed
        try:
            yield types.GenerateContentResponse(
                prompt_feedback=types.GenerateContentResponsePromptFeedback(
                    block_reason=types.BlockedReason.SAFETY
                ),
                usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=17),
            )
        finally:
            closed = True

    fake.aio.models._stream_chunks = blocked  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="app.gateway.model_gateway"):
        with pytest.raises(ContentBlockedError):
            await collect(
                gateway.generate_stream(
                    "orchestrator", ["Blocked input"], workspace_id=uuid4(), task_id=uuid4()
                )
            )

    tracker.record_usage.assert_awaited_once()
    assert "gateway_partial_stream_usage_record_failed" in caplog.text
    assert "private accounting details" not in caplog.text
    assert closed
    assert len(fake.aio.models.calls) == 1


@pytest.mark.asyncio
async def test_later_blocked_chunk_includes_its_usage_in_the_partial_record() -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    gateway = make_gateway(fake, tracker=tracker)
    closed = False

    async def blocked_after_text(model: str) -> AsyncIterator[types.GenerateContentResponse]:
        nonlocal closed
        try:
            yield types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(parts=[types.Part.from_text(text="partial")])
                    )
                ]
            )
            yield types.GenerateContentResponse(
                prompt_feedback=types.GenerateContentResponsePromptFeedback(
                    block_reason=types.BlockedReason.SAFETY
                ),
                usage_metadata=types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=40, candidates_token_count=9
                ),
            )
        finally:
            closed = True

    fake.aio.models._stream_chunks = blocked_after_text  # type: ignore[method-assign]
    with pytest.raises(StreamInterruptedError) as caught:
        await collect(
            gateway.generate_stream(
                "orchestrator", ["Input"], workspace_id=uuid4(), task_id=uuid4()
            )
        )

    tracker.record_usage.assert_awaited_once()
    usage = tracker.record_usage.call_args.kwargs["usage"]
    assert usage.prompt_tokens == 40
    assert usage.candidates_tokens == 9
    assert usage.is_usage_estimate is True
    assert caught.value.partial_usage == usage
    assert closed
    assert len(fake.aio.models.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["dimensions", "empty"])
async def test_invalid_embedding_is_metered_once(invalid: str) -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    gateway = make_gateway(fake, tracker=tracker)
    if invalid == "dimensions":
        fake.aio.models.embedding_dimension = 512
        error: type[GatewayError] = EmbeddingDimensionMismatchError
        message = "expected exactly 768 dimensions"
    else:
        original = fake.aio.models.embed_content

        async def empty_embedding(**kwargs: Any) -> types.EmbedContentResponse:
            await original(**kwargs)
            return types.EmbedContentResponse(embeddings=[])

        fake.aio.models.embed_content = empty_embedding  # type: ignore[method-assign]
        error = GatewayError
        message = "no embedding vectors"

    workspace_id, task_id = uuid4(), uuid4()
    with pytest.raises(error, match=message):
        await gateway.embed_content(
            "embed", "Embedding input", workspace_id=workspace_id, task_id=task_id
        )

    tracker.record_usage.assert_awaited_once()
    recorded = tracker.record_usage.call_args.kwargs
    usage = recorded["usage"]
    assert recorded["workspace_id"] == workspace_id
    assert recorded["task_id"] == task_id
    assert recorded["is_tool_turn"] is False
    assert usage.is_usage_estimate is True
    assert usage.prompt_tokens > 0
    assert usage.estimated_cost_usd is not None
    assert usage.estimated_cost_usd > 0
    assert len(fake.aio.models.calls) == 1


@pytest.mark.asyncio
async def test_valid_embedding_is_metered_once() -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    gateway = make_gateway(fake, tracker=tracker)
    response = await gateway.embed_content(
        "embed", "Embedding input", workspace_id=uuid4(), task_id=uuid4()
    )
    tracker.record_usage.assert_awaited_once()
    assert tracker.record_usage.call_args.kwargs["usage"] == response.usage
    assert len(fake.aio.models.calls) == 1


@pytest.mark.asyncio
async def test_embedding_accounting_failure_does_not_retry_a_received_response() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.embedding_dimension = 512
    tracker = make_tracker_mock()
    tracker.record_usage.side_effect = RedisConnectionError("private accounting details")
    gateway = make_gateway(fake, tracker=tracker)

    with pytest.raises(RedisConnectionError):
        await gateway.embed_content(
            "embed", "Embedding input", workspace_id=uuid4(), task_id=uuid4(), allow_fallback=True
        )

    tracker.record_usage.assert_awaited_once()
    assert [call["model"] for call in fake.aio.models.calls] == ["gemini-embedding-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", ["cancel", "close"])
async def test_stream_abort_preserves_signal_when_accounting_fails(
    signal: str, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeGeminiClient()
    fake.aio.models.default_text = "one two"
    tracker = make_tracker_mock()
    tracker.record_usage.side_effect = RedisConnectionError("private accounting details")
    gateway = make_gateway(fake, tracker=tracker)
    stream = gateway.generate_stream(
        "orchestrator", ["Input"], workspace_id=uuid4(), task_id=uuid4()
    )
    await stream.__anext__()

    with caplog.at_level(logging.WARNING, logger="app.gateway.model_gateway"):
        if signal == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await stream.athrow(asyncio.CancelledError())
        else:
            await stream.aclose()

    tracker.record_usage.assert_awaited_once()
    assert "gateway_partial_stream_usage_record_failed" in caplog.text
    assert "private accounting details" not in caplog.text
    assert fake.aio.models.open_streams == 0
    assert len(fake.aio.models.calls) == 1


@pytest.mark.asyncio
async def test_second_cancellation_observes_deferred_accounting_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    gateway = make_gateway(fake, tracker=tracker)
    provider_waiting = asyncio.Event()
    provider_closed = asyncio.Event()
    record_started = asyncio.Event()
    release_record = asyncio.Event()
    failure_observed = asyncio.Event()
    never = asyncio.Event()

    async def delayed_record(**_kwargs: Any) -> None:
        record_started.set()
        await release_record.wait()
        raise RedisConnectionError("private accounting details")

    async def paused_stream(model: str) -> AsyncIterator[types.GenerateContentResponse]:
        try:
            yield types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(parts=[types.Part.from_text(text="partial")])
                    )
                ]
            )
            provider_waiting.set()
            await never.wait()
        finally:
            provider_closed.set()

    original_log = gateway._log_partial_usage_failure

    def observe_failure(exc: BaseException) -> None:
        original_log(exc)
        failure_observed.set()

    tracker.record_usage.side_effect = delayed_record
    fake.aio.models._stream_chunks = paused_stream  # type: ignore[method-assign]
    monkeypatch.setattr(gateway, "_log_partial_usage_failure", observe_failure)
    consumer = asyncio.create_task(
        collect(
            gateway.generate_stream(
                "orchestrator", ["Input"], workspace_id=uuid4(), task_id=uuid4()
            )
        )
    )
    try:
        with caplog.at_level(logging.WARNING, logger="app.gateway.model_gateway"):
            await asyncio.wait_for(provider_waiting.wait(), timeout=1)
            consumer.cancel()
            await asyncio.wait_for(record_started.wait(), timeout=1)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            assert provider_closed.is_set()
            assert len(gateway._pending_partial_usage) == 1
            release_record.set()
            await asyncio.wait_for(failure_observed.wait(), timeout=1)

        assert not gateway._pending_partial_usage
        assert "gateway_partial_stream_usage_record_failed" in caplog.text
        assert "private accounting details" not in caplog.text
        tracker.record_usage.assert_awaited_once()
    finally:
        release_record.set()
        if not consumer.done():
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer


@pytest.mark.asyncio
async def test_stream_interruption_is_not_masked_by_accounting_failure() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.default_text = "one two"
    fake.aio.models.stream_error_after_chunk = 1
    tracker = make_tracker_mock()
    tracker.record_usage.side_effect = RedisConnectionError("private accounting details")
    gateway = make_gateway(fake, tracker=tracker)

    with pytest.raises(StreamInterruptedError) as caught:
        await collect(
            gateway.generate_stream(
                "orchestrator", ["Input"], workspace_id=uuid4(), task_id=uuid4()
            )
        )

    assert caught.value.partial_text == "one "
    tracker.record_usage.assert_awaited_once()
    assert fake.aio.models.open_streams == 0
    assert len(fake.aio.models.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["text", "stream", "tools", "embed"])
async def test_gateway_passes_unknown_price_to_budget_admission(operation: str) -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    alias = "embed" if operation == "embed" else "orchestrator"
    registry = build_default_registry({alias: "gemini-custom-model"})
    gateway = make_gateway(fake, tracker=tracker, registry=registry, **ENFORCE)
    workspace_id, task_id = uuid4(), uuid4()

    if operation == "text":
        await gateway.generate_text(alias, ["Input"], workspace_id=workspace_id, task_id=task_id)
    elif operation == "stream":
        await collect(
            gateway.generate_stream(alias, ["Input"], workspace_id=workspace_id, task_id=task_id)
        )
    elif operation == "tools":
        await gateway.generate_with_tools(
            alias, ["Input"], [], workspace_id=workspace_id, task_id=task_id
        )
    else:
        await gateway.embed_content(alias, "Input", workspace_id=workspace_id, task_id=task_id)

    assert len(fake.aio.models.calls) == 1
    tracker.acquire_call.assert_awaited_once_with(
        workspace_id,
        task_id,
        for_tool_call=operation == "tools",
        price_known=False,
        reservation_micro_usd=0,
    )
    tracker.record_usage.assert_awaited_once()
    assert tracker.record_usage.call_args.kwargs["usage"].estimated_cost_usd is None


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["input", "output", "cached"])
async def test_gateway_admission_handles_missing_price_rates_conservatively(
    missing: str,
) -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    registry = build_default_registry()
    base = registry.get("orchestrator")
    if missing == "input":
        incomplete = replace(base, cost_per_1m_input=None)
    elif missing == "output":
        incomplete = replace(base, cost_per_1m_output=None)
    else:
        incomplete = replace(base, cost_per_1m_cached=None)
        fake.aio.models.cached_content_token_count = 10
    registry = ModelRegistry({**registry.specs, "orchestrator": incomplete})
    gateway = make_gateway(fake, tracker=tracker, registry=registry, **ENFORCE)

    await gateway.generate_text("orchestrator", ["Input"], workspace_id=uuid4(), task_id=uuid4())

    assert len(fake.aio.models.calls) == 1
    tracker.acquire_call.assert_awaited_once()
    assert tracker.acquire_call.call_args.kwargs["price_known"] is (
        missing != "input" and missing != "output"
    )
    tracker.record_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_allows_embedding_with_its_input_only_price() -> None:
    fake = FakeGeminiClient()
    gateway = make_gateway(fake, tracker=make_tracker_mock(), **ENFORCE)
    response = await gateway.embed_content(
        "embed", "Embedding input", workspace_id=uuid4(), task_id=uuid4()
    )
    assert response.usage.estimated_cost_usd is not None
    assert len(fake.aio.models.calls) == 1


@pytest.mark.asyncio
async def test_gateway_checks_price_for_each_embedding_fallback_attempt() -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    fake.aio.models.enqueue_errors("gemini-embedding-2", [ServerError(503, {})])
    gateway = make_gateway(fake, tracker=tracker, **ENFORCE)

    result = await gateway.embed_content(
        "embed", "Input", workspace_id=uuid4(), task_id=uuid4(), allow_fallback=True
    )

    assert result.usage.estimated_cost_usd is None
    assert [call["model"] for call in fake.aio.models.calls] == [
        "gemini-embedding-2",
        "gemini-embedding-001",
    ]
    assert tracker.acquire_call.await_count == 2
    assert tracker.acquire_call.await_args.kwargs["price_known"] is False
    tracker.record_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_existing_policy_does_not_block_prior_unpriced_usage() -> None:
    redis = MagicMock(spec=Redis)
    redis.hgetall = AsyncMock(return_value={b"cost_micro_usd": b"1", b"unpriced_calls": b"1"})
    tracker = BudgetTracker(redis, make_settings(**ENFORCE))

    await tracker.ensure_within_budget(uuid4(), uuid4())

    redis.hgetall.assert_awaited_once()


@pytest.mark.asyncio
async def test_log_only_still_allows_and_records_unpriced_model() -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    registry = build_default_registry({"orchestrator": "gemini-custom-model"})
    gateway = make_gateway(fake, tracker=tracker, registry=registry)
    result = await gateway.generate_text(
        "orchestrator", ["Input"], workspace_id=uuid4(), task_id=uuid4()
    )
    assert result.usage.estimated_cost_usd is None
    tracker.record_usage.assert_awaited_once()
    assert len(fake.aio.models.calls) == 1


@pytest.mark.asyncio
async def test_enforce_without_task_fails_closed_before_provider_execution() -> None:
    fake = FakeGeminiClient()
    registry = build_default_registry({"orchestrator": "gemini-custom-model"})
    gateway = make_gateway(fake, tracker=make_tracker_mock(), registry=registry, **ENFORCE)
    with pytest.raises(BudgetExceededError, match="require both task_id and workspace_id"):
        await gateway.generate_text("orchestrator", ["Input"])
    assert fake.aio.models.calls == []
