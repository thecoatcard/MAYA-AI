"""Chat tool loop, Phase 2 (ADR-0026), through the chat endpoint with MAYA_CHAT_TOOL_LOOP on.

Covered: web_search (numbered sources, the three-search cap, quota as a plain 'unavailable');
GitHub and Notion reads on a loop turn when connected; generate_image on a loop turn; memory tools
on a loop turn; send_email (prepares an approval-bound email task, never sends, one per reply, the
recipient must be in the user's message, off without Gmail); and the flag-off path, unchanged.

Fakes only: the chat model is scripted, the research agent runs over a fake model gateway,
Notion and GitHub run over fake HTTP transports, the image model and the memory store are fakes,
and the task submitter is a fake. Nothing sends mail and nothing calls a provider.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, cast
from uuid import uuid4

import pytest
from fastapi import Depends

from app.agents.specialized.research import ResearchAgent
from app.api.v1.chat import get_chat_service
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.core.clock import Clock
from app.gateway.exceptions import GatewayFallbackExhaustedError
from app.memory.chat_memory import RecalledMemory
from app.services.chat_service import ChatService
from app.services.loop_tools import MAX_WEB_SEARCHES_PER_REPLY, PHASE2_ADDENDUM
from tests.integration.test_chat_image import (
    RecordingImageClient,
    image_client,  # noqa: F401 (fixture)
)
from tests.integration.test_chat_inline_files import IdempotentSubmitter, call, card_frames
from tests.integration.test_chat_memory import FakeMemory, Prefs, tool_names
from tests.integration.test_chat_notion_tools import NOTION_TOOLS, RecordingNotionRepo
from tests.integration.test_chat_repo_tools import URL, RecordingRepoTools
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    send,
    sign_in,
    stream_calls,
)

SOURCE_URL = "https://example.org/solar"
SEARCH_TEXT = "Solar output rose in 2026 [1]."
LOOP_NAMES = {"create_document", "preview_document", "start_job", "check_job"}


class FakeSearchGateway:
    """The model gateway's grounded call: a canned answer, or one error on every attempt."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def generate_text(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        citations = [{"title": "Solar report", "url": SOURCE_URL}]
        return SimpleNamespace(text=SEARCH_TEXT, citations=citations)


def wire(
    world: World,
    *,
    memory: Any = None,
    prefs: Any = None,
    submitter: Any = None,
    repo_tools: Any = None,
    search: FakeSearchGateway | None = None,
) -> None:
    """The chat endpoint's service with the Phase 2 collaborators (fakes or the real agent)."""
    web = ResearchAgent(model_gateway=search) if search is not None else None
    # The loop needs the task submitter too (inline routing names submit_task), as in production.
    task_submitter = submitter if submitter is not None else IdempotentSubmitter()

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
            task_submitter=task_submitter,
            preferences=prefs,
            memory=memory,
            repo_tools=repo_tools,
            web_search=cast(Any, web),
        )

    world.app.dependency_overrides[get_chat_service] = build


def finished(frames: list[Any]) -> list[str]:
    return [f.data["status"] for f in frames if f.event == "tool.finished"]


def system_text(record: dict[str, Any]) -> str:
    return str(record["config"].system_instruction)


@pytest.fixture
async def loop_world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=True) as value:
        yield value


@pytest.fixture
async def mail_world(tmp_path: Path) -> AsyncIterator[World]:
    """A loop world with a connected mailbox, so send_email is offered for an email request."""
    async with make_world(
        artifacts_dir=tmp_path / "artifacts",
        chat_tool_loop=True,
        mail_address="owner@example.com",
        mail_app_password="fake-app-password-1234",
    ) as value:
        yield value


# ------------------------------------------------------------------------------- web_search


