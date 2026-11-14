"""Thought summaries on the gateway (MAYA_THINKING_SUMMARIES): fake SDK, real SDK types.

Covered: a summary part becomes ``thought`` on a stream chunk and never ``text``; the request asks
for summaries only when requested; with the flag off the request carries no thinking config and the
text is unchanged; ``generate_text`` returns the summary on its response.
"""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from google.genai import types
from pydantic import SecretStr

from app.core.config import Settings
from app.gateway.cost_tracker import BudgetTracker
from app.gateway.model_gateway import ModelGateway, _chunk_thought, _thinking_kwargs
from app.gateway.registry import build_default_registry
from app.gateway.resilience import ResiliencePolicy
from app.services.chat_service import ORCHESTRATOR_ALIAS
from tests.support.fake_gemini_client import FakeGeminiClient


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
def gateway(fake: FakeGeminiClient) -> ModelGateway:
    tracker = AsyncMock(spec=BudgetTracker)
    tracker.record_usage = AsyncMock()
    return ModelGateway(
        registry=build_default_registry(),
        settings=_settings(),
        client=fake,
        budget_tracker=tracker,
        resilience_policy=ResiliencePolicy(sleep=AsyncMock()),
    )


async def _stream(gateway: ModelGateway, **kwargs: Any) -> list[Any]:
    return [
        chunk
        async for chunk in gateway.generate_stream(
            ORCHESTRATOR_ALIAS, [{"role": "user", "parts": [{"text": "hi"}]}], **kwargs
        )
    ]


def _config(fake: FakeGeminiClient) -> types.GenerateContentConfig:
    (call,) = [c for c in fake.aio.models.calls if c["method"] == "generate_content_stream"]
    return cast(types.GenerateContentConfig, call["config"])


async def test_a_thought_summary_becomes_a_thought_chunk_and_never_text(
    gateway: ModelGateway, fake: FakeGeminiClient
) -> None:
    fake.aio.models.queued_stream_scripts = [
        [{"thought": "Checking the request."}, {"text": "Hello."}],
    ]
    chunks = await _stream(gateway, thought_summaries=True)

    assert "".join(c.thought for c in chunks) == "Checking the request."
    assert "".join(c.text for c in chunks) == "Hello."
    thinking = _config(fake).thinking_config
    assert thinking is not None
    assert thinking.include_thoughts is True


async def test_with_summaries_off_the_request_is_unchanged_and_the_text_is_the_same(
    gateway: ModelGateway, fake: FakeGeminiClient
) -> None:
    fake.aio.models.queued_stream_scripts = [[{"text": "Hello."}]]
    chunks = await _stream(gateway)

    assert all(c.thought == "" for c in chunks)
    assert "".join(c.text for c in chunks) == "Hello."
    assert _config(fake).thinking_config is None


async def test_with_summaries_off_a_thought_part_is_still_kept_out_of_the_text(
    gateway: ModelGateway, fake: FakeGeminiClient
) -> None:
    fake.aio.models.queued_stream_scripts = [[{"thought": "private"}, {"text": "Hello."}]]
    chunks = await _stream(gateway)
    assert "".join(c.text for c in chunks) == "Hello."
    assert all(c.thought == "" for c in chunks)


async def test_a_model_that_sends_no_summary_gives_no_thought_chunks(
    gateway: ModelGateway, fake: FakeGeminiClient
) -> None:
    fake.aio.models.queued_stream_scripts = [[{"text": "Hello."}]]
    chunks = await _stream(gateway, thought_summaries=True)
    assert all(c.thought == "" for c in chunks)
    assert "".join(c.text for c in chunks) == "Hello."


async def test_generate_text_returns_the_summary_only_when_asked(
    gateway: ModelGateway, fake: FakeGeminiClient
) -> None:
    seen: list[types.GenerateContentConfig] = []

    async def generate(**kwargs: Any) -> types.GenerateContentResponse:
        seen.append(kwargs["config"])
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(text="Looking at the options.", thought=True),
                            types.Part.from_text(text="Answer."),
                        ],
                    ),
                    finish_reason=types.FinishReason.STOP,
                )
            ],
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=3, candidates_token_count=4, total_token_count=7
            ),
        )

    fake.aio.models.generate_content = generate  # type: ignore[method-assign]

    on = await gateway.generate_text(
        ORCHESTRATOR_ALIAS, [{"role": "user", "parts": [{"text": "hi"}]}], thought_summaries=True
    )
    off = await gateway.generate_text(
        ORCHESTRATOR_ALIAS, [{"role": "user", "parts": [{"text": "hi"}]}]
    )

    assert on.text == "Answer." and on.thought_summary == "Looking at the options."
    assert off.text == "Answer." and off.thought_summary == ""
    assert seen[0].thinking_config is not None and seen[0].thinking_config.include_thoughts
    assert seen[1].thinking_config is None


def test_the_thinking_helpers_send_nothing_when_off() -> None:
    assert _thinking_kwargs(False) == {}
    assert _thinking_kwargs(True)["thinking_config"].include_thoughts is True


def test_chunk_thought_reads_only_thought_parts() -> None:
    chunk = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(text="Summary.", thought=True),
                        types.Part.from_text(text="Visible."),
                    ],
                )
            )
        ]
    )
    assert _chunk_thought(chunk) == "Summary."
    assert _chunk_thought(types.GenerateContentResponse(candidates=[])) == ""
