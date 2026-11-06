"""Streaming: retry/fallback only before the first chunk, estimated usage afterwards."""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from google.genai import types
from google.genai.errors import ClientError, ServerError

from app.gateway.exceptions import (
    BudgetExceededError,
    ContentBlockedError,
    GatewayFallbackExhaustedError,
    StreamInterruptedError,
)
from app.gateway.schemas import GatewayStreamChunk
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.gateway_factory import make_gateway, make_tracker_mock

TEXT = "one two three four five six"


async def collect(stream: AsyncIterator[GatewayStreamChunk]) -> list[GatewayStreamChunk]:
    return [chunk async for chunk in stream]


@pytest.mark.asyncio
async def test_status_error_while_opening_the_stream_backs_off_then_succeeds() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.default_text = TEXT
    fake.aio.models.enqueue_errors("gemini-3.8-flash", [ClientError(429, {}), ClientError(429, {})])
    sleep = AsyncMock()
    gateway = make_gateway(fake, sleep=sleep)

    chunks = await collect(gateway.generate_stream("orchestrator", ["Count"]))

    assert "".join(c.text for c in chunks) == TEXT
    assert {c.alias_used for c in chunks} == {"orchestrator"}
    assert [call.args[0] for call in sleep.await_args_list] == [2.0, 4.0]  # full jitter at max
    assert fake.aio.models.open_streams == 0


@pytest.mark.asyncio
async def test_error_before_the_first_chunk_falls_back_to_the_next_model() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.default_text = TEXT
    fake.aio.models.stream_first_chunk_errors["gemini-3.8-flash"] = [
        ConnectionResetError("dropped before any data")
    ]
    gateway = make_gateway(fake)

    chunks = await collect(gateway.generate_stream("orchestrator", ["Count"]))

    assert {c.alias_used for c in chunks} == {"lite"}
    assert all(c.was_fallback for c in chunks)
    assert fake.aio.models.open_streams == 0  # the abandoned first stream was closed too


@pytest.mark.asyncio
async def test_every_model_failing_before_the_first_chunk_exhausts_the_chain() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.enqueue_errors("gemini-3.8-flash", [ServerError(503, {})])
    fake.aio.models.enqueue_errors("gemini-3.5-flash-lite", [ServerError(500, {})])
    gateway = make_gateway(fake)

    with pytest.raises(GatewayFallbackExhaustedError, match=r"ServerError\(500\)"):
        await collect(gateway.generate_stream("orchestrator", ["Count"]))


@pytest.mark.asyncio
async def test_mid_stream_failure_after_chunks_is_never_retried_and_hides_error_text() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.default_text = TEXT
    fake.aio.models.stream_error_after_chunk = 3
    tracker = make_tracker_mock()
    gateway = make_gateway(fake, tracker=tracker)

    with pytest.raises(StreamInterruptedError) as exc_info:
        await collect(
            gateway.generate_stream(
                "orchestrator", ["Count"], task_id=uuid4(), workspace_id=uuid4()
            )
        )

    error = exc_info.value
    assert error.chunks_yielded == 3
    assert error.partial_text == "one two three "
    assert error.partial_usage.is_usage_estimate is True
    assert error.partial_usage.prompt_tokens > 0  # estimated from the request, not a constant
    assert "Simulated network stream dropout" not in str(error)  # error text is never embedded
    assert len(fake.aio.models.calls) == 1  # no retry and no fallback after output began
    tracker.record_usage.assert_awaited_once()
    assert fake.aio.models.open_streams == 0


@pytest.mark.asyncio
async def test_stopping_early_records_an_estimate_and_closes_the_provider_stream() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.default_text = TEXT
    tracker = make_tracker_mock()
    gateway = make_gateway(fake, tracker=tracker)

    stream = gateway.generate_stream(
        "orchestrator", ["Count"], task_id=uuid4(), workspace_id=uuid4()
    )
    first = await stream.__anext__()
    assert first.text == "one "
    await stream.aclose()  # the consumer stops reading

    tracker.record_usage.assert_awaited_once()
    assert tracker.record_usage.call_args.kwargs["usage"].is_usage_estimate is True
    assert fake.aio.models.open_streams == 0


@pytest.mark.asyncio
async def test_cancellation_mid_stream_records_an_estimate_and_reraises() -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    gateway = make_gateway(fake, tracker=tracker)

    async def cancelled_after_one_chunk(model: str) -> AsyncIterator[types.GenerateContentResponse]:
        yield types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(parts=[types.Part.from_text(text="partial ")]),
                )
            ]
        )
        raise asyncio.CancelledError

    fake.aio.models._stream_chunks = cancelled_after_one_chunk  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await collect(
            gateway.generate_stream(
                "orchestrator", ["Count"], task_id=uuid4(), workspace_id=uuid4()
            )
        )

    tracker.record_usage.assert_awaited_once()
    assert tracker.record_usage.call_args.kwargs["usage"].is_usage_estimate is True


@pytest.mark.asyncio
async def test_stream_without_usage_metadata_is_recorded_as_an_estimate() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.default_text = TEXT
    fake.aio.models.stream_includes_usage = False
    tracker = make_tracker_mock()
    gateway = make_gateway(fake, tracker=tracker)

    await collect(
        gateway.generate_stream("orchestrator", ["Count"], task_id=uuid4(), workspace_id=uuid4())
    )

    usage = tracker.record_usage.call_args.kwargs["usage"]
    assert usage.is_usage_estimate is True
    assert usage.prompt_tokens > 0
    assert usage.candidates_tokens > 0


@pytest.mark.asyncio
async def test_final_chunk_reports_finish_reason() -> None:
    fake = FakeGeminiClient()
    fake.aio.models.default_text = "alpha beta"
    gateway = make_gateway(fake)

    chunks = await collect(gateway.generate_stream("orchestrator", ["Go"]))

    assert [c.is_final for c in chunks] == [False, True]
    assert chunks[-1].finish_reason == "STOP"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.is_usage_estimate is False


@pytest.mark.asyncio
async def test_a_blocked_first_chunk_is_raised_without_fallback() -> None:
    fake = FakeGeminiClient()
    gateway = make_gateway(fake)

    async def blocked(model: str) -> AsyncIterator[types.GenerateContentResponse]:
        yield types.GenerateContentResponse(
            prompt_feedback=types.GenerateContentResponsePromptFeedback(
                block_reason=types.BlockedReason.SAFETY
            )
        )

    fake.aio.models._stream_chunks = blocked  # type: ignore[method-assign]

    with pytest.raises(ContentBlockedError):
        await collect(gateway.generate_stream("orchestrator", ["Blocked"]))

    assert len(fake.aio.models.calls) == 1


@pytest.mark.asyncio
async def test_exhausted_budget_stops_a_stream_before_the_provider_is_contacted() -> None:
    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    tracker.acquire_call.side_effect = BudgetExceededError("cap reached")
    gateway = make_gateway(fake, tracker=tracker)

    with pytest.raises(BudgetExceededError):
        await collect(
            gateway.generate_stream(
                "orchestrator", ["Count"], task_id=uuid4(), workspace_id=uuid4()
            )
        )

    assert fake.aio.models.calls == []
