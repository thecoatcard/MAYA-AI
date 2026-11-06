"""Deterministic offline tests for distributed budget admission and accounting cleanup."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from google.genai import types
from google.genai.errors import ClientError, ServerError
from redis.exceptions import ConnectionError as RedisConnectionError

from app.gateway.cost_tracker import BudgetTracker
from app.gateway.exceptions import (
    BudgetExceededError,
    ContentBlockedError,
    EmbeddingDimensionMismatchError,
)
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import ModelRegistry, build_default_registry
from app.gateway.schemas import TokenUsage
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.gateway_factory import make_settings

ENFORCE = {
    "budget_enforcement_mode": "enforce",
    "gemini_budget_task_usd_cap": Decimal("2.00"),
    "gemini_max_tool_turns_cap": 3,
}


class _FakePipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self.redis = redis
        self.increments: list[tuple[str, int]] = []

    async def __aenter__(self) -> "_FakePipeline":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def hincrby(self, _key: str, field: str, amount: int) -> "_FakePipeline":
        self.increments.append((field, amount))
        return self

    def expire(self, *_args: Any) -> "_FakePipeline":
        return self

    async def execute(self) -> list[int | bool]:
        if self.redis.pipeline_started is not None:
            self.redis.pipeline_started.set()
        try:
            if self.redis.pipeline_continue is not None:
                await self.redis.pipeline_continue.wait()
            if self.redis.pipeline_error is not None:
                raise self.redis.pipeline_error
            async with self.redis.atomic:
                for field, amount in self.increments:
                    self.redis.hash[field] = self.redis.hash.get(field, 0) + amount
                return [self.redis.hash.get(field, 0) for field, _ in self.increments]
        finally:
            if self.redis.pipeline_finished is not None:
                self.redis.pipeline_finished.set()


class _FakeRedis:
    """Implements the tracker-used Redis operations with atomic in-memory semantics."""

    def __init__(self) -> None:
        self.hash: dict[str, Any] = {}
        self.leases: dict[str, str] = {}
        self.atomic = asyncio.Lock()
        self.eval_calls = 0
        self.fail_eval: BaseException | None = None
        self.pipeline_error: BaseException | None = None
        self.pipeline_started: asyncio.Event | None = None
        self.pipeline_continue: asyncio.Event | None = None
        self.pipeline_finished: asyncio.Event | None = None
        self.settlement_started: asyncio.Event | None = None
        self.settlement_continue: asyncio.Event | None = None
        self.settlement_finished: asyncio.Event | None = None

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        self.eval_calls += 1
        if self.fail_eval is not None:
            raise self.fail_eval
        async with self.atomic:
            if "PEXPIRE" in script:
                key, cost_cap, turn_cap, token, _ttl_ms, tool_call, reservation = args
                if key in self.leases:
                    return 0
                current_cost = self.hash.get("cost_micro_usd", 0)
                reserved = self.hash.get("reserved_micro_usd", 0)
                if int(cost_cap) >= 0 and (
                    current_cost + reserved >= int(cost_cap)
                    or current_cost + reserved + int(reservation) > int(cost_cap)
                ):
                    return -1
                if (
                    int(tool_call)
                    and int(turn_cap) >= 0
                    and self.hash.get("tool_turns", 0) >= int(turn_cap)
                ):
                    return -2
                assert "HSET" in script
                self.leases[str(key)] = str(token)
                if int(reservation) > 0:
                    self.hash["reserved_micro_usd"] = reserved + int(reservation)
                    self.hash["admission_reservation_micro_usd"] = int(reservation)
                self.hash.pop("settled_admission_token", None)
                return 1

            if "settled_admission_token" in script:
                if self.settlement_started is not None:
                    self.settlement_started.set()
                try:
                    if self.settlement_continue is not None:
                        await self.settlement_continue.wait()
                    if self.settlement_finished is not None:
                        self.settlement_finished.set()
                except BaseException:
                    if self.settlement_finished is not None:
                        self.settlement_finished.set()
                    raise
                if self.pipeline_error is not None:
                    raise self.pipeline_error
                (
                    key,
                    token,
                    cost_micro,
                    turns_to_add,
                    tokens_to_add,
                    unpriced_to_add,
                    _ttl_seconds,
                    estimated_usage,
                ) = args
                if self.leases.get(str(key)) != str(token):
                    return [-1]
                if self.hash.get("settled_admission_token") == str(token):
                    return [
                        self.hash.get("cost_micro_usd", 0),
                        self.hash.get("tool_turns", 0),
                        self.hash.get("total_tokens", 0),
                        self.hash.get("unpriced_calls", 0),
                    ]
                reservation = self.hash.pop("admission_reservation_micro_usd", 0)
                amount = int(cost_micro)
                if amount < 0 or int(estimated_usage):
                    amount = int(reservation)
                self.hash["cost_micro_usd"] = self.hash.get("cost_micro_usd", 0) + amount
                self.hash["tool_turns"] = self.hash.get("tool_turns", 0) + int(turns_to_add)
                self.hash["total_tokens"] = self.hash.get("total_tokens", 0) + int(tokens_to_add)
                self.hash["unpriced_calls"] = self.hash.get("unpriced_calls", 0) + int(
                    unpriced_to_add
                )
                self.hash["reserved_micro_usd"] = self.hash.get("reserved_micro_usd", 0) - int(
                    reservation
                )
                self.hash["settled_admission_token"] = str(token)
                if self.hash.get("release_requested_token") == str(token):
                    self.leases.pop(str(key), None)
                    self.hash.pop("release_requested_token", None)
                return [
                    self.hash["cost_micro_usd"],
                    self.hash["tool_turns"],
                    self.hash["total_tokens"],
                    self.hash["unpriced_calls"],
                ]

            key, token = args
            if "release_requested_token" in script:
                if self.leases.get(str(key)) == str(token):
                    if "admission_reservation_micro_usd" in self.hash:
                        self.hash["release_requested_token"] = str(token)
                        return 0
                    del self.leases[str(key)]
                    return 1
                return 0
            if self.leases.get(str(key)) == str(token):
                del self.leases[str(key)]
                return 1
            return 0

    async def hgetall(self, _key: str) -> dict[bytes, bytes]:
        return {key.encode(): str(value).encode() for key, value in self.hash.items()}

    def pipeline(self, *, transaction: bool) -> _FakePipeline:
        assert transaction is True
        return _FakePipeline(self)

    async def delete(self, key: str) -> int:
        self.hash.clear()
        self.leases.pop(key, None)
        return 1


def _tracker(redis: _FakeRedis, *, cap_usd: Decimal = Decimal("2.00")) -> BudgetTracker:
    settings = {**ENFORCE, "gemini_budget_task_usd_cap": cap_usd}
    return BudgetTracker(redis, make_settings(**settings))  # type: ignore[arg-type]


def _gateway(
    fake: FakeGeminiClient,
    tracker: BudgetTracker,
    *,
    cap_usd: Decimal = Decimal("2.00"),
) -> ModelGateway:
    settings = make_settings(**{**ENFORCE, "gemini_budget_task_usd_cap": cap_usd})
    return ModelGateway(build_default_registry(), settings, client=fake, budget_tracker=tracker)


@pytest.mark.asyncio
async def test_atomic_admission_rejects_concurrent_same_task_call() -> None:
    redis = _FakeRedis()
    tracker = _tracker(redis)
    workspace_id, task_id = uuid4(), uuid4()

    results = await asyncio.gather(
        tracker.acquire_call(workspace_id, task_id, price_known=True),
        tracker.acquire_call(workspace_id, task_id, price_known=True),
        return_exceptions=True,
    )

    assert sum(isinstance(result, str) for result in results) == 1
    assert sum(isinstance(result, BudgetExceededError) for result in results) == 1
    token = next(result for result in results if isinstance(result, str))
    await tracker.release_call(workspace_id, task_id, token)
    next_token = await tracker.acquire_call(workspace_id, task_id, price_known=True)
    assert next_token is not None
    await tracker.release_call(workspace_id, task_id, next_token)


@pytest.mark.asyncio
async def test_reservation_survives_lease_release_until_usage_is_accounted() -> None:
    redis = _FakeRedis()
    tracker = _tracker(redis)
    workspace_id, task_id = uuid4(), uuid4()
    token = await tracker.acquire_call(
        workspace_id, task_id, price_known=True, reservation_micro_usd=1_500_000
    )
    assert token is not None

    await tracker.release_call(workspace_id, task_id, token)
    assert redis.hash["reserved_micro_usd"] == 1_500_000
    assert redis.leases  # release is deferred until the reservation is reconciled
    with pytest.raises(BudgetExceededError, match="already has a provider call in progress"):
        await tracker.acquire_call(workspace_id, task_id, reservation_micro_usd=1)

    await tracker.record_usage(
        workspace_id,
        task_id,
        TokenUsage(prompt_tokens=10, estimated_cost_usd=Decimal("0.10")),
        reservation_token=token,
    )
    usage = await tracker.get_usage(workspace_id, task_id)
    assert usage["reserved_micro_usd"] == 0
    assert usage["cost_micro_usd"] == 100_000
    assert not redis.leases


@pytest.mark.asyncio
async def test_atomic_admission_applies_money_and_tool_turn_caps() -> None:
    redis = _FakeRedis()
    redis.hash["cost_micro_usd"] = 2_000_000
    redis.hash["tool_turns"] = 3
    tracker = _tracker(redis)
    workspace_id, task_id = uuid4(), uuid4()

    with pytest.raises(BudgetExceededError, match="financial budget cap"):
        await tracker.acquire_call(workspace_id, task_id, price_known=True)
    redis.hash["cost_micro_usd"] = 0

    # A final plain response does not consume or check the tool-turn allowance.
    token = await tracker.acquire_call(workspace_id, task_id, price_known=True)
    await tracker.release_call(workspace_id, task_id, token)
    with pytest.raises(BudgetExceededError, match="tool turns cap"):
        await tracker.acquire_call(workspace_id, task_id, for_tool_call=True, price_known=True)


@pytest.mark.asyncio
async def test_near_cap_call_is_rejected_before_provider_execution() -> None:
    redis = _FakeRedis()
    redis.hash["cost_micro_usd"] = 1_000_000
    tracker = _tracker(redis)
    fake = FakeGeminiClient()
    gateway = _gateway(fake, tracker)

    with pytest.raises(BudgetExceededError, match="financial budget cap"):
        await gateway.generate_text(
            "orchestrator", ["input"], task_id=uuid4(), workspace_id=uuid4()
        )

    assert fake.aio.models.calls == []
    assert redis.hash.get("reserved_micro_usd", 0) == 0


@pytest.mark.asyncio
async def test_actual_usage_reconciles_the_reservation_after_accounting() -> None:
    redis = _FakeRedis()
    tracker = _tracker(redis)
    fake = FakeGeminiClient()
    gateway = _gateway(fake, tracker)
    workspace_id, task_id = uuid4(), uuid4()

    response = await gateway.generate_text(
        "orchestrator",
        ["input"],
        max_output_tokens=17,
        task_id=task_id,
        workspace_id=workspace_id,
    )

    assert fake.aio.models.calls[0]["config"].max_output_tokens == 17
    usage = await tracker.get_usage(workspace_id, task_id)
    assert usage["reserved_micro_usd"] == 0
    assert usage["cost_micro_usd"] == 263
    assert response.usage.estimated_cost_usd == Decimal("0.0002625")
    assert not redis.leases


@pytest.mark.asyncio
async def test_retry_and_fallback_attempts_release_lease_before_next_attempt() -> None:
    redis = _FakeRedis()
    tracker = _tracker(redis, cap_usd=Decimal("5.00"))
    fake = FakeGeminiClient()
    fake.aio.models.enqueue_errors(
        "gemini-3.8-flash",
        [ClientError(429, {"error": {}}), ServerError(503, {"error": {}})],
    )
    gateway = _gateway(fake, tracker, cap_usd=Decimal("5.00"))
    workspace_id, task_id = uuid4(), uuid4()

    response = await gateway.generate_text(
        "orchestrator", ["Try, retry, then fall back"], workspace_id=workspace_id, task_id=task_id
    )

    assert response.was_fallback is True
    assert [call["model"] for call in fake.aio.models.calls] == [
        "gemini-3.8-flash",
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
    ]
    assert not redis.leases
    usage = await tracker.get_usage(workspace_id, task_id)
    assert usage["total_tokens"] > 150  # failed attempts are conservatively accounted
    assert usage["cost_micro_usd"] > 2_000_000
    assert usage["reserved_micro_usd"] == 0


@pytest.mark.asyncio
async def test_tool_stream_and_embedding_attempts_settle_reservations() -> None:
    workspace_id, task_id = uuid4(), uuid4()

    tool_redis = _FakeRedis()
    tool_tracker = _tracker(tool_redis)
    tool_client = FakeGeminiClient()
    tool_client.aio.models.default_tool_calls = [{"name": "lookup", "args": {"q": "x"}}]
    tool_gateway = _gateway(tool_client, tool_tracker)
    tool_response = await tool_gateway.generate_with_tools(
        "orchestrator",
        ["input"],
        [],
        workspace_id=workspace_id,
        task_id=task_id,
        max_output_tokens=23,
    )
    assert tool_response.call_requests
    assert tool_client.aio.models.calls[0]["config"].max_output_tokens == 23
    tool_usage = await tool_tracker.get_usage(workspace_id, task_id)
    assert tool_usage["reserved_micro_usd"] == 0
    assert tool_usage["tool_turns"] == 1

    stream_redis = _FakeRedis()
    stream_tracker = _tracker(stream_redis)
    stream_client = FakeGeminiClient()
    stream_gateway = _gateway(stream_client, stream_tracker)
    stream = stream_gateway.generate_stream(
        "orchestrator", ["input"], workspace_id=workspace_id, task_id=task_id
    )
    await stream.__anext__()
    await stream.aclose()
    stream_usage = await stream_tracker.get_usage(workspace_id, task_id)
    assert stream_client.aio.models.calls[0]["config"].max_output_tokens == 65_536
    assert stream_usage["reserved_micro_usd"] == 0
    assert stream_usage["cost_micro_usd"] == 1_032_192
    assert not stream_redis.leases

    embed_redis = _FakeRedis()
    embed_tracker = _tracker(embed_redis)
    embed_client = FakeGeminiClient()
    embed_gateway = _gateway(embed_client, embed_tracker)
    await embed_gateway.embed_content(
        "embed", ["one", "two"], workspace_id=workspace_id, task_id=task_id
    )
    embed_usage = await embed_tracker.get_usage(workspace_id, task_id)
    assert embed_usage["reserved_micro_usd"] == 0
    assert embed_usage["cost_micro_usd"] == 3_277
    assert not embed_redis.leases


@pytest.mark.asyncio
async def test_provider_error_and_nonstream_cancellation_account_before_release() -> None:
    error_redis = _FakeRedis()
    error_tracker = _tracker(error_redis, cap_usd=Decimal("5.00"))
    error_client = FakeGeminiClient()
    error_client.aio.models.enqueue_errors("gemini-3.8-flash", [ServerError(503, {"error": {}})])
    error_gateway = _gateway(error_client, error_tracker, cap_usd=Decimal("5.00"))

    await error_gateway.generate_text(
        "orchestrator", ["input"], task_id=uuid4(), workspace_id=uuid4()
    )
    assert error_redis.hash["cost_micro_usd"] > 1_000_000
    assert error_redis.hash["reserved_micro_usd"] == 0
    assert not error_redis.leases

    cancel_redis = _FakeRedis()
    cancel_tracker = _tracker(cancel_redis)
    cancel_client = FakeGeminiClient()

    async def cancel_before_response(**_kwargs: Any) -> types.GenerateContentResponse:
        raise asyncio.CancelledError

    cancel_client.aio.models.generate_content = cancel_before_response  # type: ignore[method-assign]
    cancel_gateway = _gateway(cancel_client, cancel_tracker)
    workspace_id, task_id = uuid4(), uuid4()
    with pytest.raises(asyncio.CancelledError):
        await cancel_gateway.generate_text(
            "orchestrator", ["input"], task_id=task_id, workspace_id=workspace_id
        )

    cancelled_usage = await cancel_tracker.get_usage(workspace_id, task_id)
    assert cancelled_usage["cost_micro_usd"] == 1_032_192
    assert cancelled_usage["reserved_micro_usd"] == 0
    assert not cancel_redis.leases


@pytest.mark.asyncio
async def test_stream_cancellation_keeps_reservation_until_partial_usage_finishes() -> None:
    redis = _FakeRedis()
    redis.settlement_started = asyncio.Event()
    redis.settlement_continue = asyncio.Event()
    redis.settlement_finished = asyncio.Event()
    tracker = _tracker(redis)
    fake = FakeGeminiClient()
    provider_waiting = asyncio.Event()
    never = asyncio.Event()
    workspace_id, task_id = uuid4(), uuid4()

    async def paused_stream(_model: str) -> Any:
        yield types.GenerateContentResponse(
            candidates=[
                types.Candidate(content=types.Content(parts=[types.Part.from_text(text="partial")]))
            ]
        )
        provider_waiting.set()
        await never.wait()

    fake.aio.models._stream_chunks = paused_stream  # type: ignore[assignment]
    gateway = _gateway(fake, tracker)
    stream = gateway.generate_stream(
        "orchestrator", ["input"], task_id=task_id, workspace_id=workspace_id
    )

    async def consume() -> None:
        async for _chunk in stream:
            pass

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(provider_waiting.wait(), timeout=1)
        consumer.cancel()
        await asyncio.wait_for(redis.settlement_started.wait(), timeout=1)
        assert redis.hash["reserved_micro_usd"] == 1_032_192
        assert redis.leases

        redis.settlement_continue.set()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        await asyncio.wait_for(redis.settlement_finished.wait(), timeout=1)
    finally:
        redis.settlement_continue.set()
        if not consumer.done():
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer

    usage = await tracker.get_usage(workspace_id, task_id)
    assert usage["cost_micro_usd"] == 1_032_192
    assert redis.hash["reserved_micro_usd"] == 0
    assert not redis.leases


@pytest.mark.asyncio
async def test_enforced_billable_call_requires_task_workspace_and_tracker() -> None:
    fake = FakeGeminiClient()
    tracker = _tracker(_FakeRedis())
    gateway = _gateway(fake, tracker)
    with pytest.raises(BudgetExceededError, match="require both task_id and workspace_id"):
        await gateway.generate_text("orchestrator", ["input"], workspace_id=uuid4())
    with pytest.raises(ValueError, match="workspace_id is required"):
        await gateway.generate_text("orchestrator", ["input"], task_id=uuid4())
    assert fake.aio.models.calls == []

    no_tracker_gateway = ModelGateway(
        build_default_registry(),
        make_settings(**ENFORCE),
        client=fake,
    )
    with pytest.raises(BudgetExceededError, match="available budget tracker"):
        await no_tracker_gateway.embed_content(
            "embed", "input", task_id=uuid4(), workspace_id=uuid4()
        )
    assert fake.aio.models.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["text", "stream", "tools", "embed"])
async def test_unknown_price_is_blocked_before_provider_attempt(operation: str) -> None:
    redis = _FakeRedis()
    tracker = _tracker(redis)
    fake = FakeGeminiClient()
    alias = "embed" if operation == "embed" else "orchestrator"
    registry = build_default_registry({alias: "gemini-custom-enterprise"})
    gateway = ModelGateway(registry, make_settings(**ENFORCE), client=fake, budget_tracker=tracker)

    with pytest.raises(BudgetExceededError, match="unknown pricing"):
        if operation == "text":
            await gateway.generate_text(alias, ["input"], task_id=uuid4(), workspace_id=uuid4())
        elif operation == "stream":
            stream = gateway.generate_stream(
                alias, ["input"], task_id=uuid4(), workspace_id=uuid4()
            )
            await stream.__anext__()
        elif operation == "tools":
            await gateway.generate_with_tools(
                alias, ["input"], [], task_id=uuid4(), workspace_id=uuid4()
            )
        else:
            await gateway.embed_content(alias, "input", task_id=uuid4(), workspace_id=uuid4())

    assert fake.aio.models.calls == []
    assert redis.eval_calls == 0


@pytest.mark.asyncio
async def test_unpriced_embedding_fallback_is_blocked_after_retryable_failure() -> None:
    redis = _FakeRedis()
    tracker = _tracker(redis)
    fake = FakeGeminiClient()
    fake.aio.models.enqueue_errors("gemini-embedding-2", [ServerError(503, {"error": {}})])
    gateway = _gateway(fake, tracker)

    with pytest.raises(BudgetExceededError, match="unknown pricing"):
        await gateway.embed_content(
            "embed", "input", task_id=uuid4(), workspace_id=uuid4(), allow_fallback=True
        )

    assert [call["model"] for call in fake.aio.models.calls] == ["gemini-embedding-2"]
    assert not redis.leases


@pytest.mark.asyncio
async def test_missing_price_rate_is_blocked_and_redis_errors_fail_closed() -> None:
    base = build_default_registry().get("orchestrator")
    registry = build_default_registry()
    registry = ModelRegistry(
        {**registry.specs, "orchestrator": replace(base, cost_per_1m_output=None)}
    )
    fake = FakeGeminiClient()
    redis = _FakeRedis()
    gateway = ModelGateway(
        registry, make_settings(**ENFORCE), client=fake, budget_tracker=_tracker(redis)
    )

    with pytest.raises(BudgetExceededError, match="unknown pricing"):
        await gateway.generate_text(
            "orchestrator", ["input"], task_id=uuid4(), workspace_id=uuid4()
        )
    assert fake.aio.models.calls == []

    redis.fail_eval = RedisConnectionError("private Redis details")
    known_gateway = _gateway(fake, _tracker(redis))
    with pytest.raises(RedisConnectionError):
        await known_gateway.generate_text(
            "orchestrator", ["input"], task_id=uuid4(), workspace_id=uuid4()
        )
    assert fake.aio.models.calls == []


@pytest.mark.asyncio
async def test_redis_accounting_error_does_not_retry_or_release_reservation() -> None:
    redis = _FakeRedis()
    redis.pipeline_error = RedisConnectionError("private Redis details")
    tracker = _tracker(redis)
    fake = FakeGeminiClient()
    gateway = _gateway(fake, tracker)

    with pytest.raises(RedisConnectionError):
        await gateway.generate_text(
            "orchestrator", ["input"], task_id=uuid4(), workspace_id=uuid4()
        )

    assert len(fake.aio.models.calls) == 1
    assert redis.leases
    assert redis.hash["reserved_micro_usd"] > 0


@pytest.mark.asyncio
async def test_cancelled_shielded_accounting_failure_is_observed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = _FakeRedis()
    redis.pipeline_error = RedisConnectionError("private Redis details")
    redis.pipeline_started = asyncio.Event()
    redis.pipeline_continue = asyncio.Event()
    redis.pipeline_finished = asyncio.Event()
    tracker = _tracker(redis)
    loop = asyncio.get_running_loop()
    unhandled: list[dict[str, Any]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        record_task = asyncio.create_task(
            tracker.record_usage(
                uuid4(),
                uuid4(),
                TokenUsage(prompt_tokens=1, estimated_cost_usd=Decimal("0.01")),
            )
        )
        await redis.pipeline_started.wait()
        record_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await record_task
        redis.pipeline_continue.set()
        await redis.pipeline_finished.wait()
        await asyncio.sleep(0)
    finally:
        redis.pipeline_continue.set()
        loop.set_exception_handler(previous_handler)

    assert not unhandled
    assert "budget_usage_write_failed_after_cancellation" in caplog.text
    assert "private Redis details" not in caplog.text


@pytest.mark.asyncio
async def test_blocked_first_stream_chunk_is_metered_and_releases_admission() -> None:
    redis = _FakeRedis()
    tracker = _tracker(redis)
    fake = FakeGeminiClient()
    workspace_id, task_id = uuid4(), uuid4()

    async def blocked(_model: str) -> Any:
        yield types.GenerateContentResponse(
            prompt_feedback=types.GenerateContentResponsePromptFeedback(
                block_reason=types.BlockedReason.SAFETY
            ),
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=17),
        )

    fake.aio.models._stream_chunks = blocked  # type: ignore[assignment]
    with pytest.raises(ContentBlockedError):
        async for _chunk in _gateway(fake, tracker).generate_stream(
            "orchestrator", ["blocked"], task_id=task_id, workspace_id=workspace_id
        ):
            pass

    usage = await tracker.get_usage(workspace_id, task_id)
    assert usage["total_tokens"] == 17
    assert not redis.leases


@pytest.mark.asyncio
async def test_malformed_embedding_response_is_metered_before_validation() -> None:
    redis = _FakeRedis()
    tracker = _tracker(redis)
    fake = FakeGeminiClient()
    fake.aio.models.embedding_dimension = 512
    workspace_id, task_id = uuid4(), uuid4()

    with pytest.raises(EmbeddingDimensionMismatchError):
        await _gateway(fake, tracker).embed_content(
            "embed", "malformed vector", task_id=task_id, workspace_id=workspace_id
        )

    usage = await tracker.get_usage(workspace_id, task_id)
    assert usage["total_tokens"] > 0
    assert usage["cost_micro_usd"] > 0
    assert not redis.leases


@pytest.mark.asyncio
async def test_cleanup_accounting_failure_does_not_mask_stream_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from tests.support.gateway_factory import make_tracker_mock

    fake = FakeGeminiClient()
    tracker = make_tracker_mock()
    tracker.acquire_call.return_value = "lease"
    tracker.record_usage.side_effect = RedisConnectionError("private accounting details")
    tracker.release_call.side_effect = RedisConnectionError("private cleanup details")
    gateway = ModelGateway(
        build_default_registry(),
        make_settings(**ENFORCE),
        client=fake,
        budget_tracker=tracker,
    )
    stream = gateway.generate_stream(
        "orchestrator", ["one two"], task_id=uuid4(), workspace_id=uuid4()
    )
    await stream.__anext__()

    with pytest.raises(asyncio.CancelledError):
        await stream.athrow(asyncio.CancelledError())

    tracker.record_usage.assert_awaited_once()
    tracker.release_call.assert_awaited_once()
    assert "private" not in caplog.text
