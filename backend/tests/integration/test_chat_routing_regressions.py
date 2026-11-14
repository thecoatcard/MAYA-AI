"""Regressions from the owner's desktop run: a memory statement is not a file request, an email
without Gmail never claims an approval, typing "approved" approves nothing, and a Notion page with
no named parent is found through a workspace search (all on fakes; no model, Gmail or Notion)."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from app.mcp.notion_tools import NotionToolkit
from app.services.chat_repo import ChatRepoTools
from app.services.chat_router import Route, route_message
from tests.integration.test_chat_inline_files import IdempotentSubmitter, call
from tests.integration.test_chat_memory import FakeMemory, Prefs, tool_names
from tests.integration.test_chat_phase2_tools import wire
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    send,
    sign_in,
    stream_calls,
)
from tests.support.notion_fake import FakeNotionApi

ADDRESS = "owner@example.com"
PARENT = "11112222333344445555666677778888"


@pytest.fixture
async def loop_world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=True) as value:
        yield value


@pytest.fixture
async def mail_world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(
        artifacts_dir=tmp_path / "artifacts",
        chat_tool_loop=True,
        mail_address="owner@example.com",
        mail_app_password="fake-app-password-1234",
    ) as value:
        yield value


def system_text(record: dict[str, Any]) -> str:
    return str(record["config"].system_instruction)


# --------------------------------------------------------------------------------- bug 1


@pytest.mark.parametrize(
    "text",
    [
        "Yaad rakho: mujhe reports Hinglish mein chahiye",
        "yaad rakhna ki mujhe reports Hindi mein chahiye",
        "Remember that I want reports in Hinglish",
        "Remember: I like my reports short",
        "Please remember I want reports in tables",
        "Save this preference: reports in Hinglish",
        "याद रखो मुझे रिपोर्ट हिंदी में चाहिए",
    ],
)
def test_a_memory_statement_is_answered_never_a_file_request(text: str) -> None:
    decision = route_message(text)
    assert decision.route is Route.ANSWER and decision.reason == "memory_statement"


@pytest.mark.parametrize(
    "text",
    ["ek report PDF banao", "Make a report on solar power", "yaad rakho aur ek PDF banao"],
)
def test_a_real_file_request_is_still_a_file_request(text: str) -> None:
    assert route_message(text).reason != "memory_statement"


async def test_a_preference_with_the_word_reports_saves_memory_and_offers_no_document_tool(
    loop_world: World,
) -> None:
    memory = FakeMemory()
    wire(loop_world, memory=memory, prefs=Prefs())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("save_memory", {"text": "Wants reports in Hinglish", "kind": "preference"}),
        [{"text": "Theek hai, yaad rakh liya."}],
    ]

    frames = events(
        await send(loop_world, account, "Yaad rakho: mujhe reports Hinglish mein chahiye")
    )

    first = stream_calls(loop_world)[0]
    assert "save_memory" in tool_names(first)
    assert not {"create_document", "start_job", "submit_task"} & set(tool_names(first))
    assert memory.saves == [("Wants reports in Hinglish", "preference")]
    assert "memory.saved" in names(frames) and "file.created" not in names(frames)


# --------------------------------------------------------------------------------- bug 2


async def test_email_draft_without_gmail_says_so_and_creates_no_approval(
    loop_world: World,
) -> None:
    submitter = IdempotentSubmitter()
    wire(loop_world, submitter=submitter)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        [{"text": "Gmail is not connected, so nothing was sent."}]
    ]

    await send(loop_world, account, f"{ADDRESS} par ek chhoti test mail draft karo")

    first = stream_calls(loop_world)[0]
    assert not {"send_email", "submit_task"} & set(tool_names(first))
    assert "no mailbox is connected" in system_text(first)
    assert "no approval was created" in system_text(first)
    assert submitter.submitted == []


async def test_email_draft_with_gmail_creates_a_real_approval_task(mail_world: World) -> None:
    submitter = IdempotentSubmitter()
    wire(mail_world, submitter=submitter)
    account = await sign_in(mail_world)
    mail_world.models.queued_stream_scripts = [
        call("send_email", {"to": ADDRESS, "subject": "Test", "body": "Ye ek test mail hai."}),
        [{"text": "Approval card me approval ke liye taiyar hai."}],
    ]

    frames = events(
        await send(mail_world, account, f"{ADDRESS} par ek chhoti test mail draft karo")
    )

    assert "send_email" in tool_names(stream_calls(mail_world)[0])
    assert len(submitter.submitted) == 1 and "task.created" in names(frames)


@pytest.mark.parametrize("text", ["approved", "Approved.", "ok approved", "approve kar do"])
async def test_typing_approved_in_chat_approves_nothing(loop_world: World, text: str) -> None:
    submitter = IdempotentSubmitter()
    wire(loop_world, submitter=submitter)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        [{"text": "Use the Approve button on the card above."}]
    ]

    await send(loop_world, account, text)

    first = stream_calls(loop_world)[0]
    assert route_message(text).reason == "approval_in_chat"
    assert "Approve button on the card above" in system_text(first)
    assert "Approval Center" not in system_text(first)
    assert not {"send_email", "submit_task"} & set(tool_names(first))
    assert submitter.submitted == []


# --------------------------------------------------------------------------------- bug 3


class WorkspaceNotionRepo(ChatRepoTools):
    """Notion connected with no tasks database, over the stateful fake API."""

    def __init__(self, api: FakeNotionApi) -> None:
        super().__init__(None, None, None)  # type: ignore[arg-type]
        self.api = api

    async def notion_available(self, text: str) -> bool:
        return True

    def new_notion_toolkit(self, **limits: int) -> NotionToolkit:
        return self.api.toolkit(database=None)


async def test_a_notion_page_without_a_parent_is_found_by_search_then_proposed(
    loop_world: World,
) -> None:
    api = FakeNotionApi()
    api.pages[PARENT] = {
        "object": "page",
        "id": PARENT,
        "url": "https://notion.so/projects",
        "parent": {"type": "workspace"},
        "properties": {"title": {"type": "title", "title": [{"plain_text": "MAYA Projects"}]}},
    }
    api.children[PARENT] = []
    submitter = IdempotentSubmitter()
    wire(loop_world, submitter=submitter, repo_tools=WorkspaceNotionRepo(api))
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("notion_create_page", {"title": "MAYA Test"}),
        call("notion_search", {"query": "MAYA", "scope": "workspace"}),
        call("notion_create_page", {"title": "MAYA Test", "parent_page_id": PARENT}),
        [{"text": "The page waits for your approval under MAYA Projects."}],
    ]

    frames = events(
        await send(
            loop_world,
            account,
            "Notion mein ek page add karo aur usme 'MAYA Test' naam ka page banao",
        )
    )

    calls = stream_calls(loop_world)
    refused = calls[1]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert refused["status"] == "error" and "notion_search" in refused["message"]
    found = calls[2]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert found["ok"] is True and PARENT in found["result"] and "MAYA Projects" in found["result"]
    waiting = calls[3]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert waiting["status"] == "waiting_for_approval"
    assert len(submitter.submitted) == 1 and not [
        w for w in api.writes if w.url.path != "/v1/search"
    ]
    failed = [
        f.data
        for f in frames
        if f.event == "progress" and f.data["state"] == "failed" and f.data.get("detail")
    ]
    assert failed and "Say where the page goes" in failed[0]["detail"]
