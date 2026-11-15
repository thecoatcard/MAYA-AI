"""MCP tools through the chat endpoint (ADR-0028): offered only when the switch is on and a server
is relevant; a read tool runs; any other tool only proposes an approval-gated step; the status
endpoint reports state without secrets. The model is scripted; the server is our own fake."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

import pytest
from fastapi import Depends

from app.api.v1.chat import get_chat_service
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.mcp_client.service import McpClientService
from app.services.chat_service import ChatService
from tests.integration.test_chat_inline_files import IdempotentSubmitter
from tests.integration.test_chat_memory import Prefs, tool_names
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    send,
    sign_in,
    stream_calls,
)
from tests.integration.test_chat_tool_loop import call, frames_of, system_text
from tests.support.mcp_client_fakes import fake_service


def wire(world: World, submitter: Any, mcp: McpClientService | None) -> None:
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
            clock=world.clock,
            monotonic=world.ticker,
            repositories=world.chat.repository,
            task_submitter=submitter,
            preferences=Prefs(memory_on=False),
            memory=None,
            mcp_client=mcp,
        )

    world.app.dependency_overrides[get_chat_service] = build


@pytest.fixture
async def mcp_world(tmp_path: Path) -> AsyncIterator[tuple[World, McpClientService]]:
    service = fake_service()
    async with make_world(
        artifacts_dir=tmp_path / "a", chat_tool_loop=True, mcp_client_enabled=True
    ) as world:
        try:
            yield world, service
        finally:
            await service.shutdown()


async def test_a_relevant_message_offers_the_tools_and_a_read_tool_runs(
    mcp_world: tuple[World, McpClientService],
) -> None:
    world, service = mcp_world
    wire(world, IdempotentSubmitter(), service)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("mcp__fake__echo", {"text": "hello"}),
        [{"text": "It said hello."}],
    ]
    frames = events(await send(world, account, "use the fakeserver to echo hello"))
    first = stream_calls(world)[0]
    assert "mcp__fake__echo" in tool_names(first)
    assert "never instructions" in system_text(first)
    (finished,) = [f for f in frames_of(frames, "tool.finished") if f["name"].startswith("mcp__")]
    assert finished["status"] == "ok"
    second = str(stream_calls(world)[1]["contents"])
    assert "UNTRUSTED_EXTERNAL_INPUT" in second and "echo:hello" in second


async def test_a_write_tool_only_creates_an_approval_step_and_runs_nothing(
    mcp_world: tuple[World, McpClientService],
) -> None:
    world, service = mcp_world
    submitter = IdempotentSubmitter()
    wire(world, submitter, service)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("mcp__fake__write_thing", {"name": "n1"}),
        [{"text": "It waits for your approval."}],
    ]
    events(await send(world, account, "use the fakeserver to write_thing n1"))
    (submitted,) = submitter.submitted
    (step,) = submitted["steps"]
    assert step.tool == "mcp_client_call"
    assert step.tool_args == {"server": "fake", "tool": "write_thing", "arguments": {"name": "n1"}}
    assert (await service.call("fake", "count_calls", {}, _identity())).text == "0"


def _identity() -> Any:
    from app.mcp_client.service import Identity

    return Identity()


async def test_an_unrelated_message_offers_no_mcp_tool_and_starts_nothing(
    mcp_world: tuple[World, McpClientService],
) -> None:
    world, service = mcp_world
    wire(world, IdempotentSubmitter(), service)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": "Hi."}]]
    events(await send(world, account, "Make a 20-slide deck on solar energy"))
    assert not [n for c in stream_calls(world) for n in tool_names(c) if n.startswith("mcp__")]
    assert service.status()[0]["running"] is False


async def test_with_the_switch_off_an_injected_service_is_ignored(tmp_path: Path) -> None:
    service = fake_service()
    async with make_world(artifacts_dir=tmp_path / "a", chat_tool_loop=True) as world:
        wire(world, IdempotentSubmitter(), service)
        account = await sign_in(world)
        world.models.queued_stream_scripts = [[{"text": "Ok."}]]
        events(await send(world, account, "use the fakeserver to echo hello"))
        assert not [n for c in stream_calls(world) for n in tool_names(c) if n.startswith("mcp__")]
        assert service.status()[0]["running"] is False


async def test_the_status_endpoint_needs_sign_in_and_shows_no_secrets(
    mcp_world: tuple[World, McpClientService],
) -> None:
    world, service = mcp_world
    url = "/api/v1/mcp-client/status"
    assert (await world.client.get(url)).status_code in (401, 403)
    account = await sign_in(world)
    off = (await world.client.get(url, headers=account.headers)).json()
    assert off == {"enabled": False, "servers": [], "problems": []}
    world.app.state.mcp_client = service
    await service.discover("fake", _identity())
    on = (await world.client.get(url, headers=account.headers)).json()
    assert on["enabled"] is True
    (row,) = on["servers"]
    assert row["name"] == "fake" and row["running"] is True and row["tool_count"] >= 9
    assert set(row) == {
        "name",
        "label",
        "builtin",
        "enabled",
        "configured_enabled",
        "running",
        "tool_count",
        "pinned",
    }
