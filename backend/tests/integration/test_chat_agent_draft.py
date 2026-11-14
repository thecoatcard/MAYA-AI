"""Chat drafts a custom agent on request (ADR-0023): preview in the thread, nothing written.

Same wiring as ``test_chat_memory``; the model call that writes the draft is replaced by a fake
(no real model is called) and a real temporary folder stands in for the connected folder.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from app.agents.custom import CustomAgent, CustomAgentError, render_definition
from app.files import services as file_services
from app.services import chat_service
from tests.integration.test_chat_memory import FakeMemory, tool_names, wire
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    new_key,
    send,
    sign_in,
    stream_calls,
)
from tests.support.files import make_services

CALL: dict[str, Any] = {
    "name": "create_custom_agent",
    "args": {"request": "mere liye ek agent jo weekly report likhe"},
}
AGENT = CustomAgent(
    name="weekly-reporter",
    description="Writes a weekly status report.",
    instructions="Write a short weekly status report from the notes I give you.",
    capabilities=("read_files",),
)


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async with make_world() as value:
        yield value


@pytest.fixture
def folder(tmp_path: Path) -> Iterator[Path]:
    """A connected folder for every user (the test principal's ids are random)."""
    services, settings, work = make_services(tmp_path)

    async def any_user(workspace_id: Any, user_id: Any) -> str:
        return str(work)

    settings.get_workspace_folder = any_user  # type: ignore[method-assign]
    file_services.configure(services)
    yield work
    file_services.reset()


@pytest.fixture
def fake_draft(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    async def draft(gateway: Any, request: str, workspace_id: Any) -> Any:
        seen.append(request)
        return AGENT, ".maya/agents/weekly-reporter.md", render_definition(AGENT)

    monkeypatch.setattr(chat_service, "draft_custom_agent", draft)
    return seen


async def test_a_draft_is_streamed_with_the_exact_file_and_nothing_is_written(
    world: World, folder: Path, fake_draft: list[str]
) -> None:
    wire(world, FakeMemory())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        [{"text": "Drafting. "}, {"call": CALL}],
        [{"text": "Please review the draft and approve it."}],
    ]
    frames = events(await send(world, account, "mere liye ek agent bana do"))
    order = names(frames)
    assert order.count("agent.draft") == 1 and order[-1] == "done"
    draft = next(f for f in frames if f.event == "agent.draft").data
    assert draft["name"] == "weekly-reporter"
    assert draft["path"] == ".maya/agents/weekly-reporter.md"
    assert draft["content"] == render_definition(AGENT)
    assert fake_draft == [CALL["args"]["request"]]
    assert "create_custom_agent" in tool_names(stream_calls(world)[0])
    # the model is told nothing was created
    response = stream_calls(world)[1]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert response["status"] == "drafted" and "Nothing is saved" in response["message"]
    assert not (folder / ".maya").exists()  # the write waits for the user's approval
    (assistant,) = world.chat.rows("orchestrator")
    assert assistant.metadata["agent_drafts"] == [draft]


async def test_a_replay_sends_the_stored_draft_without_a_new_model_call(
    world: World, folder: Path, fake_draft: list[str]
) -> None:
    wire(world, FakeMemory())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"call": CALL}], [{"text": "Review it."}]]
    key = new_key()
    first = events(await send(world, account, "create an agent", key=key))
    again = events(await send(world, account, "create an agent", key=key))
    assert [f.data for f in again if f.event == "agent.draft"] == [
        f.data for f in first if f.event == "agent.draft"
    ]
    assert len(fake_draft) == 1 and len(stream_calls(world)) == 2


async def test_without_a_connected_folder_nothing_is_drafted_and_the_model_must_ask(
    world: World, tmp_path: Path, fake_draft: list[str]
) -> None:
    services, _settings, _work = make_services(tmp_path)  # no folder connected
    file_services.configure(services)
    try:
        wire(world, FakeMemory())
        account = await sign_in(world)
        world.models.queued_stream_scripts = [[{"call": CALL}], [{"text": "Connect a folder."}]]
        frames = events(await send(world, account, "create an agent for reports"))
    finally:
        file_services.reset()
    assert "agent.draft" not in names(frames) and fake_draft == []
    response = stream_calls(world)[1]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert response["status"] == "needs_folder" and "working folder" in response["message"]


async def test_an_invalid_draft_is_reported_not_shown(
    world: World, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def bad(*args: Any) -> Any:
        raise CustomAgentError("The name must be 3-40 characters", "bad_name")

    monkeypatch.setattr(chat_service, "draft_custom_agent", bad)
    wire(world, FakeMemory())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"call": CALL}], [{"text": "Could not."}]]
    frames = events(await send(world, account, "create an agent"))
    assert "agent.draft" not in names(frames)
    response = stream_calls(world)[1]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert response["status"] == "error"


async def test_the_tool_is_not_offered_when_file_features_are_not_installed(
    world: World,
) -> None:
    file_services.reset()
    wire(world, FakeMemory())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": "Hello."}]]
    await send(world, account, "hi there friend")
    assert "create_custom_agent" not in tool_names(stream_calls(world)[0])
