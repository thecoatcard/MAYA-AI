"""Chat replies that must not say more than the system knows, and the Notion tools in the loop.

Regressions from the owner's run: after "add a java notes page in that db" MAYA made a pending
approval; the next message "contniue" repeated the same announcement, a failed first Notion call
had no reason, and a reply claimed things nothing had done. Everything runs on fakes: a scripted
model (``world.models``), the stateful ``FakeNotionApi`` and a fake job reader. No real Gemini or
Notion is called.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import Depends

from app.api.v1.chat import get_chat_service
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.mcp.notion_tools import NotionToolkit
from app.services.chat_grounding import SessionJob, payload_hash
from app.services.chat_repo import ChatRepoTools
from app.services.chat_service import ChatService
from app.services.document_tools import JobView
from tests.integration.test_chat_inline_files import IdempotentSubmitter, call
from tests.integration.test_chat_memory import tool_names
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    send,
    sign_in,
    stream_calls,
)
from tests.support.notion_fake import DB, PAGE, FakeNotionApi

Clock = Any


class NotionRepo(ChatRepoTools):
    """Notion connected, on the stateful fake API (the saved database is ``DB`` unless None)."""

    def __init__(self, api: FakeNotionApi, database: str | None = DB) -> None:
        super().__init__(None, None, None)  # type: ignore[arg-type]
        self.api = api
        self.database = database

    async def notion_available(self, text: str) -> bool:
        return True

    def new_notion_toolkit(self, **limits: int) -> NotionToolkit:
        return self.api.toolkit(database=self.database)


class FakeJobReader:
    """The task rows of this conversation, as the real reader reports them."""

    def __init__(self, jobs: list[SessionJob] | None = None) -> None:
        self.jobs = jobs or []

    async def read(self, *, workspace_id: UUID, user_id: UUID, job_id: UUID) -> JobView | None:
        return None

    async def session_activity(self, **_kwargs: Any) -> list[SessionJob]:
        return list(self.jobs)


def wire(
    world: World, *, repo: NotionRepo, submitter: Any, reader: FakeJobReader | None = None
) -> None:
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
            repo_tools=repo,
            job_reader=reader,
        )

    world.app.dependency_overrides[get_chat_service] = build


@pytest.fixture
async def loop_world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=True) as value:
        yield value


def system_text(record: dict[str, Any]) -> str:
    return str(record["config"].system_instruction)


def response_of(record: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], record["contents"][-1]["parts"][0]["function_response"]["response"])


def reply_text(frames: list[Any]) -> str:
    return "".join(f.data["text"] for f in frames if f.event == "delta")


def seeded_api() -> FakeNotionApi:
    api = FakeNotionApi()
    api.database_titles[DB] = "Study Notes"
    api.database_options[DB] = {"Status": ["Todo", "In progress", "Done"]}
    api.pages[PAGE]["properties"] = {
        "title": {"type": "title", "title": [{"plain_text": "Java Notes"}]}
    }
    api.pages[PAGE]["parent"] = {"type": "workspace"}
    return api


def title(text: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": text}}]}


# ------------------------------------------------------------------- "what is in my Notion"


async def test_what_is_in_my_notion_lists_databases_queries_then_answers_with_sources(
    loop_world: World,
) -> None:
    api = seeded_api()
    api.add_row(DB, Name=title("Collections"), Status={"status": {"name": "Todo"}})
    api.add_row(DB, Name=title("Streams"), Status={"status": {"name": "Done"}})
    wire(loop_world, repo=NotionRepo(api), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("notion_list_databases", {}),
        call(
            "notion_query_database",
            {
                "database_id": DB,
                "filters": [{"property": "Status", "op": "equals", "value": "Done"}],
            },
        ),
        [{"text": "You have 1 finished note: Streams. Source: Notion database 'Study Notes'."}],
    ]

    frames = events(await send(loop_world, account, "what is in my Notion?"))

    calls = stream_calls(loop_world)
    assert {"notion_list_databases", "notion_replace_section"} <= set(tool_names(calls[0]))
    assert "Source: Notion page" in system_text(calls[0])  # the rule to cite sources is given
    listed = response_of(calls[1])
    assert listed["ok"] is True and "Study Notes" in listed["result"] and DB in listed["result"]
    queried = response_of(calls[2])
    assert queried["ok"] is True and "Streams" in queried["result"]
    assert "Collections" not in queried["result"]  # only what the filter returned
    text = reply_text(frames)
    assert "Source: Notion database 'Study Notes'" in text and "Note:" not in text
    assert names(frames)[-1] == "done"


async def test_a_failed_notion_read_adds_what_could_not_be_read_and_why(
    loop_world: World,
) -> None:
    api = seeded_api()
    api.fail_reads = True
    wire(loop_world, repo=NotionRepo(api), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("notion_get_database", {}),
        [{"text": "You have notes on Java, Python and Go."}],
    ]

    frames = events(await send(loop_world, account, "what is in my Notion?"))

    text = reply_text(frames)
    assert "I could not read your Notion in this reply" in text
    assert "nothing above about it comes from your data" in text


async def test_a_reply_that_already_says_it_could_not_read_gets_no_second_note(
    loop_world: World,
) -> None:
    api = seeded_api()
    api.fail_reads = True
    wire(loop_world, repo=NotionRepo(api), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("notion_get_database", {}),
        [{"text": "I could not read your Notion: it returned a server error."}],
    ]

    frames = events(await send(loop_world, account, "what is in my Notion?"))

    assert reply_text(frames).count("could not read") == 1


# ---------------------------------------------------------------- add a page in "that db"


async def test_add_a_java_notes_page_reads_the_schema_then_proposes_one_approval_with_names(
    loop_world: World,
) -> None:
    api = seeded_api()
    api.databases[DB] = {"Topic": "title", "Status": "status"}
    submitter = IdempotentSubmitter()
    wire(loop_world, repo=NotionRepo(api), submitter=submitter)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("notion_get_database", {}),
        # the model says "Title" although this database calls its title column "Topic"
        call(
            "notion_add_row",
            {"properties": [{"name": "Title", "value": "Java Notes"}], "markdown": "# Java\n- OOP"},
        ),
        [{"text": "Java Notes is prepared; it waits for your approval."}],
    ]

    frames = events(await send(loop_world, account, "can u add a java notes page in that db"))

    calls = stream_calls(loop_world)
    assert "call notion_get_database" in system_text(calls[0])  # schema first, in the rules
    schema = response_of(calls[1])["result"]
    assert "Topic | title" in schema and "options: Todo, In progress, Done" in schema
    waiting = response_of(calls[2])
    assert waiting["status"] == "waiting_for_approval"  # no failed first attempt
    assert "Study Notes" in waiting["summary"] and DB not in waiting["summary"]
    assert "'Java Notes'" in waiting["summary"]
    assert len(submitter.submitted) == 1 and api.writes == []
    assert "task.created" in names(frames)
    assert not [f for f in frames if f.event == "progress" and f.data["state"] == "failed"]


async def test_a_failed_first_write_shows_a_plain_reason_and_the_reply_describes_the_end_state(
    loop_world: World,
) -> None:
    api = seeded_api()
    submitter = IdempotentSubmitter()
    wire(loop_world, repo=NotionRepo(api), submitter=submitter)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("notion_add_row", {"properties": [{"name": "Colour", "value": "blue"}]}),
        call(
            "notion_add_row",
            {"properties": [{"name": "Name", "value": "Java Notes"}]},
        ),
        [{"text": "It waits for your approval."}],
    ]

    frames = events(await send(loop_world, account, "add Java Notes to the db"))

    calls = stream_calls(loop_world)
    failed = [f.data for f in frames if f.event == "progress" and f.data["state"] == "failed"]
    assert failed and "no column named 'Colour'" in failed[0]["detail"]
    assert "Its columns are" in failed[0]["detail"] or "columns" in failed[0]["detail"]
    # the model sees, in its rules, that an earlier call failed and must report the final state
    assert "A tool call failed earlier in this turn" in system_text(calls[2])
    assert "Describe only the final state" in system_text(calls[2])
    assert response_of(calls[2])["status"] == "waiting_for_approval"
    assert len(submitter.submitted) == 1


# ------------------------------------------------------------------------ "contniue"


async def test_contniue_reports_the_state_once_and_creates_no_second_approval(
    loop_world: World,
) -> None:
    api = seeded_api()
    submitter = IdempotentSubmitter()
    repo = NotionRepo(api)
    # First, what the model proposes for the row; its hash is what the task row carries.
    from app.mcp.notion_write import ADD_ROW, propose_write

    args = {"properties": [{"name": "Name", "value": "Java Notes"}]}
    proposal = await propose_write(repo.new_notion_toolkit(), ADD_ROW, args)
    waiting = SessionJob(
        task_id=uuid4(),
        title=proposal.summary,
        status="awaiting_approval",
        tool=ADD_ROW,
        payload_hash=payload_hash(ADD_ROW, proposal.tool_args),
    )
    reader = FakeJobReader([waiting])
    wire(loop_world, repo=repo, submitter=submitter, reader=reader)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        # a model that ignores the rules and proposes the same row again
        call("notion_add_row", args),
        [{"text": "Still waiting for your approval on the card above."}],
    ]

    frames = events(await send(loop_world, account, "contniue"))

    calls = stream_calls(loop_world)
    system = system_text(calls[0])
    assert "CURRENT STATE OF THIS CONVERSATION" in system
    assert "waiting for the user's approval" in system
    assert "Do NOT restate" in system
    again = response_of(calls[1])
    assert again["status"] == "already_waiting" and "ALREADY waiting" in again["message"]
    assert submitter.submitted == []  # no duplicate approval
    text = reply_text(frames)
    assert "Still waiting" in text and "I have prepared" not in text
    assert "Correction" not in text
    assert "task.created" not in names(frames)
    assert not [f for f in frames if f.event == "progress" and f.data["state"] == "failed"]


async def test_a_followup_with_nothing_pending_says_so_and_asks(loop_world: World) -> None:
    api = seeded_api()
    wire(loop_world, repo=NotionRepo(api), submitter=IdempotentSubmitter(), reader=FakeJobReader())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [[{"text": "Nothing is pending. What next?"}]]

    await send(loop_world, account, "aage badho")

    assert "No job is pending or running" in system_text(stream_calls(loop_world)[0])


async def test_a_normal_question_gets_no_followup_state(loop_world: World) -> None:
    api = seeded_api()
    wire(loop_world, repo=NotionRepo(api), submitter=IdempotentSubmitter(), reader=FakeJobReader())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [[{"text": "Here is a plan."}]]

    await send(loop_world, account, "Plan my week in three points")

    assert "CURRENT STATE OF THIS CONVERSATION" not in system_text(stream_calls(loop_world)[0])


# ------------------------------------------------------------------- truthful action claims


async def test_a_claimed_action_with_no_successful_tool_gets_an_honest_correction(
    loop_world: World,
) -> None:
    api = seeded_api()
    wire(loop_world, repo=NotionRepo(api), submitter=IdempotentSubmitter(), reader=FakeJobReader())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("notion_add_row", {"properties": [{"name": "Colour", "value": "x"}]}),
        [{"text": "I have added the Java Notes page to your Notion."}],
    ]

    frames = events(await send(loop_world, account, "add the java notes page"))

    text = reply_text(frames)
    assert "Correction: nothing was created, saved or sent by this reply." in text
    assert "failed" in text and "no column named 'Colour'" in text
    assert "Nothing is pending for your approval either." in text


async def test_a_prepared_claim_backed_by_a_pending_approval_is_left_alone(
    loop_world: World,
) -> None:
    api = seeded_api()
    waiting = SessionJob(
        task_id=uuid4(),
        title="Add row Java Notes",
        status="awaiting_approval",
        tool="notion_add_row",
    )
    wire(
        loop_world,
        repo=NotionRepo(api),
        submitter=IdempotentSubmitter(),
        reader=FakeJobReader([waiting]),
    )
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        [{"text": "I have prepared the request for the Java Notes row. It needs approval."}]
    ]

    frames = events(await send(loop_world, account, "how is it going with the page request"))

    assert "Correction" not in reply_text(frames)


async def test_a_claim_backed_by_a_successful_write_proposal_is_left_alone(
    loop_world: World,
) -> None:
    api = seeded_api()
    submitter = IdempotentSubmitter()
    wire(loop_world, repo=NotionRepo(api), submitter=submitter, reader=FakeJobReader())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("notion_add_row", {"properties": [{"name": "Name", "value": "Java Notes"}]}),
        [{"text": "I have prepared the request to add the page. It needs your approval."}],
    ]

    frames = events(await send(loop_world, account, "add a java notes page"))

    assert "Correction" not in reply_text(frames) and len(submitter.submitted) == 1


# ------------------------------------------------------- working with existing content


async def test_add_notes_to_an_existing_page_finds_it_then_proposes_an_append(
    loop_world: World,
) -> None:
    api = seeded_api()
    submitter = IdempotentSubmitter()
    wire(loop_world, repo=NotionRepo(api), submitter=submitter, reader=FakeJobReader())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("notion_find_page", {"query": "Java Notes"}),
        call(
            "notion_append_blocks",
            {"page_id": PAGE, "markdown": "## Generics\n\n- **Bounded** types\n"},
        ),
        [{"text": "The notes are ready to append to 'Java Notes' once you approve."}],
    ]

    await send(loop_world, account, "add these notes to my existing 'Java Notes' page: generics")

    calls = stream_calls(loop_world)
    found = response_of(calls[1])["result"]
    assert "Java Notes" in found and PAGE in found
    waiting = response_of(calls[2])
    assert waiting["status"] == "waiting_for_approval" and "'Java Notes'" in waiting["summary"]
    assert "heading_2" in waiting["payload"] and "bold" in waiting["payload"]
    assert len(submitter.submitted) == 1 and api.writes == []


async def test_change_the_status_of_a_row_queries_it_then_proposes_a_typed_update(
    loop_world: World,
) -> None:
    api = seeded_api()
    row = api.add_row(DB, Name=title("Java Notes"), Status={"status": {"name": "Todo"}})
    submitter = IdempotentSubmitter()
    wire(loop_world, repo=NotionRepo(api), submitter=submitter, reader=FakeJobReader())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call(
            "notion_query_database",
            {"filters": [{"property": "Name", "op": "contains", "value": "Java Notes"}]},
        ),
        call(
            "notion_update_page",
            {"page_id": row, "properties": [{"name": "Status", "value": "Done"}]},
        ),
        [{"text": "The status change waits for your approval."}],
    ]

    await send(loop_world, account, "change the status of the 'Java Notes' row to Done")

    calls = stream_calls(loop_world)
    assert row in response_of(calls[1])["result"]
    waiting = response_of(calls[2])
    assert waiting["status"] == "waiting_for_approval"
    assert '"status": {' in waiting["payload"] and "Done" in waiting["payload"]
    assert len(submitter.submitted) == 1 and api.writes == []


async def test_replace_a_section_reads_the_page_and_lists_what_will_be_removed(
    loop_world: World,
) -> None:
    api = seeded_api()
    for block in (
        {
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Collections"}}]},
        },
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": "Old collections text"}}]
            },
        },
        {
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Streams"}}]},
        },
    ):
        api.add_block(PAGE, block)
    submitter = IdempotentSubmitter()
    wire(loop_world, repo=NotionRepo(api), submitter=submitter, reader=FakeJobReader())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("notion_get_page", {"page_id": PAGE}),
        call(
            "notion_replace_section",
            {"page_id": PAGE, "heading": "Collections", "markdown": "New text about lists"},
        ),
        [{"text": "The replacement waits for your approval."}],
    ]

    await send(loop_world, account, "replace the Collections section on that page")

    calls = stream_calls(loop_world)
    page = response_of(calls[1])["result"]
    assert "Top-level blocks (id | type | start of text)" in page
    waiting = response_of(calls[2])
    assert waiting["status"] == "waiting_for_approval"
    assert "Removes 1 block" in waiting["summary"] and "Old collections text" in waiting["summary"]
    assert "Streams" not in waiting["summary"].split("Inserts")[0]
    assert len(submitter.submitted) == 1 and api.writes == []
