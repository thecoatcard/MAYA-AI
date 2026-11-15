"""Notion writes: payload building and validation, the approval gate, read-back verification and
plain error messages.

All Notion HTTP goes to ``tests.support.notion_fake.FakeNotionApi`` (``httpx.MockTransport``);
nothing reaches the network. The approval tests run the real ``SubtaskWorker``, the real
``McpGateway`` with the built-in server and the real permission registry and approval tokens over
a recording database fake: zero write requests before approval, exactly one after, none after a
rejection, none when the payload was changed after approval.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest

from app.agents.planner import PLANNER_TOOL_NAMES
from app.connections.providers import NOTION_DATABASE_NOT_SHARED, NOTION_PAGE_NOT_SHARED
from app.db.models import PlanStatus, Subtask, SubtaskAgent, SubtaskStatus, Task
from app.mcp.client_manager import MCPClientManager
from app.mcp.gateway import BUILTIN_MANIFEST, SERVED_MANIFEST, McpGateway, builtin_spec
from app.mcp.notion_tools import NOT_CONNECTED_MESSAGE, NotionToolkit
from app.mcp.notion_write import (
    ADD_ROW,
    APPEND_BLOCKS,
    CREATE_PAGE,
    NOT_APPROVED_MESSAGE,
    NOTION_WRITE_TOOL_NAMES,
    UPDATE_ROW,
    UPLOAD_UNAVAILABLE,
    NotionWriteError,
    WriteProposal,
    build_block,
    build_properties,
    chat_write_call,
    chunk_text,
    execute_write,
    propose_write,
    rich_text,
    validate_requests,
    write_step,
)
from app.mcp.permissions import (
    DEFAULT_TOOL_PERMISSIONS,
    PermissionDeniedError,
    ToolPermissionRegistry,
    ToolTier,
)
from app.mcp.scope import tool_call_scope
from app.mcp.tokens import mint_approval_token
from app.services.task_submission import TaskSubmission, TaskSubmissionService
from app.tasks.queue import SubtaskJob
from app.tasks.worker import SubtaskWorker
from tests.support.notion_fake import DB, PAGE, ROW, SCHEMA, TOKEN, FakeNotionApi

JWT_KEY = "k" * 32
BLOCKS: list[dict[str, Any]] = [
    {"type": "heading_2", "text": "Launch plan"},
    {"type": "paragraph", "text": "We ship on Friday."},
    {"type": "bulleted_list_item", "text": "Freeze the branch"},
    {"type": "to_do", "text": "Tell support", "checked": True},
    {"type": "code", "text": "print('go')", "language": "Python"},
    {"type": "callout", "text": "Check the rollback plan first."},
    {"type": "table", "has_header": True, "rows": [["Step", "Owner"], ["Deploy", "Asha"]]},
    {"type": "image", "url": "https://example.org/chart.png"},
]


async def propose(fake: FakeNotionApi, name: str, args: dict[str, Any]) -> WriteProposal:
    return await propose_write(fake.toolkit(), name, args)


async def error_of(fake: FakeNotionApi, name: str, args: dict[str, Any]) -> str:
    with pytest.raises(NotionWriteError) as caught:
        await propose(fake, name, args)
    return str(caught.value)


# ------------------------------------------------------------------------------- tiers / manifest


def test_write_tools_need_approval_and_the_planner_cannot_name_them() -> None:
    served = {t["name"]: t for t in SERVED_MANIFEST}
    for name in NOTION_WRITE_TOOL_NAMES:
        assert DEFAULT_TOOL_PERMISSIONS[name] is ToolTier.APPROVAL_REQUIRED
        registry = ToolPermissionRegistry()
        assert registry.requires_human_approval(name) and not registry.is_allowed_autonomous(name)
        with pytest.raises(PermissionDeniedError):
            registry.authorize_tool_call(name, {"requests": []})
        assert served[name]["inputSchema"]["required"] == ["requests"]
        # Served by the gateway, but not offered to the planner (a write step is only ever built
        # by the chat tool call that validated its payload).
        assert name not in {t["name"] for t in BUILTIN_MANIFEST}
        assert name not in PLANNER_TOOL_NAMES


# ------------------------------------------------------------------------------ sizes / blocks


def test_long_text_is_cut_into_items_of_at_most_2000_characters() -> None:
    text = "a" * 1999 + "\U0001f600" + "b" * 2500  # the emoji is two UTF-16 units
    items = rich_text(text)
    pieces = [item["text"]["content"] for item in items]
    assert "".join(pieces) == text
    assert all(len(p.encode("utf-16-le")) // 2 <= 2000 for p in pieces)
    assert len(pieces) == 3 and pieces[0] == "a" * 1999  # never cut inside the emoji
    assert chunk_text("") == [] and chunk_text("abc", limit=2) == ["ab", "c"]
    with pytest.raises(NotionWriteError, match="too long for one Notion block"):
        rich_text("x" * 200_001)


def test_every_supported_block_type_builds_the_notion_shape() -> None:
    built = [build_block(spec, n) for n, spec in enumerate(BLOCKS, start=1)]
    assert [b["type"] for b in built] == [
        "heading_2",
        "paragraph",
        "bulleted_list_item",
        "to_do",
        "code",
        "callout",
        "table",
        "image",
    ]
    assert built[3]["to_do"]["checked"] is True
    assert built[4]["code"]["language"] == "python"
    table = built[6]["table"]
    assert table["table_width"] == 2 and table["has_column_header"] is True
    assert table["children"][1]["table_row"]["cells"][1][0]["text"]["content"] == "Asha"
    assert built[7]["image"] == {
        "type": "external",
        "external": {"url": "https://example.org/chart.png"},
    }
    assert build_block({"type": "h1", "text": "T"}, 1)["type"] == "heading_1"


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"type": "embed", "text": "x"}, "not supported"),
        ({"type": "bookmark", "text": "x"}, "not supported"),
        ({"type": "heading_4", "text": "x"}, "not supported"),
        ({"text": "no type"}, "not supported"),
        ("just text", "not an object"),
        ({"type": "heading_1", "text": "  "}, "needs 'text'"),
        ({"type": "code", "text": "x", "language": "klingon"}, "not a language"),
        ({"type": "table", "rows": [["a", "b"], ["c"]]}, "every row must have 2 cells"),
        ({"type": "table", "rows": []}, "needs 'rows'"),
        ({"type": "image", "url": "http://example.org/a.png"}, "https://"),
        ({"type": "image", "url": "https://example.org/" + "a" * 2000}, "longer than 2000"),
    ],
)
def test_bad_blocks_are_refused_with_a_plain_reason(spec: Any, message: str) -> None:
    with pytest.raises(NotionWriteError, match=message):
        build_block(spec, 1)


def test_an_image_without_a_url_says_upload_is_not_available() -> None:
    with pytest.raises(NotionWriteError) as caught:
        build_block({"type": "image"}, 1)
    assert str(caught.value) == UPLOAD_UNAVAILABLE


async def test_append_is_split_into_requests_of_at_most_100_blocks() -> None:
    fake = FakeNotionApi()
    blocks = [{"type": "paragraph", "text": f"line {n}"} for n in range(250)]
    proposal = await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": blocks})
    requests = proposal.tool_args["requests"]
    assert [len(r["body"]["children"]) for r in requests] == [100, 100, 50]
    assert {(r["method"], r["path"]) for r in requests} == {("PATCH", f"/blocks/{PAGE}/children")}
    assert requests[2]["body"]["children"][-1]["paragraph"]["rich_text"][0]["text"] == {
        "content": "line 249"
    }
    too_many = [*blocks, *blocks]
    assert "at most 300" in await error_of(
        fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": too_many}
    )
    # a new page carries at most one request of blocks
    assert "at most 100" in await error_of(
        fake, CREATE_PAGE, {"title": "T", "parent_page_id": PAGE, "blocks": blocks}
    )
    assert fake.writes == []


# -------------------------------------------------------------------------------- properties


def test_properties_are_typed_by_the_real_database_schema() -> None:
    built = build_properties(
        SCHEMA,
        [
            {"name": "Name", "value": "Ship deck"},
            {"name": "status", "value": "In progress"},  # case-insensitive column match
            {"name": "Due", "value": "2026-11-30 to 2026-12-02"},
            {"name": "Points", "value": "3"},
            {"name": "Tags", "value": "urgent, client, urgent"},
            {"name": "Done", "value": "yes"},
            {"name": "Notes", "value": "n" * 2500},
            {"name": "Link", "value": ""},
        ],
    )
    assert built["Name"] == {"title": [{"type": "text", "text": {"content": "Ship deck"}}]}
    assert built["Status"] == {"status": {"name": "In progress"}}
    assert built["Due"] == {"date": {"start": "2026-11-30", "end": "2026-12-02"}}
    assert built["Points"] == {"number": 3}
    assert built["Tags"] == {"multi_select": [{"name": "urgent"}, {"name": "client"}]}
    assert built["Done"] == {"checkbox": True}
    assert [len(i["text"]["content"]) for i in built["Notes"]["rich_text"]] == [2000, 500]
    assert built["Link"] == {"url": None}  # an empty value clears the column


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({"name": "Priority", "value": "High"}, r"no column named 'Priority'.*Name \(title\)"),
        ({"name": "Owner", "value": "Asha"}, "is a people column"),
        ({"name": "Points", "value": "three"}, "is not a number"),
        ({"name": "Due", "value": "next Friday"}, "is not an ISO date"),
        ({"name": "Done", "value": "maybe"}, "use true or false"),
        ({"name": "Name", "value": ""}, "cannot be empty"),
    ],
)
def test_wrong_columns_and_values_are_refused_before_anything_is_proposed(
    item: dict[str, str], message: str
) -> None:
    with pytest.raises(NotionWriteError, match=message):
        build_properties(SCHEMA, [item])


# ------------------------------------------------------------------- proposing never writes


async def test_proposals_carry_the_exact_requests_and_never_write() -> None:
    fake = FakeNotionApi()
    page = await propose(
        fake, CREATE_PAGE, {"title": "Notes", "parent_page_id": PAGE, "blocks": BLOCKS[:2]}
    )
    row = await propose(
        fake,
        ADD_ROW,
        {"properties": [{"name": "Name", "value": "Ship deck"}, {"name": "Points", "value": "5"}]},
    )
    update = await propose(
        fake, UPDATE_ROW, {"page_id": ROW, "properties": [{"name": "Status", "value": "Done"}]}
    )
    row_page = await propose(fake, CREATE_PAGE, {"title": "Row page", "database_id": DB})

    assert page.tool == CREATE_PAGE and page.tool_args["requests"][0]["path"] == "/pages"
    body = page.tool_args["requests"][0]["body"]
    assert body["parent"] == {"page_id": PAGE}
    assert body["properties"]["title"]["title"][0]["text"]["content"] == "Notes"
    assert [b["type"] for b in body["children"]] == ["heading_2", "paragraph"]
    assert row.tool_args["requests"] == [
        {
            "method": "POST",
            "path": "/pages",
            "body": {
                "parent": {"database_id": DB},
                "properties": {
                    "Name": {"title": [{"type": "text", "text": {"content": "Ship deck"}}]},
                    "Points": {"number": 5},
                },
            },
        }
    ]
    assert update.tool_args["requests"] == [
        {
            "method": "PATCH",
            "path": f"/pages/{ROW}",
            "body": {"properties": {"Status": {"status": {"name": "Done"}}}},
        }
    ]
    assert row_page.tool_args["requests"][0]["body"]["parent"] == {"database_id": DB}
    for proposal in (page, row, update, row_page):
        assert validate_requests(proposal.tool, proposal.tool_args["requests"])
        assert json.loads(proposal.payload_text()) == proposal.tool_args
    # reads only: GETs and the duplicate check (a POST database query), never a write
    assert fake.writes == []


async def test_proposal_errors_are_plain_and_need_no_write() -> None:
    fake = FakeNotionApi()
    assert "not both" in await error_of(
        fake, CREATE_PAGE, {"title": "T", "parent_page_id": PAGE, "database_id": DB}
    )
    assert "Say where the page goes" in await error_of(fake, CREATE_PAGE, {"title": "T"})
    assert "not a Notion page link or id" in await error_of(
        fake, APPEND_BLOCKS, {"page_id": "my page", "blocks": BLOCKS[:1]}
    )
    assert "not a row of a database" in await error_of(
        fake, UPDATE_ROW, {"page_id": PAGE, "properties": [{"name": "Name", "value": "x"}]}
    )
    assert "title column 'Name'" in await error_of(
        fake, ADD_ROW, {"properties": [{"name": "Points", "value": "1"}]}
    )
    unknown_db = "9" * 32
    assert await error_of(
        fake, ADD_ROW, {"database_id": unknown_db, "properties": [{"name": "Name", "value": "x"}]}
    ) == (NOTION_DATABASE_NOT_SHARED)
    assert await error_of(
        fake, UPDATE_ROW, {"page_id": "8" * 32, "properties": [{"name": "Name", "value": "x"}]}
    ) == (NOTION_PAGE_NOT_SHARED)

    async def nobody() -> None:
        return None

    with pytest.raises(NotionWriteError) as caught:
        await propose_write(NotionToolkit(nobody), APPEND_BLOCKS, {"page_id": PAGE, "blocks": []})
    assert str(caught.value) == NOT_CONNECTED_MESSAGE  # missing token
    assert fake.writes == []


# ------------------------------------------------------------------- approved payload checks


async def test_a_changed_payload_is_refused_before_any_request() -> None:
    fake = FakeNotionApi()
    good = (await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": BLOCKS[:1]})).tool_args
    request = good["requests"][0]
    block = request["body"]["children"][0]
    upload = {"type": "image", "image": {"type": "file_upload", "file_upload": {"id": "x"}}}
    bad_lists: list[Any] = [
        None,
        [],
        [{**request, "method": "DELETE"}],
        [{**request, "path": "/pages"}],
        [{**request, "path": f"/blocks/{PAGE}"}],
        [{**request, "path": "/users/me"}],
        [{**request, "extra": 1}],
        [{**request, "body": {"children": [block], "after": "x"}}],
        [{**request, "body": {"children": [{"type": "embed", "embed": {"url": "https://x"}}]}}],
        [{**request, "body": {"children": [upload]}}],
        [{**request, "body": {"children": [block] * 101}}],
        [request, {**request, "path": f"/blocks/{ROW}/children"}],  # two different pages
        [request] * 4,
    ]
    for requests in bad_lists:
        with pytest.raises(NotionWriteError):
            await execute_write(
                APPEND_BLOCKS, {"requests": requests}, fake.connector(), approved=True
            )
    # a payload approved for one tool cannot run as another
    with pytest.raises(NotionWriteError):
        await execute_write(UPDATE_ROW, good, fake.connector(), approved=True)
    page_parent = {"parent": {"page_id": PAGE}, "properties": {"title": {"title": []}}}
    with pytest.raises(NotionWriteError):
        validate_requests(ADD_ROW, [{"method": "POST", "path": "/pages", "body": page_parent}])
    assert fake.writes == []


async def test_execute_refuses_without_the_approved_flag() -> None:
    fake = FakeNotionApi()
    args = (await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": BLOCKS})).tool_args
    for approved in (False, None, "true", 1):
        with pytest.raises(NotionWriteError) as caught:
            await execute_write(APPEND_BLOCKS, args, fake.connector(), approved=approved)  # type: ignore[arg-type]
        assert str(caught.value) == NOT_APPROVED_MESSAGE
    assert fake.writes == []


# --------------------------------------------------------------------- write, then read back


async def test_append_writes_once_and_verifies_every_block_type() -> None:
    fake = FakeNotionApi()
    fake.children[PAGE] = [
        {"object": "block", "id": "e" * 32, "type": "paragraph", "paragraph": {"rich_text": []}}
    ]
    args = (await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": BLOCKS})).tool_args

    text = await execute_write(APPEND_BLOCKS, args, fake.connector(), approved=True)

    assert len(fake.writes) == 1
    assert fake.write_bodies()[0] == args["requests"][0]["body"]  # sent exactly as approved
    assert text.startswith(f"Appended 8 blocks to the Notion page {PAGE}.")
    assert "VERIFIED by reading it back: 8 blocks match what was sent." in text
    assert len(fake.children[PAGE]) == 9


async def test_read_back_differences_are_listed_exactly() -> None:
    fake = FakeNotionApi()
    args = (await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": BLOCKS})).tool_args
    original = fake._store_blocks

    def store_changed(parent: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stored = original(parent, blocks)
        stored[1]["paragraph"]["rich_text"][0]["plain_text"] = "We ship on Monday."
        stored[3]["to_do"]["checked"] = False
        stored[0]["type"] = "heading_3"
        table_rows = fake.children[stored[6]["id"]]
        table_rows[1]["table_row"]["cells"][1][0]["plain_text"] = "Ravi"
        return stored

    fake._store_blocks = store_changed  # type: ignore[method-assign]
    text = await execute_write(APPEND_BLOCKS, args, fake.connector(), approved=True)

    assert "VERIFICATION FOUND 4 DIFFERENCES" in text
    assert "block 1 (heading_2) type: sent 'heading_2', Notion has 'heading_3'" in text
    assert (
        "block 2 (paragraph) text: sent 'We ship on Friday.', Notion has 'We ship on Monday.'"
        in text
    )
    assert "block 4 (to_do) checked: sent 'True', Notion has 'False'" in text
    assert "block 7 (table): the cell texts read back differ" in text
    assert "VERIFIED by" not in text


async def test_rows_are_created_and_updated_with_property_verification() -> None:
    fake = FakeNotionApi()
    properties = [
        {"name": "Name", "value": "Ship deck"},
        {"name": "Status", "value": "In progress"},
        {"name": "Due", "value": "2026-11-30"},
        {"name": "Points", "value": "2.5"},
        {"name": "Tags", "value": "urgent, client"},
        {"name": "Done", "value": "false"},
    ]
    added = (
        await propose(fake, ADD_ROW, {"properties": properties, "blocks": BLOCKS[:2]})
    ).tool_args
    text = await execute_write(ADD_ROW, added, fake.connector(), approved=True)
    assert len(fake.writes) == 1
    assert "Created the Notion page" in text and "Link: https://notion.so/" in text
    assert "VERIFIED by reading it back: 6 property values and 2 blocks match" in text

    update = (
        await propose(
            fake,
            UPDATE_ROW,
            {"page_id": ROW, "properties": [{"name": "Status", "value": "Done"}]},
        )
    ).tool_args
    # Notion keeps another value than the one sent (for example an automation changed it back).
    fake.write_answers.append(
        httpx.Response(200, json={"object": "page", "id": ROW, "url": "https://notion.so/row"})
    )
    text = await execute_write(UPDATE_ROW, update, fake.connector(), approved=True)
    assert len(fake.writes) == 2
    assert "VERIFICATION FOUND 1 DIFFERENCE " in text
    assert "column 'Status' (status): sent 'Done', Notion has 'Todo'" in text


async def test_a_failed_read_back_is_reported_as_not_verified_not_as_a_failed_write() -> None:
    fake = FakeNotionApi()
    args = (await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": BLOCKS[:2]})).tool_args
    fake.fail_reads = True
    text = await execute_write(APPEND_BLOCKS, args, fake.connector(), approved=True)
    assert len(fake.writes) == 1 and len(fake.children[PAGE]) == 2
    assert text.startswith("Appended 2 blocks") and "NOT VERIFIED" in text


# --------------------------------------------------------------------------------- errors


def notion_error(status: int, code: str, message: str = "", **headers: str) -> httpx.Response:
    body = {"object": "error", "status": status, "code": code, "message": message}
    return httpx.Response(status, json=body, headers=headers)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (notion_error(404, "object_not_found"), NOTION_PAGE_NOT_SHARED),
        (notion_error(403, "restricted_resource"), "Insert content and Update content"),
        (
            notion_error(400, "validation_error", "Status is expected to be status."),
            "400 validation_error): Status is expected to be status.",
        ),
        (notion_error(401, "unauthorized"), "rejected the saved token (401)"),
        (notion_error(409, "conflict_error"), "save conflict (409)"),
        (notion_error(503, "service_unavailable"), "server problem (503)"),
        (httpx.ConnectError("getaddrinfo failed"), "could not reach Notion"),
        (httpx.ReadTimeout("timed out"), "may or may not have been saved"),
    ],
)
async def test_write_failures_become_plain_sentences(answer: Any, expected: str) -> None:
    fake = FakeNotionApi()
    args = (await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": BLOCKS[:1]})).tool_args
    fake.write_answers.append(answer)
    with pytest.raises(NotionWriteError) as caught:
        await execute_write(APPEND_BLOCKS, args, fake.connector(), approved=True)
    message = str(caught.value)
    assert expected in message
    assert TOKEN not in message and "Traceback" not in message and "httpx" not in message
    assert len(fake.writes) == 1  # one attempt: an error or a timeout is never sent again
    assert fake.children[PAGE] == []


async def test_database_not_shared_on_write_names_the_database() -> None:
    fake = FakeNotionApi()
    args = (
        await propose(fake, ADD_ROW, {"properties": [{"name": "Name", "value": "x"}]})
    ).tool_args
    fake.write_answers.append(notion_error(404, "object_not_found"))
    with pytest.raises(NotionWriteError) as caught:
        await execute_write(ADD_ROW, args, fake.connector(), approved=True)
    assert str(caught.value) == NOTION_DATABASE_NOT_SHARED


async def test_rate_limit_is_retried_within_bounds_using_retry_after() -> None:
    fake = FakeNotionApi()
    args = (await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": BLOCKS[:1]})).tool_args
    limited = notion_error(429, "rate_limited", **{"Retry-After": "2"})
    fake.write_answers.extend([limited, limited])
    text = await execute_write(APPEND_BLOCKS, args, fake.connector(), approved=True)
    assert fake.sleeps == [2.0, 2.0]  # waited what Notion asked, without really sleeping
    assert len(fake.writes) == 3 and len(fake.children[PAGE]) == 1  # written exactly once
    assert "VERIFIED by reading it back" in text

    # still limited after the bounded retries: a plain message, nothing written
    fake = FakeNotionApi()
    fake.write_answers.extend([limited, limited, limited])
    with pytest.raises(NotionWriteError, match=r"rate limiting requests \(429\).*about 2 seconds"):
        await execute_write(APPEND_BLOCKS, args, fake.connector(), approved=True)
    assert len(fake.writes) == 3 and fake.children[PAGE] == []

    # a wait longer than the cap is not slept through
    fake = FakeNotionApi()
    fake.write_answers.append(notion_error(429, "rate_limited", **{"Retry-After": "120"}))
    with pytest.raises(NotionWriteError, match="about 120 seconds"):
        await execute_write(APPEND_BLOCKS, args, fake.connector(), approved=True)
    assert fake.sleeps == [] and len(fake.writes) == 1


async def test_a_partial_multi_request_append_says_what_was_written() -> None:
    fake = FakeNotionApi()
    blocks = [{"type": "paragraph", "text": f"line {n}"} for n in range(150)]
    args = (await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": blocks})).tool_args
    original = fake.__call__
    seen = {"writes": 0}

    def second_write_fails(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            seen["writes"] += 1
            if seen["writes"] == 2:
                fake.write_answers.append(notion_error(503, "service_unavailable"))
        return original(request)

    connector = fake.connector()
    connector._transport = httpx.MockTransport(second_write_fails)
    with pytest.raises(
        NotionWriteError, match=r"Only 1 of 2 requests were written \(the first 100"
    ):
        await execute_write(APPEND_BLOCKS, args, connector, approved=True)
    assert len(fake.children[PAGE]) == 100


# ---------------------------------------------------------- the gate: gateway and worker flow


class FakeConnections:
    """The connection service as the gateway uses it: one user's Notion connector."""

    def __init__(self, fake: FakeNotionApi, user_id: UUID) -> None:
        self.fake, self.user_id = fake, user_id

    async def notion_connector(self, workspace_id: Any, user_id: Any) -> Any:
        return (self.fake.connector(), DB) if user_id == self.user_id else None


