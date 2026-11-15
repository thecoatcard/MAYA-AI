"""The Notion tools added for existing content: connector endpoints, read tools, edit tools.

Everything runs on ``tests.support.notion_fake.FakeNotionApi`` (``httpx.MockTransport``) or a small
per-endpoint ``MockTransport``; nothing reaches Notion. Verified against the fake only: the real
Notion API was not called.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.connections.providers import NOTION_VERSION
from app.mcp.connectors.notion import NotionConnector
from app.mcp.limiter import VendorRateLimiter
from app.mcp.notion_edit_defs import EDIT_TOOL_NAMES
from app.mcp.notion_markdown import markdown_to_blocks
from app.mcp.notion_tools import NOTION_TOOL_NAMES, NotionToolkit
from app.mcp.notion_write import (
    ADD_ROW,
    APPEND_BLOCKS,
    CREATE_PAGE,
    NOTION_WRITE_TOOL_NAMES,
    NotionWriteError,
    WriteProposal,
    execute_write,
    propose_write,
    validate_requests,
)
from app.mcp.permissions import DEFAULT_TOOL_PERMISSIONS, ToolTier
from tests.support.notion_fake import DB, PAGE, ROW, FakeNotionApi

PARENT = "11112222333344445555666677778888"


def rich(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": text}}]


def title_prop(text: str) -> dict[str, Any]:
    return {"title": rich(text)}


async def propose(fake: FakeNotionApi, name: str, args: dict[str, Any]) -> WriteProposal:
    return await propose_write(fake.toolkit(), name, args)


async def run(fake: FakeNotionApi, proposal: WriteProposal) -> str:
    """Approve and send exactly the proposed payload (the gateway does this after approval)."""
    return await execute_write(proposal.tool, proposal.tool_args, fake.connector(), approved=True)


async def refused(fake: FakeNotionApi, name: str, args: dict[str, Any]) -> str:
    with pytest.raises(NotionWriteError) as caught:
        await propose(fake, name, args)
    return str(caught.value)


def seeded() -> FakeNotionApi:
    fake = FakeNotionApi()
    fake.database_titles[DB] = "Study Notes"
    fake.database_options[DB] = {"Status": ["Todo", "In progress", "Done"]}
    fake.pages[PAGE]["properties"] = {
        "title": {"type": "title", "title": [{"plain_text": "Java Notes"}]}
    }
    fake.pages[PAGE]["parent"] = {"type": "workspace"}
    return fake


# ------------------------------------------------------------------------------- permissions


def test_new_read_tools_are_automatic_and_new_write_tools_need_approval() -> None:
    for name in ("notion_list_databases", "notion_get_database", "notion_query_database"):
        assert DEFAULT_TOOL_PERMISSIONS[name] is ToolTier.READ
        assert name in NOTION_TOOL_NAMES
    assert DEFAULT_TOOL_PERMISSIONS["notion_find_page"] is ToolTier.READ
    for name in EDIT_TOOL_NAMES:
        assert DEFAULT_TOOL_PERMISSIONS[name] is ToolTier.APPROVAL_REQUIRED
        assert name in NOTION_WRITE_TOOL_NAMES


# --------------------------------------------------------------------------- connector endpoints


def mock_connector(handler: Any) -> NotionConnector:
    limiter = VendorRateLimiter()
    limiter.configure_vendor("notion", 1000.0, 1000.0)

    async def no_sleep(_seconds: float) -> None:
        return None

    return NotionConnector(
        "ntn_" + "z" * 40,
        rate_limiter=limiter,
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
    )


async def test_each_new_endpoint_uses_the_right_method_path_and_version() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": [], "has_more": False})

    connector = mock_connector(handler)
    await connector.search_databases("study", 5, "cur")
    await connector.get_block("a" * 32)
    await connector.query_database_page(DB, {"page_size": 2})
    await connector.update_block("b" * 32, {"paragraph": {"rich_text": []}})
    await connector.delete_block("c" * 32)
    await connector.create_comment({"parent": {"page_id": PAGE}, "rich_text": []})
    await connector.append_block_children(PAGE, {"children": [], "after": "d" * 32})
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/v1/search"),
        ("GET", f"/v1/blocks/{'a' * 32}"),
        ("POST", f"/v1/databases/{DB}/query"),
        ("PATCH", f"/v1/blocks/{'b' * 32}"),
        ("DELETE", f"/v1/blocks/{'c' * 32}"),
        ("POST", "/v1/comments"),
        ("PATCH", f"/v1/blocks/{PAGE}/children"),
    ]
    assert {r.headers["Notion-Version"] for r in seen} == {NOTION_VERSION}
    search = json.loads(seen[0].content)
    assert search["filter"] == {"property": "object", "value": "database"}
    assert search["start_cursor"] == "cur" and search["page_size"] == 5
    assert json.loads(seen[6].content)["after"] == "d" * 32


async def test_a_rate_limited_read_waits_and_retries_once() -> None:
    answers = [
        httpx.Response(429, headers={"retry-after": "1"}, json={"code": "rate_limited"}),
        httpx.Response(200, json={"results": [], "has_more": False}),
    ]
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return answers.pop(0)

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    limiter = VendorRateLimiter()
    limiter.configure_vendor("notion", 1000.0, 1000.0)
    connector = NotionConnector(
        "ntn_" + "z" * 40, rate_limiter=limiter, transport=httpx.MockTransport(handler), sleep=sleep
    )
    data = await connector.search_databases("")
    assert data["results"] == [] and sleeps == [1.0]


async def test_a_persistent_429_becomes_a_plain_sentence_in_the_read_tool() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "1"}, json={"code": "rate_limited"})

    connector = mock_connector(handler)

    async def provider() -> tuple[NotionConnector, str | None]:
        return connector, DB

    out = await NotionToolkit(provider).call("notion_list_databases", {})
    assert out["ok"] is False and "rate limiting" in out["error"]


# ------------------------------------------------------------------------------- read tools


async def test_list_databases_shows_name_id_and_column_types() -> None:
    fake = seeded()
    out = await fake.toolkit().call("notion_list_databases", {})
    assert out["ok"] is True
    assert (
        "Study Notes" in out["result"]
        and DB in out["result"]
        and "Status (status)" in out["result"]
    )
    none = await fake.toolkit().call("notion_list_databases", {"query": "zzz"})
    assert "No database is shared" in none["result"]


async def test_get_database_lists_columns_options_and_read_only_markers() -> None:
    fake = seeded()
    fake.databases[DB]["Related"] = "relation"
    out = await fake.toolkit().call("notion_get_database", {})
    text = out["result"]
    assert "Study Notes" in text and "- Status | status | options: Todo, In progress, Done" in text
    assert "Related | relation | read-only for MAYA" in text
    assert "Name | title" in text


async def test_query_database_maps_filters_sort_and_limit() -> None:
    fake = seeded()
    for n, status in enumerate(["Todo", "Done", "Done"]):
        fake.add_row(
            DB,
            Name=title_prop(f"Row {n}"),
            Status={"status": {"name": status}},
        )
    out = await fake.toolkit().call(
        "notion_query_database",
        {
            "filters": [{"property": "status", "op": "equals", "value": "Done"}],
            "sort_by": "Name",
            "sort_direction": "descending",
            "limit": 1,
        },
    )
    assert out["ok"] is True
    sent = json.loads(fake.requests[-1].content)
    assert sent["filter"] == {"property": "Status", "status": {"equals": "Done"}}
    assert sent["sorts"] == [{"property": "Name", "direction": "descending"}]
    assert sent["page_size"] == 1
    assert "Row 1" in out["result"] and "Row 0" not in out["result"]
    assert "More rows exist than the 1 shown" in out["result"] and "cursor '1'" in out["result"]
    second = await fake.toolkit().call(
        "notion_query_database",
        {
            "filters": [{"property": "Status", "op": "equals", "value": "Done"}],
            "cursor": "1",
            "limit": 5,
        },
    )
    assert "Row 2" in second["result"] and "More rows exist" not in second["result"]


async def test_query_database_limits_and_combinations() -> None:
    fake = seeded()
    await fake.toolkit().call("notion_query_database", {"limit": 500})
    assert json.loads(fake.requests[-1].content)["page_size"] == 50
    any_of = await fake.toolkit().call(
        "notion_query_database",
        {
            "match": "any",
            "filters": [
                {"property": "Name", "op": "contains", "value": "java"},
                {"property": "Done", "op": "equals", "value": "true"},
                {"property": "Due", "op": "on_or_after", "value": "2026-11-01"},
                {"property": "Points", "op": "greater_than", "value": "3"},
                {"property": "Tags", "op": "is_empty"},
                {"property": "Status", "op": "not_equals", "value": "Done"},
            ],
        },
    )
    assert any_of["ok"] is True
    clauses = json.loads(fake.requests[-1].content)["filter"]["or"]
    assert {"property": "Done", "checkbox": {"equals": True}} in clauses
    assert {"property": "Points", "number": {"greater_than": 3.0}} in clauses
    assert {"property": "Tags", "multi_select": {"is_empty": True}} in clauses
    assert {"property": "Status", "status": {"does_not_equal": "Done"}} in clauses


@pytest.mark.parametrize(
    ("filter_", "message"),
    [
        ({"property": "Related", "op": "contains", "value": "x"}, "not supported"),
        ({"property": "Status", "op": "contains", "value": "x"}, "cannot be used"),
        ({"property": "Nope", "op": "equals", "value": "x"}, "no column named"),
        ({"property": "Points", "op": "equals", "value": "abc"}, "not a number"),
        ({"property": "Due", "op": "before", "value": "soon"}, "ISO date"),
        ({"property": "Name", "op": "equals"}, "needs a 'value'"),
        ({"property": "Done", "op": "equals", "value": "maybe"}, "true or false"),
    ],
)
async def test_unsupported_filters_are_refused_with_a_plain_message(
    filter_: dict[str, str], message: str
) -> None:
    fake = seeded()
    fake.databases[DB]["Related"] = "relation"
    out = await fake.toolkit().call("notion_query_database", {"filters": [filter_]})
    assert out["ok"] is False and message in out["error"]
    assert not [r for r in fake.requests if r.url.path.endswith("/query")]


async def test_find_page_returns_title_id_parent_url_and_never_invents() -> None:
    fake = seeded()
    out = await fake.toolkit().call("notion_find_page", {"query": "Java"})
    assert out["ok"] is True
    assert (
        "Java Notes" in out["result"]
        and PAGE in out["result"]
        and "top-level page" in out["result"]
    )
    assert "https://notion.so/plan" in out["result"]
    none = await fake.toolkit().call("notion_find_page", {"query": "no such title"})
    assert "No page" in none["result"] and PAGE not in none["result"]


async def test_get_page_returns_markdown_with_ids_nested_items_and_pagination() -> None:
    fake = seeded()
    blocks = markdown_to_blocks(
        "# Collections\n- a\n  - b\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n```java\nint a;\n```"
    )
    stored = [fake.add_block(PAGE, b) for b in blocks]
    out = await fake.toolkit().call("notion_get_page", {"page_id": PAGE})
    text = out["result"]
    assert stored[0]["id"] in text  # block ids for targeting
    assert "# Collections" in text and "- a" in text and "  - b" in text
    assert "| x | y |" in text and "| 1 | 2 |" in text and "```java" in text
    # more than 100 top-level blocks: the cursor continues the read
    for n in range(130):
        fake.add_block(PAGE, {"type": "paragraph", "paragraph": {"rich_text": rich(f"p{n}")}})
    big = await fake.toolkit().call("notion_get_page", {"page_id": PAGE, "max_chars": 12000})
    assert "p129" in big["result"]  # followed the cursor (up to 300 blocks)


# --------------------------------------------------------------------------- write: create/append


async def test_create_page_from_markdown_shows_converted_blocks_and_verifies() -> None:
    fake = seeded()
    proposal = await propose(
        fake,
        CREATE_PAGE,
        {
            "title": "Generics",
            "parent_page_id": PARENT_PAGE(fake),
            "markdown": "# Generics\n\nUse **bounded** types.\n\n- one\n  - nested\n",
        },
    )
    body = proposal.tool_args["requests"][0]["body"]
    kinds = [b["type"] for b in body["children"]]
    assert kinds == ["heading_1", "paragraph", "bulleted_list_item"]
    assert body["children"][2]["bulleted_list_item"]["children"][0]["type"] == "bulleted_list_item"
    assert "bounded" in proposal.payload_text()  # the approval preview carries the blocks
    assert validate_requests(proposal.tool, proposal.tool_args["requests"])
    text = await run(fake, proposal)
    assert "VERIFIED by reading it back" in text and "DIFFERENCE" not in text


def PARENT_PAGE(fake: FakeNotionApi) -> str:
    fake.pages[PARENT] = {
        "object": "page",
        "id": PARENT,
        "url": "https://notion.so/projects",
        "parent": {"type": "workspace"},
        "properties": {"title": {"type": "title", "title": [{"plain_text": "Projects"}]}},
    }
    fake.children[PARENT] = []
    return PARENT


async def test_markdown_and_blocks_together_are_refused() -> None:
    fake = seeded()
    message = await refused(
        fake,
        APPEND_BLOCKS,
        {"page_id": PAGE, "markdown": "x", "blocks": [{"type": "paragraph", "text": "y"}]},
    )
    assert "not both" in message


async def test_leftover_markdown_in_the_read_back_is_flagged() -> None:
    fake = seeded()
    proposal = await propose(fake, APPEND_BLOCKS, {"page_id": PAGE, "markdown": "plain words"})
    original = fake._store_blocks

    def raw_markers(parent: str, blocks: list[dict[str, Any]]) -> Any:
        stored = original(parent, blocks)
        stored[0]["paragraph"]["rich_text"][0]["plain_text"] = "**plain** words"
        return stored

    fake._store_blocks = raw_markers  # type: ignore[method-assign]
    text = await run(fake, proposal)
    assert "VERIFICATION FOUND" in text and "still shows '**' as raw text" in text


async def test_append_after_a_block_inserts_there_and_names_the_page() -> None:
    fake = seeded()
    first = fake.add_block(PAGE, {"type": "paragraph", "paragraph": {"rich_text": rich("one")}})
    fake.add_block(PAGE, {"type": "paragraph", "paragraph": {"rich_text": rich("three")}})
    proposal = await propose(
        fake,
        APPEND_BLOCKS,
        {"page_id": PAGE, "after_block_id": first["id"], "markdown": "two"},
    )
    assert "'Java Notes'" in proposal.summary and "after the block" in proposal.summary
    assert proposal.tool_args["requests"][0]["body"]["after"] == first["id"]
    text = await run(fake, proposal)
    assert "VERIFIED" in text
    order = [b["paragraph"]["rich_text"][0]["plain_text"] for b in fake.children[PAGE]]
    assert order == ["one", "two", "three"]


async def test_append_after_falls_back_to_the_end_with_a_plain_note() -> None:
    fake = seeded()
    first = fake.add_block(PAGE, {"type": "paragraph", "paragraph": {"rich_text": rich("one")}})
    fake.add_block(PAGE, {"type": "paragraph", "paragraph": {"rich_text": rich("three")}})
    fake.reject_after = True
    proposal = await propose(
        fake, APPEND_BLOCKS, {"page_id": PAGE, "after_block_id": first["id"], "markdown": "two"}
    )
    text = await run(fake, proposal)
    assert "added at the END of the page" in text
    order = [b["paragraph"]["rich_text"][0]["plain_text"] for b in fake.children[PAGE]]
    assert order == ["one", "three", "two"]


# -------------------------------------------------------------------------- duplicate protection


async def test_a_page_with_the_same_title_is_not_created_twice() -> None:
    fake = seeded()
    parent = PARENT_PAGE(fake)
    fake.add_block(parent, {"type": "child_page", "child_page": {"title": "Java Notes"}})
    fake.children[parent][0]["child_page"] = {"title": "Java Notes"}
    message = await refused(fake, CREATE_PAGE, {"title": "java notes", "parent_page_id": parent})
    assert "already exists" in message and "notion_append_blocks" in message
    again = await propose(
        fake,
        CREATE_PAGE,
        {"title": "java notes", "parent_page_id": parent, "allow_duplicate": True},
    )
    assert again.tool == CREATE_PAGE


async def test_a_row_with_the_same_title_is_not_added_twice() -> None:
    fake = seeded()
    fake.add_row(DB, Name=title_prop("Java Notes"), Status={"status": {"name": "Todo"}})
    message = await refused(fake, ADD_ROW, {"title": "Java Notes", "properties": []})
    assert "already exists in that database" in message
    other = await propose(fake, ADD_ROW, {"title": "Python Notes", "properties": []})
    assert "Study Notes" in other.summary and "Python Notes" in other.summary


# ------------------------------------------------------------------------------ edit tools


async def test_update_page_changes_a_row_typed_from_the_schema_and_verifies() -> None:
    fake = seeded()
    proposal = await propose(
        fake,
        "notion_update_page",
        {
            "page_id": ROW,
            "title": "New title",
            "icon": "\U0001f4d8",
            "properties": [{"name": "Status", "value": "Done"}, {"name": "Done", "value": "true"}],
        },
    )
    body = proposal.tool_args["requests"][0]["body"]
    assert body["properties"]["Status"] == {"status": {"name": "Done"}}
    assert body["properties"]["Name"]["title"][0]["text"]["content"] == "New title"
    assert body["icon"] == {"type": "emoji", "emoji": "\U0001f4d8"}
    assert "Old title" in proposal.summary
    text = await run(fake, proposal)
    assert "VERIFIED" in text and "icon" in text
    assert fake.pages[ROW]["properties"]["Status"]["status"] == {"name": "Done"}


async def test_update_page_rejects_read_only_and_untyped_columns() -> None:
    fake = seeded()
    fake.databases[DB]["Related"] = "relation"
    fake.databases[DB]["Total"] = "rollup"
    for column in ("Related", "Total"):
        message = await refused(
            fake,
            "notion_update_page",
            {"page_id": ROW, "properties": [{"name": column, "value": "x"}]},
        )
        assert "read-only for MAYA" in message
    people = await propose(
        fake,
        "notion_update_page",
        {
            "page_id": ROW,
            "properties": [{"name": "Owner", "value": "9f1c2d3e-0000-4000-8000-000000000001"}],
        },
    )
    assert (
        people.tool_args["requests"][0]["body"]["properties"]["Owner"]["people"][0]["object"]
        == "user"
    )
    by_name = await refused(
        fake,
        "notion_update_page",
        {"page_id": ROW, "properties": [{"name": "Owner", "value": "Asha"}]},
    )
    assert "user ids" in by_name
    plain_page = await refused(
        fake,
        "notion_update_page",
        {"page_id": PAGE, "properties": [{"name": "Status", "value": "x"}]},
    )
    assert "not a row of a database" in plain_page


async def test_archive_page_is_labelled_destructive_and_verified() -> None:
    fake = seeded()
    proposal = await propose(fake, "notion_archive_page", {"page_id": PAGE})
    assert (
        "ARCHIVE" in proposal.summary
        and "trash" in proposal.summary
        and "Java Notes" in proposal.summary
    )
    assert proposal.tool_args["requests"][0]["body"] == {"archived": True}
    text = await run(fake, proposal)
    assert "Archived" in text and "VERIFIED" in text and fake.pages[PAGE]["archived"] is True
    again = await refused(fake, "notion_archive_page", {"page_id": PAGE})
    assert "already archived" in again


async def test_update_and_delete_a_block_show_the_old_text_and_verify() -> None:
    fake = seeded()
    block = fake.add_block(
        PAGE, {"type": "paragraph", "paragraph": {"rich_text": rich("old text")}}
    )
    todo = fake.add_block(
        PAGE, {"type": "to_do", "to_do": {"rich_text": rich("task"), "checked": False}}
    )
    update = await propose(
        fake, "notion_update_block", {"block_id": block["id"], "text": "new **bold** text"}
    )
    assert "old text" in update.summary and "new **bold** text" in update.summary
    assert update.tool_args["requests"][0]["note"] == "Was: old text"
    assert "VERIFIED" in await run(fake, update)
    assert fake.blocks[block["id"]]["paragraph"]["rich_text"][1]["plain_text"] == "bold"
    checked = await propose(
        fake, "notion_update_block", {"block_id": todo["id"], "text": "task", "checked": True}
    )
    assert (
        "VERIFIED" in await run(fake, checked)
        and fake.blocks[todo["id"]]["to_do"]["checked"] is True
    )

    delete = await propose(fake, "notion_delete_block", {"block_id": block["id"]})
    assert "DELETE" in delete.summary and "trash" in delete.summary
    assert "VERIFIED" in await run(fake, delete)
    assert block["id"] not in [b["id"] for b in fake.children[PAGE]]
    sub = fake.add_block(PAGE, {"type": "child_page", "child_page": {"title": "Sub"}})
    assert "notion_archive_page" in await refused(
        fake, "notion_delete_block", {"block_id": sub["id"]}
    )
    divider = fake.add_block(PAGE, {"type": "divider", "divider": {}})
    assert "no text to change" in await refused(
        fake, "notion_update_block", {"block_id": divider["id"], "text": "x"}
    )


async def test_comments_are_added_and_a_missing_capability_is_a_plain_message() -> None:
    fake = seeded()
    proposal = await propose(fake, "notion_add_comment", {"page_id": PAGE, "text": "Looks good"})
    assert "Java Notes" in proposal.summary
    text = await run(fake, proposal)
    assert "VERIFIED" in text and fake.comments[0]["rich_text"][0]["plain_text"] == "Looks good"
    fake.comments_forbidden = True
    blocked = await propose(fake, "notion_add_comment", {"page_id": PAGE, "text": "again"})
    with pytest.raises(NotionWriteError) as caught:
        await run(fake, blocked)
    assert "Insert comments capability" in str(caught.value)


# ------------------------------------------------------------------------------- replace section


def page_with_sections(fake: FakeNotionApi) -> dict[str, str]:
    ids: dict[str, str] = {}
    for key, block in [
        ("intro", {"type": "paragraph", "paragraph": {"rich_text": rich("Intro")}}),
        ("h_lists", {"type": "heading_2", "heading_2": {"rich_text": rich("Collections")}}),
        (
            "a",
            {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich("ArrayList")}},
        ),
        ("b", {"type": "paragraph", "paragraph": {"rich_text": rich("Old paragraph")}}),
        ("h_next", {"type": "heading_2", "heading_2": {"rich_text": rich("Streams")}}),
        ("c", {"type": "paragraph", "paragraph": {"rich_text": rich("Stream text")}}),
    ]:
        ids[key] = fake.add_block(PAGE, block)["id"]
    return ids


async def test_replace_section_lists_removed_text_inserts_after_the_heading_and_verifies() -> None:
    fake = seeded()
    ids = page_with_sections(fake)
    proposal = await propose(
        fake,
        "notion_replace_section",
        {
            "page_id": PAGE,
            "heading": "collections",
            "markdown": "- **List**: ordered\n- **Set**: unique",
        },
    )
    assert "Removes 2 blocks" in proposal.summary
    assert "ArrayList" in proposal.summary and "Old paragraph" in proposal.summary
    assert "Stream text" not in proposal.summary and "Streams" not in proposal.summary
    assert "Inserts 2 blocks" in proposal.summary
    requests = proposal.tool_args["requests"]
    assert requests[0]["body"]["after"] == ids["h_lists"]
    assert [r["method"] for r in requests] == ["PATCH", "DELETE", "DELETE"]
    assert validate_requests(proposal.tool, requests)
    text = await run(fake, proposal)
    assert "Replaced a section" in text and "VERIFIED" in text
    remaining = [
        (b["type"], (b[b["type"]].get("rich_text") or [{"plain_text": ""}])[0]["plain_text"])
        for b in fake.children[PAGE]
    ]
    assert remaining == [
        ("paragraph", "Intro"),
        ("heading_2", "Collections"),
        ("bulleted_list_item", "List"),
        ("bulleted_list_item", "Set"),
        ("heading_2", "Streams"),
        ("paragraph", "Stream text"),
    ]


async def test_replace_section_refuses_unclear_or_empty_targets_and_never_clears_a_page() -> None:
    fake = seeded()
    page_with_sections(fake)
    fake.add_block(PAGE, {"type": "heading_2", "heading_2": {"rich_text": rich("Collections 2")}})
    exact = await propose(
        fake, "notion_replace_section", {"page_id": PAGE, "heading": "Collections", "markdown": "x"}
    )
    assert "'Collections'" in exact.summary  # an exact match wins over the partial 'Collections 2'
    fake.add_block(PAGE, {"type": "heading_3", "heading_3": {"rich_text": rich("Dup")}})
    fake.add_block(PAGE, {"type": "heading_3", "heading_3": {"rich_text": rich("Dup")}})
    ambiguous = await refused(
        fake, "notion_replace_section", {"page_id": PAGE, "heading": "Dup", "markdown": "x"}
    )
    assert "Several headings match" in ambiguous and "start_block_id" in ambiguous
    missing = await refused(
        fake, "notion_replace_section", {"page_id": PAGE, "heading": "Nope", "markdown": "x"}
    )
    assert "No heading matches" in missing and "Collections" in missing
    no_target = await refused(fake, "notion_replace_section", {"page_id": PAGE, "markdown": "x"})
    assert "Name the section" in no_target
    empty = await refused(
        fake, "notion_replace_section", {"page_id": PAGE, "heading": "Streams", "markdown": "  "}
    )
    assert "content is empty" in empty
    for n in range(60):
        fake.add_block(PAGE, {"type": "paragraph", "paragraph": {"rich_text": rich(f"filler {n}")}})
    too_big = await refused(
        fake,
        "notion_replace_section",
        {"page_id": PAGE, "heading": "Collections 2", "markdown": "x"},
    )
    assert "removes at most 50" in too_big


async def test_replace_section_stops_before_deleting_when_the_insert_is_refused() -> None:
    fake = seeded()
    page_with_sections(fake)
    proposal = await propose(
        fake,
        "notion_replace_section",
        {"page_id": PAGE, "heading": "Collections", "markdown": "new"},
    )
    fake.reject_after = True
    with pytest.raises(NotionWriteError) as caught:
        await run(fake, proposal)
    assert "Nothing was changed" in str(caught.value)
    assert len(fake.children[PAGE]) == 6  # nothing was removed


# ----------------------------------------------------------------------------- the approval gate


def test_edit_payloads_that_were_changed_after_approval_are_refused() -> None:
    page = "a" * 32
    block = "b" * 32
    bad: list[tuple[str, list[dict[str, Any]]]] = [
        (
            "notion_archive_page",
            [{"method": "PATCH", "path": f"/pages/{page}", "body": {"archived": False}}],
        ),
        ("notion_archive_page", [{"method": "DELETE", "path": f"/pages/{page}", "body": {}}]),
        (
            "notion_delete_block",
            [{"method": "DELETE", "path": f"/blocks/{block}", "body": {"x": 1}}],
        ),
        ("notion_delete_block", [{"method": "DELETE", "path": "/pages/" + page, "body": {}}]),
        (
            "notion_update_page",
            [{"method": "PATCH", "path": f"/pages/{page}", "body": {"archived": True}}],
        ),
        (
            "notion_update_block",
            [{"method": "PATCH", "path": f"/blocks/{block}", "body": {"image": {"rich_text": []}}}],
        ),
        (
            "notion_add_comment",
            [
                {
                    "method": "POST",
                    "path": "/comments",
                    "body": {"parent": {"database_id": page}, "rich_text": [{}]},
                }
            ],
        ),
        (
            "notion_replace_section",
            [{"method": "DELETE", "path": f"/blocks/{block}", "body": {}}],
        ),
        (
            "notion_delete_block",
            [{"method": "DELETE", "path": f"/blocks/{block}", "body": {}, "extra": 1}],
        ),
    ]
    for tool, requests in bad:
        with pytest.raises(NotionWriteError):
            validate_requests(tool, requests)
    good = [{"method": "DELETE", "path": f"/blocks/{block}", "body": {}, "note": "Removes: x"}]
    assert validate_requests("notion_delete_block", good)


async def test_nothing_is_sent_without_the_approved_flag() -> None:
    fake = seeded()
    proposal = await propose(fake, "notion_archive_page", {"page_id": PAGE})
    with pytest.raises(NotionWriteError):
        await execute_write(proposal.tool, proposal.tool_args, fake.connector(), approved=False)
    assert fake.writes == []