async def test_web_search_gives_numbered_sources_and_stops_at_the_cap(loop_world: World) -> None:
    search = FakeSearchGateway()
    wire(loop_world, search=search)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("web_search", {"query": "solar power news"}),
        call("web_search", {"query": "solar power news again"}),
        call("web_search", {"query": "solar power 2026"}),
        call("web_search", {"query": "one more search"}),
        [{"text": "Solar output rose in 2026 [1]."}],
    ]
    frames = events(await send(loop_world, account, "What is the latest news on solar power?"))

    first = stream_calls(loop_world)[0]
    assert "web_search" in tool_names(first) and PHASE2_ADDENDUM in system_text(first)
    assert finished(frames) == ["ok"] * MAX_WEB_SEARCHES_PER_REPLY + ["skipped"]
    assert len(search.calls) == MAX_WEB_SEARCHES_PER_REPLY
    # The model saw the numbered source it may cite, and nothing else as a source.
    answered = str(stream_calls(loop_world)[-1]["contents"])
    assert SOURCE_URL in answered and "Solar report" in answered
    assert names(frames)[-1] == "done"


async def test_a_search_quota_error_is_unavailable_and_no_source_reaches_the_model(
    loop_world: World,
) -> None:
    search = FakeSearchGateway()
    search.error = GatewayFallbackExhaustedError("quota")
    wire(loop_world, search=search)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("web_search", {"query": "solar power news"}),
        [{"text": "I could not check current sources right now."}],
    ]
    frames = events(await send(loop_world, account, "What is the latest news on solar power?"))

    assert finished(frames) == ["error"]
    told = str(stream_calls(loop_world)[1]["contents"])
    assert "Web search FAILED" in told and "Do NOT give headlines" in told
    assert SOURCE_URL not in told
    assert names(frames)[-1] == "done"


async def test_with_the_flag_off_latest_news_gets_no_tools_and_no_search(tmp_path: Path) -> None:
    async with make_world(artifacts_dir=tmp_path / "artifacts") as legacy:
        search = FakeSearchGateway()
        wire(legacy, search=search)
        account = await sign_in(legacy)
        legacy.models.queued_stream_scripts = [[{"text": "Here is what I know."}]]
        await send(legacy, account, "What is the latest news on solar power?")

        (only,) = stream_calls(legacy)
        assert tool_names(only) == [] and search.calls == []


# ------------------------------------------------------------------- GitHub and Notion reads


async def test_github_reads_are_offered_and_run_on_a_loop_turn_when_connected(
    loop_world: World,
) -> None:
    seen: list[Any] = []
    repo = RecordingRepoTools(seen, connected=True)
    wire(loop_world, repo_tools=repo)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        [{"call": {"name": "github_get_repo", "args": {"repo": URL}}}],
        [{"text": "The demo repository uses the main branch."}],
    ]
    frames = events(await send(loop_world, account, "List my GitHub repositories"))

    first = stream_calls(loop_world)[0]
    assert {"github_get_repo", "github_read_file"} <= set(tool_names(first))
    assert LOOP_NAMES <= set(tool_names(first)) and "submit_task" not in tool_names(first)
    assert finished(frames) == ["ok"]
    assert {r.url.host for r in seen} == {"api.github.com"}
    assert names(frames)[-1] == "done"


async def test_notion_reads_are_offered_and_run_on_a_loop_turn_when_connected(
    loop_world: World,
) -> None:
    wire(loop_world, repo_tools=RecordingNotionRepo(connected=True))
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        [{"call": {"name": "notion_list_tasks", "args": {"due": "today"}}}],
        [{"text": "You have one task today: Ship deck."}],
    ]
    frames = events(await send(loop_world, account, "What tasks are on my Notion board today?"))

    first, follow = stream_calls(loop_world)
    assert NOTION_TOOLS <= set(tool_names(first)) and LOOP_NAMES <= set(tool_names(first))
    assert finished(frames) == ["ok"]
    assert "Ship deck" in str(follow["contents"])
    assert names(frames)[-1] == "done"


# -------------------------------------------------------------------------- image and memory