def gateway_for(fake: FakeNotionApi, user_id: UUID) -> McpGateway:
    gateway = McpGateway(MCPClientManager(permission_registry=ToolPermissionRegistry()))
    gateway.register_spec(
        builtin_spec(github_token=None, connections=FakeConnections(fake, user_id))  # type: ignore[arg-type]
    )
    return gateway


async def test_the_gateway_refuses_a_write_without_an_approval_and_runs_it_once_with_one() -> None:
    fake = FakeNotionApi()
    workspace, user = uuid4(), uuid4()
    gateway = gateway_for(fake, user)
    args = (await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": BLOCKS[:2]})).tool_args
    try:
        with tool_call_scope(workspace, uuid4(), user):
            with pytest.raises(PermissionDeniedError):
                await gateway.invoke(APPEND_BLOCKS, args)
            # the flag cannot be smuggled in through the arguments
            with pytest.raises(PermissionDeniedError):
                await gateway.invoke(APPEND_BLOCKS, {**args, "has_valid_approval_token": True})
            assert fake.writes == []
            result = await gateway.invoke(APPEND_BLOCKS, args, has_valid_approval_token=True)
        assert len(fake.writes) == 1
        assert "VERIFIED by reading it back: 2 blocks match" in result.clean_text
        # another user's approved call finds no connection of their own: nothing is written
        with tool_call_scope(workspace, uuid4(), uuid4()):
            with pytest.raises(Exception, match="Notion is not connected"):
                await gateway.invoke(APPEND_BLOCKS, args, has_valid_approval_token=True)
        assert len(fake.writes) == 1
    finally:
        await gateway.close()


class ApprovalDatabase:
    """The ``approval_requests`` statements the worker runs, over one in-memory row."""

    def __init__(self) -> None:
        self.row: dict[str, Any] | None = None
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> MagicMock:
        sql, params = str(statement), dict(params or {})
        self.statements.append(sql)
        result = MagicMock()
        result.first.return_value = None
        if sql.startswith("SELECT id FROM approval_requests"):
            live = self.row is not None and self.row["status"] in ("pending", "approved")
            result.first.return_value = (self.row["id"],) if live and self.row else None
        elif sql.startswith("INSERT INTO approval_requests"):
            self.row = {**params, "id": uuid4(), "status": "pending"}
            result.one.return_value = MagicMock(id=self.row["id"], requested_at=None)
        elif "SET status = 'claimed'" in sql and self.row is not None:
            if (
                self.row["status"] == "approved"
                and self.row["id"] == params["request_id"]
                and self.row["payload_hash"] == params["payload_hash"]
            ):
                self.row["status"] = "claimed"
                result.first.return_value = (self.row["id"],)
        elif "SET status = 'executing'" in sql and self.row is not None:
            if self.row["status"] == "claimed":
                self.row["status"] = "executing"
                result.first.return_value = (self.row["id"],)
        return result

    def approve(self, workspace: UUID, user: UUID) -> str:
        """What ``POST /approvals/{id}/decide`` does on approve: a token for the STORED hash."""
        assert self.row is not None and self.row["status"] == "pending"
        token, _jti = mint_approval_token(
            JWT_KEY,
            user_id=user,
            workspace_id=workspace,
            request_id=self.row["id"],
            tool_name=self.row["tool_name"],
            payload_hash=self.row["payload_hash"],
        )
        self.row["status"] = "approved"
        return token


class Flow:
    """One chat-proposed Notion write as a stored task step, run by the real worker."""

    def __init__(self, fake: FakeNotionApi, proposal: WriteProposal) -> None:
        self.fake = fake
        self.workspace, self.user, task_id = uuid4(), uuid4(), uuid4()
        step = write_step(proposal)
        self.subtask = Subtask(
            id=uuid4(),
            task_id=task_id,
            workspace_id=self.workspace,
            assigned_agent=SubtaskAgent.PM,
            description=step.description,
            dependencies=[],
            status=SubtaskStatus.PENDING,
            # what TaskSubmissionService stores for a step (persisted_input_data)
            input_data={"tool": step.tool, "tool_args": json.loads(json.dumps(step.tool_args))},
        )
        self.task = Task(
            id=task_id,
            workspace_id=self.workspace,
            user_id=self.user,
            session_id=str(task_id),
            user_instruction=proposal.summary,
            status="executing",
            plan_status=PlanStatus.EXECUTING,
        )
        self.database = ApprovalDatabase()
        self.gateway = gateway_for(fake, self.user)
        self.worker = SubtaskWorker(jwt_signing_key=JWT_KEY, tool_invoker=self.gateway)

    async def run(self) -> SubtaskStatus:
        session = AsyncMock()
        session.add = MagicMock()
        connection = MagicMock()
        connection.execute = AsyncMock(side_effect=self.database.execute)
        connection.info = {}
        session.connection = AsyncMock(return_value=connection)

        async def execute(statement: Any, *args: Any, **kwargs: Any) -> MagicMock:
            result = MagicMock()
            sql = str(statement)
            if "FROM subtasks" in sql:
                result.scalars.return_value.first.return_value = self.subtask
                result.scalars.return_value.all.return_value = [self.subtask]
            elif "FROM tasks" in sql:
                result.scalars.return_value.first.return_value = self.task
            return result

        session.execute = AsyncMock(side_effect=execute)
        job = SubtaskJob(
            workspace_id=self.workspace,
            user_id=self.user,
            task_id=self.task.id,
            subtask_id=self.subtask.id,
            assigned_agent="pm",
        )
        return await self.worker.execute_job(session=session, job=job)

    def resume_with(self, token: str) -> None:
        """The approvals route on approve: the token goes on the step, which is queued again."""
        self.subtask.input_data = {**self.subtask.input_data, "approval_token": token}
        self.subtask.status = SubtaskStatus.PENDING


@pytest.fixture
async def flow() -> Any:
    fake = FakeNotionApi()
    proposal = await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": BLOCKS})
    value = Flow(fake, proposal)
    yield value
    await value.gateway.close()


async def test_no_write_before_approval_exactly_one_after(flow: Flow) -> None:
    payload = flow.subtask.input_data["tool_args"]

    assert await flow.run() == SubtaskStatus.AWAITING_APPROVAL
    assert flow.fake.writes == []  # ZERO write requests before the approval
    assert flow.task.status == "awaiting_approval"
    row = flow.database.row
    assert (
        row is not None and row["tool_name"] == APPEND_BLOCKS and row["tier"] == "APPROVAL_REQUIRED"
    )
    assert json.loads(row["tool_args"]) == payload  # the approval shows the exact payload

    # running the step again without a decision still writes nothing
    assert await flow.run() == SubtaskStatus.AWAITING_APPROVAL
    assert flow.fake.writes == []

    flow.resume_with(flow.database.approve(flow.workspace, flow.user))
    assert await flow.run() == SubtaskStatus.COMPLETED

    assert len(flow.fake.writes) == 1  # EXACTLY ONE write request after the approval
    assert flow.fake.write_bodies()[0] == payload["requests"][0]["body"]
    output = flow.subtask.output_result or {}
    assert "VERIFIED by reading it back: 8 blocks match" in str(output.get("output"))
    assert flow.database.row is not None and flow.database.row["status"] == "executing"

    # the single-use approval cannot be replayed
    flow.subtask.status = SubtaskStatus.PENDING
    assert await flow.run() == SubtaskStatus.AWAITING_APPROVAL
    assert len(flow.fake.writes) == 1


async def test_a_rejected_request_never_writes(flow: Flow) -> None:
    assert await flow.run() == SubtaskStatus.AWAITING_APPROVAL
    assert flow.database.row is not None
    # The approvals route on reject: the row is rejected, no token exists, the step is failed.
    flow.database.row["status"] = "rejected"
    assert await flow.run() == SubtaskStatus.AWAITING_APPROVAL  # even if it were queued again
    flow.subtask.status = SubtaskStatus.FAILED
    assert await flow.run() == SubtaskStatus.FAILED
    assert flow.fake.writes == []  # NO write request after a rejection


async def test_a_payload_changed_after_approval_is_not_written(flow: Flow) -> None:
    assert await flow.run() == SubtaskStatus.AWAITING_APPROVAL
    token = flow.database.approve(flow.workspace, flow.user)
    changed = json.loads(json.dumps(flow.subtask.input_data["tool_args"]))
    changed["requests"][0]["body"]["children"][1]["paragraph"]["rich_text"][0]["text"][
        "content"
    ] = "We ship today."
    flow.subtask.input_data = {**flow.subtask.input_data, "tool_args": changed}
    flow.resume_with(token)
    assert await flow.run() == SubtaskStatus.AWAITING_APPROVAL  # the hash no longer matches
    assert flow.fake.writes == []


async def test_a_forged_approval_flag_in_the_step_input_is_ignored(flow: Flow) -> None:
    flow.subtask.input_data = {
        **flow.subtask.input_data,
        "has_valid_approval_token": True,
        "approval_token": "not-a-real-token",
    }
    assert await flow.run() == SubtaskStatus.AWAITING_APPROVAL
    assert flow.fake.writes == []


# ----------------------------------------------------------------- chat call and submission


async def test_the_chat_call_submits_one_step_and_reports_waiting_for_approval() -> None:
    fake = FakeNotionApi()
    submitted: list[tuple[str, Any]] = []

    async def submit(summary: str, step: Any) -> TaskSubmission:
        submitted.append((summary, step))
        return TaskSubmission(uuid4(), summary, "executing", True)

    args = {"page_id": PAGE, "blocks": BLOCKS[:2]}
    result = await chat_write_call(fake.toolkit(), APPEND_BLOCKS, args, submit)

    assert result["status"] == "waiting_for_approval" and result["tool"] == APPEND_BLOCKS
    (summary, step) = submitted[0]
    assert step.tool == APPEND_BLOCKS and step.assigned_agent == "pm"
    assert json.loads(result["payload"]) == step.tool_args  # the model sees the exact payload
    assert summary == f"Notion: append 2 blocks to the page {PAGE}"
    assert "Nothing has been written" in result["message"]
    assert fake.writes == []

    # invalid input: nothing is submitted
    bad = await chat_write_call(
        fake.toolkit(), APPEND_BLOCKS, {"page_id": PAGE, "blocks": [{"type": "embed"}]}, submit
    )
    assert bad["status"] == "error" and "not supported" in bad["message"]
    assert len(submitted) == 1

    # the message already has a job: the write is not silently attached to it
    async def existing(summary: str, step: Any) -> TaskSubmission:
        return TaskSubmission(uuid4(), "Earlier job", "executing", False)

    again = await chat_write_call(fake.toolkit(), APPEND_BLOCKS, args, existing)
    assert again["status"] == "error" and "NOT added" in again["message"]

    async def refused(summary: str, step: Any) -> None:
        return None

    none = await chat_write_call(fake.toolkit(), APPEND_BLOCKS, args, refused)
    assert none["status"] == "error" and "could not be created" in none["message"]
    assert fake.writes == []


async def test_submission_with_given_steps_skips_the_planner_and_prechecks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeNotionApi()
    step = write_step(await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "blocks": BLOCKS[:1]}))
    service = TaskSubmissionService(engine=MagicMock(), gateway=None, queue=None)
    seen: dict[str, Any] = {}

    async def fake_submit(**kwargs: Any) -> TaskSubmission:
        seen.update(kwargs)
        return TaskSubmission(uuid4(), "t", "executing", True)

    async def no_plan(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the planner must not run for given steps")

    monkeypatch.setattr(service, "_submit", fake_submit)
    monkeypatch.setattr(service, "_plan", no_plan)
    await service.submit(
        workspace_id=uuid4(),
        user_id=uuid4(),
        idempotency_key="k",
        # words that the request prechecks would otherwise act on
        instruction="Notion: append 1 block to my notion page",
        title="t",
        session_id=None,
        source="chat",
        steps=[step],
    )
    assert seen["steps"] == [step] and "input_files" not in seen


async def test_add_row_summary_names_the_database_when_its_schema_carried_a_title() -> None:
    fake = FakeNotionApi()
    fake.database_titles[DB] = "Tasks"
    named = await propose(fake, ADD_ROW, {"properties": [{"name": "Name", "value": "Java Notes"}]})
    assert "add the row 'Java Notes' to the Tasks database" in named.summary
    assert DB not in named.summary


async def test_add_row_summary_falls_back_to_the_id_without_a_name() -> None:
    fake = FakeNotionApi()
    unnamed = await propose(
        fake, ADD_ROW, {"properties": [{"name": "Name", "value": "Java Notes"}]}
    )
    assert f"to the database {DB}" in unnamed.summary
