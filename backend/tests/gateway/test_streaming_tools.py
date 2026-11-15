"""Function calling on ``ModelGateway.generate_stream`` (fake SDK stream, real SDK types)."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from google.genai import types
from pydantic import SecretStr

from app.core.config import Settings
from app.gateway.cost_tracker import BudgetTracker
from app.gateway.model_gateway import (
    ModelGateway,
    tool_call_content,
    tool_response_content,
)
from app.gateway.registry import build_default_registry
from app.gateway.resilience import ResiliencePolicy
from app.gateway.schemas import ToolDefinition
from tests.support.fake_gemini_client import FakeGeminiClient

TOOL = ToolDefinition(
    name="submit_task",
    description="Start a background task.",
    parameters={
        "type": "object",
        "properties": {"instruction": {"type": "string"}},
        "required": ["instruction"],
    },
)


def _settings() -> Settings:
    return Settings(
        database_url=SecretStr("postgresql+asyncpg://maya_app:secret@127.0.0.1:15432/maya"),
        redis_url=SecretStr("redis://127.0.0.1:16379/0"),
        jwt_signing_key=SecretStr("1234567890123456789012345678901234567890"),
        otp_hmac_key=SecretStr("abcdefghijklmnopqrstuvwxyz0123456789abcdef"),
        gemini_api_key=SecretStr("example-fake-gemini-key-for-test"),
    )


@pytest.fixture
def fake() -> FakeGeminiClient:
    return FakeGeminiClient()


@pytest.fixture
def tracker() -> AsyncMock:
    mock = AsyncMock(spec=BudgetTracker)
    mock.record_usage = AsyncMock()
    return mock


@pytest.fixture
def gateway(fake: FakeGeminiClient, tracker: AsyncMock) -> ModelGateway:
    return ModelGateway(
        registry=build_default_registry(),
        settings=_settings(),
        client=fake,
        budget_tracker=tracker,
        resilience_policy=ResiliencePolicy(sleep=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_function_call_is_surfaced_and_declared(
    gateway: ModelGateway, fake: FakeGeminiClient, tracker: AsyncMock
) -> None:
    fake.aio.models.queued_stream_scripts = [
        [
            {"text": "Starting. "},
            {"call": {"name": "submit_task", "args": {"instruction": "Research X"}}},
        ]
    ]
    chunks = [
        c
        async for c in gateway.generate_stream(
            "orchestrator",
            ["do it"],
            workspace_id=uuid4(),
            tools=[TOOL],
        )
    ]
    assert "".join(c.text for c in chunks) == "Starting. "
    calls = [call for c in chunks for call in c.tool_calls]
    assert len(calls) == 1
    assert calls[0].tool_name == "submit_task"
    assert calls[0].arguments == {"instruction": "Research X"}
    assert calls[0].call_id

    config = fake.aio.models.calls[0]["config"]
    declared = config.tools[0].function_declarations
    assert [d.name for d in declared] == ["submit_task"]
    # a function-calling attempt is a tool turn
    assert tracker.record_usage.call_args.kwargs["is_tool_turn"] is True


@pytest.mark.asyncio
async def test_no_tools_means_no_declarations_and_no_tool_turn(
    gateway: ModelGateway, fake: FakeGeminiClient, tracker: AsyncMock
) -> None:
    fake.aio.models.default_text = "Plain answer"
    chunks = [c async for c in gateway.generate_stream("orchestrator", ["hi"])]
    assert "".join(c.text for c in chunks) == "Plain answer"
    assert all(c.tool_calls == [] for c in chunks)
    assert fake.aio.models.calls[0]["config"].tools is None
    assert tracker.record_usage.call_args.kwargs["is_tool_turn"] is False


@pytest.mark.asyncio
async def test_tools_given_but_model_answers_in_text(
    gateway: ModelGateway, fake: FakeGeminiClient, tracker: AsyncMock
) -> None:
    fake.aio.models.queued_stream_scripts = [[{"text": "It is four."}]]
    chunks = [c async for c in gateway.generate_stream("orchestrator", ["2+2?"], tools=[TOOL])]
    assert all(c.tool_calls == [] for c in chunks)
    assert tracker.record_usage.call_args.kwargs["is_tool_turn"] is False


@pytest.mark.asyncio
async def test_continuation_turn_carries_function_response(
    gateway: ModelGateway, fake: FakeGeminiClient
) -> None:
    fake.aio.models.queued_stream_scripts = [
        [{"call": {"name": "submit_task", "args": {"instruction": "Do X"}}}],
        [{"text": "I started it."}],
    ]
    first = [c async for c in gateway.generate_stream("orchestrator", ["do X"], tools=[TOOL])]
    call = first[-1].tool_calls[0]

    contents = [
        {"role": "user", "parts": [{"text": "do X"}]},
        tool_call_content(call),
        tool_response_content(call, {"status": "started", "task_id": "t-1"}),
    ]
    second = [c async for c in gateway.generate_stream("orchestrator", contents)]
    assert "".join(c.text for c in second) == "I started it."

    sent = fake.aio.models.calls[1]["contents"]
    assert sent[1]["parts"][0]["function_call"]["name"] == "submit_task"
    assert sent[2]["parts"][0]["function_response"]["response"]["task_id"] == "t-1"
    # the SDK accepts exactly this shape
    types.Content.model_validate(sent[1])
    types.Content.model_validate(sent[2])


@pytest.mark.asyncio
async def test_thought_signature_is_echoed(gateway: ModelGateway) -> None:
    from app.gateway.schemas import ToolCallRequest

    call = ToolCallRequest(call_id="c1", tool_name="submit_task", thought_signature=b"sig")
    content = tool_call_content(call)
    assert content["parts"][0]["thought_signature"] == b"sig"
    types.Content.model_validate(content)


@pytest.mark.asyncio
async def test_forced_names_set_function_calling_mode_any_for_declared_tools(
    gateway: ModelGateway, fake: FakeGeminiClient
) -> None:
    fake.aio.models.queued_stream_scripts = [
        [{"call": {"name": "submit_task", "args": {"instruction": "Research X"}}}]
    ]
    [
        c
        async for c in gateway.generate_stream(
            "orchestrator",
            ["do it"],
            workspace_id=uuid4(),
            tools=[TOOL],
            forced_tool_names=frozenset({"submit_task", "not_declared"}),
        )
    ]
    calling = fake.aio.models.calls[0]["config"].tool_config.function_calling_config
    assert calling.mode == types.FunctionCallingConfigMode.ANY
    assert calling.allowed_function_names == ["submit_task"]


@pytest.mark.asyncio
async def test_no_forced_names_means_no_tool_config(
    gateway: ModelGateway, fake: FakeGeminiClient
) -> None:
    fake.aio.models.queued_stream_scripts = [[{"text": "Plain answer."}]]
    [
        c
        async for c in gateway.generate_stream(
            "orchestrator", ["hi"], workspace_id=uuid4(), tools=[TOOL]
        )
    ]
    assert fake.aio.models.calls[0]["config"].tool_config is None