async def test_an_image_on_a_loop_turn_is_a_card_and_is_offered_once(
    loop_world: World,
    image_client: RecordingImageClient,  # noqa: F811 (the fixture is imported above)
) -> None:
    wire(loop_world)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("generate_image", {"prompt": "a red fox in the snow", "style_hint": "flat"}),
        [{"text": "Your fox image is in the chat."}],
    ]
    frames = events(await send(loop_world, account, "Draw a picture of a red fox in the snow"))

    first, follow = stream_calls(loop_world)
    assert {"generate_image", "create_document"} <= set(tool_names(first))
    assert "generate_image" not in tool_names(follow)
    assert image_client.prompts and "red fox" in image_client.prompts[0]
    (card,) = card_frames(frames)
    assert card["file_type"] == "image"
    assert names(frames)[-1] == "done"


async def test_memory_tools_are_offered_on_a_loop_turn_and_recall_reads_the_store(
    loop_world: World,
) -> None:
    memory = FakeMemory()
    memory.hits = [RecalledMemory(uuid4(), "Prefers window seats", 0.9)]
    wire(loop_world, memory=memory, prefs=Prefs(memory_on=True))
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("recall_memory", {"query": "seat preference"}),
        [{"text": "Your notes file is in the chat."}],
    ]
    frames = events(await send(loop_world, account, "Make a notes.md file about my trip to Goa"))

    first, follow = stream_calls(loop_world)
    assert {"save_memory", "forget_memory", "recall_memory"} <= set(tool_names(first))
    assert finished(frames) == ["ok"]
    assert "seat preference" in memory.recall_calls
    assert "Prefers window seats" in str(follow["contents"])
    assert names(frames)[-1] == "done"


# ------------------------------------------------------------------------------- send_email


async def test_send_email_only_prepares_an_approval_bound_task_and_never_sends(
    mail_world: World,
) -> None:
    submitter = IdempotentSubmitter()
    wire(mail_world, submitter=submitter)
    account = await sign_in(mail_world)
    mail_world.models.queued_stream_scripts = [
        call(
            "send_email",
            {
                "to": "rahul@example.com",
                "subject": "Meeting moved",
                "body": "The meeting moved to 5 pm.",
            },
        ),
        [{"text": "The email is waiting for your approval."}],
    ]
    frames = events(
        await send(mail_world, account, "Send an email to rahul@example.com about the meeting")
    )

    first = stream_calls(mail_world)[0]
    assert "send_email" in tool_names(first) and "submit_task" not in tool_names(first)
    (task,) = submitter.submitted
    assert "rahul@example.com" in task["instruction"]
    # The mailbox path: ONE approval-gated mail_send step carrying the exact message (the Gmail API
    # is never used for mail any more).
    (step,) = task["steps"]
    assert step.tool == "mail_send" and step.tool_args["to"] == ["rahul@example.com"]
    assert "task.created" in names(frames) and finished(frames) == ["ok"]
    assert names(frames)[-1] == "done"


async def test_one_email_or_job_per_reply_and_the_recipient_must_be_in_the_message(
    mail_world: World,
) -> None:
    submitter = IdempotentSubmitter()
    wire(mail_world, submitter=submitter)
    account = await sign_in(mail_world)
    rahul = {"to": "rahul@example.com", "subject": "Meeting", "body": "Moved to 5 pm."}
    mail_world.models.queued_stream_scripts = [
        call("send_email", {"to": "boss@example.com", "subject": "Hi", "body": "Hello."}),
        call("send_email", rahul),
        call("send_email", rahul),
        [{"text": "The email is waiting for your approval."}],
    ]
    frames = events(
        await send(mail_world, account, "Send an email to rahul@example.com about the meeting")
    )

    assert finished(frames) == ["error", "ok", "skipped"]
    assert len(submitter.submitted) == 1
    assert "boss@example.com" not in submitter.submitted[0]["instruction"]


async def test_without_gmail_send_email_is_not_offered_and_the_route_is_unchanged(
    loop_world: World,
) -> None:
    submitter = IdempotentSubmitter()
    wire(loop_world, submitter=submitter)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [[{"text": "I can prepare that approval request."}]]
    await send(loop_world, account, "Send an email to rahul@example.com saying hello")

    (only,) = stream_calls(loop_world)
    assert "send_email" not in tool_names(only)
    # No Gmail: no tool can queue the email, so the model is told so.
    assert tool_names(only) == []
    assert "no mailbox is connected" in system_text(only)
