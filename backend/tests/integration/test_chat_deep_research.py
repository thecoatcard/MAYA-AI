"""Chat routing of ``mode: "deep_research"``: a research task is created by code, the start message
is written by code (no model call, no promised results), the kill switch fails closed, and a
message without the mode is untouched. Fakes only; no network."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, cast

import pytest
from fastapi import Depends

from app.api.v1.chat import get_chat_service
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.core.clock import Clock
from app.search.chain import SearchChain
from app.search.service import WebSearchService
from app.services.chat_service import ChatService
from tests.integration.test_chat_inline_files import IdempotentSubmitter
from tests.integration.test_chat_native_search import FakeDdgs, Pages, make_service
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    reply_text,
    send,
    sign_in,
    stream_calls,
)


def wire(world: World, service: WebSearchService | None, submitter: IdempotentSubmitter) -> None:
    async def build(principal: Annotated[Principal, Depends(get_principal)]) -> ChatService:
        return ChatService(
            principal=principal,
            database=world.chat.database(),
            gateway=world.gateway,
            registry=world.registry,
            settings=world.settings,
            limiter=world.limiter,
            idempotency=world.store,
            stream_limiter=world.stream_limiter,
            clock=cast(Clock, world.clock),
            monotonic=world.ticker,
            repositories=world.chat.repository,
            task_submitter=submitter,
            web_search=service,
        )

    world.app.dependency_overrides[get_chat_service] = build


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts") as value:
        yield value


def search_service() -> WebSearchService:
    return make_service(FakeDdgs([]))


async def test_the_deep_research_mode_creates_a_research_task_and_writes_the_start_text(
    world: World,
) -> None:
    submitter = IdempotentSubmitter()
    wire(world, search_service(), submitter)
    account = await sign_in(world)

    frames = events(
        await send(world, account, "research solar battery storage costs", mode="deep_research")
    )

    assert len(submitter.submitted) == 1
    sent = submitter.submitted[0]
    assert sent["deep_research"] == {} and sent["source"] == "chat"
    assert sent["instruction"] == "research solar battery storage costs"
    assert "task.created" in names(frames) and names(frames)[-1] == "done"
    text = reply_text(frames)
    assert text.startswith("Started: deep research on research solar battery storage costs.")
    assert "at least 20 different sites" in text and "will not share any findings" in text
    assert stream_calls(world) == []  # the model was never called: the text comes from code


async def test_a_requested_number_of_sites_is_passed_on(world: World) -> None:
    submitter = IdempotentSubmitter()
    wire(world, search_service(), submitter)
    account = await sign_in(world)

    frames = events(
        await send(world, account, "research EV charging, read 50 sites", mode="deep_research")
    )

    assert submitter.submitted[0]["deep_research"] == {"sources": 50}
    assert "up to 50" in reply_text(frames)


async def test_without_the_mode_nothing_changes(world: World) -> None:
    submitter = IdempotentSubmitter()
    wire(world, search_service(), submitter)
    account = await sign_in(world)

    frames = events(await send(world, account, "hello"))

    assert not any("deep_research" in call for call in submitter.submitted)
    assert not reply_text(frames).startswith("Started: deep research")


async def test_the_kill_switch_starts_nothing_and_says_so(tmp_path: Path) -> None:
    async with make_world(
        artifacts_dir=tmp_path / "artifacts", deep_research_enabled=False
    ) as world:
        submitter = IdempotentSubmitter()
        wire(world, search_service(), submitter)
        account = await sign_in(world)

        frames = events(
            await send(world, account, "research solar batteries", mode="deep_research")
        )

        assert submitter.submitted == []
        assert "Deep research did not start: deep research is switched off" in reply_text(frames)
        assert names(frames)[-1] == "done"


async def test_without_a_search_provider_it_fails_closed(world: World) -> None:
    submitter = IdempotentSubmitter()
    chain = SearchChain({}, [])
    wire(world, WebSearchService(chain, Pages({})), submitter)
    account = await sign_in(world)

    frames = events(await send(world, account, "research solar batteries", mode="deep_research"))

    assert submitter.submitted == []
    assert "web search is not available" in reply_text(frames)


async def test_a_message_without_a_topic_asks_instead_of_guessing(world: World) -> None:
    submitter = IdempotentSubmitter()
    wire(world, search_service(), submitter)
    account = await sign_in(world)

    frames = events(await send(world, account, "deep research", mode="deep_research"))

    assert submitter.submitted == []
    assert names(frames)[-1] == "done"
    assert stream_calls(world) == []


async def test_the_deep_research_phrase_starts_the_same_engine_without_the_mode(
    world: World,
) -> None:
    submitter = IdempotentSubmitter()
    wire(world, search_service(), submitter)
    account = await sign_in(world)

    frames = events(
        await send(world, account, "Deep research about the future of AI agents by 2030")
    )

    assert len(submitter.submitted) == 1
    assert submitter.submitted[0]["deep_research"] == {}
    text = reply_text(frames)
    assert text.startswith("Started: deep research on ")
    assert stream_calls(world) == []  # code wrote the text; no model call, and no file was asked
