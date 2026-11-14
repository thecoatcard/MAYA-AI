"""Notion writes that change EXISTING content: propose, validate, execute, verify.

Tools (all APPROVAL_REQUIRED, declared in ``notion_edit_defs``): ``notion_update_page``,
``notion_archive_page``, ``notion_update_block``, ``notion_delete_block``, ``notion_add_comment``
and ``notion_replace_section``.

Same shape as the other Notion writes (``notion_write``): :func:`propose_edit` reads what it needs
and builds the EXACT requests (never writes); :func:`validate_edit` re-checks an approved payload
against what the tool may send; :func:`execute_edit` sends it once and reads the result back.
Deletes are Notion's trash (archive): nothing is destroyed for good, and the approval text says
which blocks go.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

from app.mcp.connectors.notion import NotionCallError, NotionConnector
from app.mcp.notion_edit_defs import (
    ADD_COMMENT,
    ARCHIVE_PAGE,
    DELETE_BLOCK,
    EDIT_TOOL_NAMES,
    REPLACE_SECTION,
    UPDATE_BLOCK,
    UPDATE_PAGE,
)
from app.mcp.notion_markdown import (
    MarkdownError,
    leftover_markdown,
    markdown_to_blocks,
    plain_rich_text,
    rich_text_from_markdown,
)
from app.mcp.notion_tools import (
    NotionToolkit,
    _read_top_blocks,
    _row_summary,
    block_text,
)
from app.mcp.notion_write import (
    MAX_BLOCKS_PER_REQUEST,
    NotionWriteError,
    WriteProposal,
    _children,
    _clean_id,
    _count,
    _dict,
    _plain,
    _property_items,
    _report,
    _short,
    _title_column,
    build_properties,
    check_children,
    compare_blocks,
    compare_properties,
    failure_message,
    ids_of,
    property_value,
    rich_text,
)

logger = logging.getLogger(__name__)

MAX_SECTION_BLOCKS: Final = 50
MAX_COMMENT_CHARS: Final = 2000
EDITABLE_TEXT_BLOCKS: Final = frozenset(
    "paragraph heading_1 heading_2 heading_3 bulleted_list_item numbered_list_item to_do quote "
    "callout toggle code".split()
)
_HEX32: Final = r"[0-9a-f]{32}"
_PAGE_PATH: Final = re.compile(rf"^/pages/({_HEX32})$")
_BLOCK_PATH: Final = re.compile(rf"^/blocks/({_HEX32})$")
_CHILDREN_PATH: Final = re.compile(rf"^/blocks/({_HEX32})/children$")
_SUMMARY_LIMIT: Final = 1800


def _norm(raw: Any) -> str:
    return str(raw or "").replace("-", "").lower()


def _req(method: str, path: str, body: dict[str, Any], note: str = "") -> dict[str, Any]:
    request: dict[str, Any] = {"method": method, "path": path, "body": body}
    if note:
        request["note"] = " ".join(note.split())[:200]
    return request


def _page_title(page: dict[str, Any]) -> str:
    title, _status, _due = _row_summary(page)
    return title


def _is_archived(obj: dict[str, Any]) -> bool:
    return bool(obj.get("archived") or obj.get("in_trash"))


# ---------------------------------------------------------------------------------- proposing


async def propose_edit(
    toolkit: NotionToolkit, connector: NotionConnector, name: str, args: dict[str, Any]
) -> WriteProposal:
    """Build the exact requests for one edit tool. READS only. Raises NotionWriteError."""
    if name == UPDATE_PAGE:
        return await _propose_update_page(toolkit, connector, args)
    if name == ARCHIVE_PAGE:
        return await _propose_archive(connector, args)
    if name == UPDATE_BLOCK:
        return await _propose_update_block(connector, args)
    if name == DELETE_BLOCK:
        return await _propose_delete_block(connector, args)
    if name == ADD_COMMENT:
        return await _propose_comment(connector, args)
    if name == REPLACE_SECTION:
        return await _propose_replace_section(connector, args)
    raise NotionWriteError(f"Unknown tool '{name}'.")


def _emoji(raw: str) -> str:
    icon = raw.strip()
    if not icon or len(icon) > 8 or icon.isascii():
        raise NotionWriteError("'icon' must be a single emoji, for example 📘.")
    return icon


async def _propose_update_page(
    toolkit: NotionToolkit, connector: NotionConnector, args: dict[str, Any]
) -> WriteProposal:
    page_id = _clean_id(args.get("page_id"), "page")
    page = await connector.get_page_checked(page_id)
    if _is_archived(page):
        raise NotionWriteError("That page is archived (in the trash), so it cannot be changed.")
    title = " ".join(str(args.get("title") or "").split())
    icon = str(args.get("icon") or "").strip()
    items = _property_items(args.get("properties"))
    database_id = str(_dict(page.get("parent")).get("database_id") or "").replace("-", "")
    properties: dict[str, Any] = {}
    if database_id:
        try:
            schema = await toolkit.schema(connector, database_id)
        except NotionCallError as error:
            raise NotionWriteError(failure_message(error, target="database")) from None
        properties = build_properties(schema, args.get("properties"))
        if title:
            column = _title_column(schema)
            if column in properties:
                raise NotionWriteError(f"Give the title once: as 'title' or as column '{column}'.")
            properties[column] = property_value("title", title, column)
    else:
        if items:
            raise NotionWriteError(
                "That page is not a row of a database, so it has no columns. Change its "
                "title or icon, or edit its blocks."
            )
        if title:
            properties["title"] = {"title": rich_text(title, what="The title")}
    body: dict[str, Any] = {}
    if properties:
        body["properties"] = properties
    if icon:
        body["icon"] = {"type": "emoji", "emoji": _emoji(icon)}
    if not body:
        raise NotionWriteError("Give a title, an icon or at least one column to change.")
    what = []
    if title:
        what.append(f"title to '{title[:60]}'")
    cols = [n for n in properties if not title or n != _title_column_name(properties, title)]
    if cols and database_id:
        what.append(f"{_count(len(cols), 'column')} ({', '.join(cols)[:80]})")
    if icon:
        what.append(f"icon to {icon}")
    summary = f"Notion: update the page '{_page_title(page)[:80]}': " + "; ".join(what)
    return WriteProposal(
        UPDATE_PAGE, {"requests": [_req("PATCH", f"/pages/{page_id}", body)]}, summary[:300]
    )


def _title_column_name(properties: dict[str, Any], title: str) -> str:
    for name, payload in properties.items():
        if "title" in payload and _plain(payload["title"]) == title:
            return name
    return ""


async def _propose_archive(connector: NotionConnector, args: dict[str, Any]) -> WriteProposal:
    page_id = _clean_id(args.get("page_id"), "page")
    page = await connector.get_page_checked(page_id)
    if _is_archived(page):
        raise NotionWriteError("That page is already archived (in the trash).")
    title = _page_title(page)
    summary = (
        f"Notion: ARCHIVE the page '{title[:80]}' (move it to the trash). This removes it from "
        "the workspace; it can be restored from Notion's trash."
    )
    request = _req(
        "PATCH", f"/pages/{page_id}", {"archived": True}, f"Moves the page '{title}' to the trash"
    )
    return WriteProposal(ARCHIVE_PAGE, {"requests": [request]}, summary[:300])


async def _propose_update_block(connector: NotionConnector, args: dict[str, Any]) -> WriteProposal:
    block_id = _clean_id(args.get("block_id"), "block")
    block = await connector.get_block(block_id)
    kind = str(block.get("type") or "")
    if kind not in EDITABLE_TEXT_BLOCKS:
        raise NotionWriteError(
            f"A {kind or 'this'} block has no text to change here. Editable: "
            f"{', '.join(sorted(EDITABLE_TEXT_BLOCKS))}."
        )
    text = str(args.get("text") if args.get("text") is not None else "")
    if not text.strip():
        raise NotionWriteError("Give the new text. To remove a block use notion_delete_block.")
    items = plain_rich_text(text) if kind == "code" else rich_text_from_markdown(text)
    if len(items) > 100:
        raise NotionWriteError("That text is too long for one block. Split it into several.")
    inner: dict[str, Any] = {"rich_text": items}
    if kind == "to_do" and args.get("checked") is not None:
        inner["checked"] = bool(args.get("checked"))
    old = (block_text(block) or "").strip()
    summary = f"Notion: change the {kind} block from '{_short(old)}' to '{_short(text.strip())}'"
    request = _req("PATCH", f"/blocks/{block_id}", {kind: inner}, f"Was: {old}")
    return WriteProposal(UPDATE_BLOCK, {"requests": [request]}, summary[:300])


async def _propose_delete_block(connector: NotionConnector, args: dict[str, Any]) -> WriteProposal:
    block_id = _clean_id(args.get("block_id"), "block")
    block = await connector.get_block(block_id)
    kind = str(block.get("type") or "")
    if kind in ("child_page", "child_database"):
        raise NotionWriteError(
            "That block is a sub-page or sub-database. Use notion_archive_page for a page."
        )
    text = (block_text(block) or "").strip()
    nested = " (and the blocks nested inside it)" if block.get("has_children") else ""
    summary = f"Notion: DELETE the {kind} block '{_short(text)}'{nested} (moves it to the trash)"
    request = _req("DELETE", f"/blocks/{block_id}", {}, f"Removes: {text}")
    return WriteProposal(DELETE_BLOCK, {"requests": [request]}, summary[:300])


async def _propose_comment(connector: NotionConnector, args: dict[str, Any]) -> WriteProposal:
    page_id = _clean_id(args.get("page_id"), "page")
    text = str(args.get("text") or "").strip()
    if not text:
        raise NotionWriteError("Give the comment text.")
    if len(text) > MAX_COMMENT_CHARS:
        raise NotionWriteError(f"A comment can have at most {MAX_COMMENT_CHARS} characters.")
    page = await connector.get_page_checked(page_id)
    body = {"parent": {"page_id": page_id}, "rich_text": plain_rich_text(text)}
    summary = f"Notion: add a comment to the page '{_page_title(page)[:80]}': '{_short(text)}'"
    return WriteProposal(
        ADD_COMMENT, {"requests": [_req("POST", "/comments", body)]}, summary[:300]
    )


def _heading_text(block: dict[str, Any]) -> str:
    kind = str(block.get("type") or "")
    return _plain(_dict(block.get(kind)).get("rich_text"))


async def _propose_replace_section(
    connector: NotionConnector, args: dict[str, Any]
) -> WriteProposal:
    page_id = _clean_id(args.get("page_id"), "page")
    try:
        new_blocks = markdown_to_blocks(str(args.get("markdown") or ""))
    except MarkdownError as error:
        raise NotionWriteError(str(error)) from None
    if not new_blocks:
        raise NotionWriteError(
            "The new content is empty. To remove blocks use notion_delete_block."
        )
    if len(new_blocks) > MAX_BLOCKS_PER_REQUEST:
        raise NotionWriteError(
            f"The new content is {len(new_blocks)} blocks; one replacement carries at most "
            f"{MAX_BLOCKS_PER_REQUEST}. Replace in smaller parts."
        )
    page = await connector.get_page_checked(page_id)
    blocks, _cursor, more = await _read_top_blocks(connector, page_id, None)
    start = _find_heading(blocks, args)
    level = int(str(start["type"])[-1])
    index = blocks.index(start)
    section: list[dict[str, Any]] = []
    for block in blocks[index + 1 :]:
        kind = str(block.get("type") or "")
        if kind.startswith("heading_") and int(kind[-1]) <= level:
            break
        section.append(block)
    else:
        if more:
            raise NotionWriteError(
                "The page is longer than MAYA reads in one go, so the end of that section is "
                "unknown. Nothing was prepared."
            )
    if not section:
        raise NotionWriteError(
            "That section has no content yet. Add content with notion_append_blocks and "
            "after_block_id set to the heading's id."
        )
    if len(section) > MAX_SECTION_BLOCKS:
        raise NotionWriteError(
            f"That section has {len(section)} blocks; one replacement removes at most "
            f"{MAX_SECTION_BLOCKS}. Replace part of it with notion_update_block or "
            "notion_delete_block."
        )
    check_children(new_blocks)
    start_id = _norm(start.get("id"))
    requests = [
        _req(
            "PATCH",
            f"/blocks/{page_id}/children",
            {"children": new_blocks, "after": start_id},
            "Inserts the new content right after the heading",
        )
    ]
    removed = []
    for block in section:
        text = (block_text(block) or "").replace("\n", " ").strip()
        kind = str(block.get("type") or "")
        nested = " (+ nested blocks)" if block.get("has_children") else ""
        removed.append(f"- {kind}: {_short(text) or '(no text)'}{nested}")
        requests.append(
            _req("DELETE", f"/blocks/{_norm(block.get('id'))}", {}, f"Removes {kind}: {text}")
        )
    inserted = [
        f"- {b['type']}: {_short(block_text(b) or '')}" for b in new_blocks[:10] if "type" in b
    ]
    summary = (
        f"Notion: replace the section '{_heading_text(start)[:60]}' of the page "
        f"'{_page_title(page)[:60]}'.\nRemoves {_count(len(section), 'block')} (to the trash):\n"
        + "\n".join(removed[:15])
        + (f"\n- ... and {len(removed) - 15} more" if len(removed) > 15 else "")
        + f"\nInserts {_count(len(new_blocks), 'block')}:\n"
        + "\n".join(inserted)
        + (f"\n- ... and {len(new_blocks) - 10} more" if len(new_blocks) > 10 else "")
    )
    return WriteProposal(REPLACE_SECTION, {"requests": requests}, summary[:_SUMMARY_LIMIT])


def _find_heading(blocks: list[dict[str, Any]], args: dict[str, Any]) -> dict[str, Any]:
    headings = [b for b in blocks if str(b.get("type") or "").startswith("heading_")]
    wanted_id = _norm(args.get("start_block_id"))
    if wanted_id:
        found = [b for b in headings if _norm(b.get("id")) == wanted_id]
        if not found:
            raise NotionWriteError(
                "start_block_id is not a heading of that page. Read the page with "
                "notion_get_page and use a heading block id."
            )
        return found[0]
    text = " ".join(str(args.get("heading") or "").split()).casefold()
    if not text:
        raise NotionWriteError("Name the section: 'heading' (its text) or 'start_block_id'.")
    exact = [b for b in headings if _heading_text(b).strip().casefold() == text]
    partial = [b for b in headings if text in _heading_text(b).casefold()]
    matches = exact or partial
    if len(matches) == 1:
        return matches[0]
    available = "; ".join(f"'{_heading_text(b)[:40]}' (id {_norm(b.get('id'))})" for b in headings)
    if not matches:
        raise NotionWriteError(
            f"No heading matches '{text[:60]}'. Headings on the page: {available[:600] or 'none'}."
        )
    names = "; ".join(f"'{_heading_text(b)[:40]}' (id {_norm(b.get('id'))})" for b in matches)
    raise NotionWriteError(
        f"Several headings match '{text[:60]}': {names[:600]}. Use start_block_id."
    )


# --------------------------------------------------------------------------------- validating


def validate_edit(tool: str, requests: Any) -> list[dict[str, Any]]:
    """The approved request list of an edit tool, checked against what that tool may send."""
    bad = NotionWriteError("The approved payload is not a valid Notion write. Nothing written.")
    if tool not in EDIT_TOOL_NAMES or not isinstance(requests, list) or not requests:
        raise bad
    most = 1 + MAX_SECTION_BLOCKS if tool == REPLACE_SECTION else 1
    if len(requests) > most:
        raise bad
    for number, request in enumerate(requests):
        if not isinstance(request, dict) or not {"method", "path", "body"} <= set(request):
            raise bad
        if set(request) - {"method", "path", "body", "note"}:
            raise bad
        method, path, body = request["method"], str(request["path"]), request["body"]
        if not isinstance(body, dict):
            raise bad
        if tool == UPDATE_PAGE:
            _valid_update_page(method, path, body, bad)
        elif tool == ARCHIVE_PAGE:
            if method != "PATCH" or not _PAGE_PATH.match(path) or body != {"archived": True}:
                raise bad
        elif tool == UPDATE_BLOCK:
            _valid_update_block(method, path, body, bad)
        elif tool == DELETE_BLOCK:
            if method != "DELETE" or not _BLOCK_PATH.match(path) or body:
                raise bad
        elif tool == ADD_COMMENT:
            parent = body.get("parent")
            rich = body.get("rich_text")
            if (
                method != "POST"
                or path != "/comments"
                or set(body) != {"parent", "rich_text"}
                or not isinstance(parent, dict)
                or set(parent) != {"page_id"}
                or re.fullmatch(_HEX32, str(parent["page_id"])) is None
                or not isinstance(rich, list)
                or not rich
            ):
                raise bad
        elif number == 0:
            after = body.get("after")
            if (
                method != "PATCH"
                or not _CHILDREN_PATH.match(path)
                or set(body) != {"children", "after"}
                or re.fullmatch(_HEX32, str(after)) is None
            ):
                raise bad
            check_children(body["children"])
        elif method != "DELETE" or not _BLOCK_PATH.match(path) or body:
            raise bad
    return requests


def _valid_update_page(method: str, path: str, body: dict[str, Any], bad: Exception) -> None:
    if (
        method != "PATCH"
        or not _PAGE_PATH.match(path)
        or not body
        or set(body) - {"properties", "icon"}
    ):
        raise bad
    if "properties" in body and (
        not isinstance(body["properties"], dict) or not body["properties"]
    ):
        raise bad
    icon = body.get("icon")
    if "icon" in body and (
        not isinstance(icon, dict) or set(icon) != {"type", "emoji"} or icon["type"] != "emoji"
    ):
        raise bad


def _valid_update_block(method: str, path: str, body: dict[str, Any], bad: Exception) -> None:
    if method != "PATCH" or not _BLOCK_PATH.match(path) or len(body) != 1:
        raise bad
    kind, inner = next(iter(body.items()))
    if kind not in EDITABLE_TEXT_BLOCKS or not isinstance(inner, dict):
        raise bad
    if "rich_text" not in inner or set(inner) - {"rich_text", "checked"}:
        raise bad
    if not isinstance(inner["rich_text"], list) or len(inner["rich_text"]) > 100:
        raise bad


# ----------------------------------------------------------------------------------- executing


def _id_of(path: str) -> str:
    return path.strip("/").split("/")[1]


async def _send(connector: NotionConnector, request: dict[str, Any]) -> dict[str, Any]:
    method, path, body = request["method"], str(request["path"]), request["body"]
    if path == "/comments":
        return await connector.create_comment(body)
    if path.endswith("/children"):
        return await connector.append_block_children(_id_of(path), body)
    if path.startswith("/pages/"):
        return await connector.update_page(_id_of(path), body)
    if method == "DELETE":
        return await connector.delete_block(_id_of(path))
    return await connector.update_block(_id_of(path), body)


async def execute_edit(
    tool: str, requests: list[dict[str, Any]], connector: NotionConnector
) -> str:
    """Send an APPROVED, validated edit and return the result text with the read-back check."""
    responses: list[dict[str, Any]] = []
    for number, request in enumerate(requests, start=1):
        try:
            responses.append(await _send(connector, request))
        except Exception as error:  # noqa: BLE001 - mapped to one plain sentence
            message = failure_message(error, target="comment" if tool == ADD_COMMENT else "page")
            if isinstance(error, NotionCallError) and "after" in request["body"]:
                if error.response.status_code == 400:
                    message = (
                        "Notion did not accept inserting after that heading (HTTP 400). Nothing "
                        "was changed."
                    )
            if number > 1:
                message = (
                    f"Only {number - 1} of {len(requests)} requests were applied; request "
                    f"{number} failed: {message} The section may be duplicated: check the page "
                    "in Notion."
                )
            raise NotionWriteError(message) from None
    return await _verify_edit(tool, requests, responses, connector)


async def _verify_edit(
    tool: str,
    requests: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    connector: NotionConnector,
) -> str:
    """What was done, then the read-back comparison (never raises: the write has happened)."""
    first, body = responses[0], requests[0]["body"]
    path = str(requests[0]["path"])
    checked: list[str] = []
    diffs: list[str] = []
    unverified: str | None = None
    try:
        if tool == UPDATE_PAGE:
            done = f"Updated the Notion page {_id_of(path)}. Link: {first.get('url', '')}"
            page = await connector.get_page_checked(_id_of(path))
            diffs = compare_properties(body.get("properties", {}), page)
            checked.append(_count(len(body.get("properties", {})), "property value"))
            if "icon" in body:
                got = _dict(page.get("icon")).get("emoji")
                if got != body["icon"]["emoji"]:
                    diffs.append(f"icon: sent '{body['icon']['emoji']}', Notion has '{got}'")
                checked.append("the icon")
        elif tool == ARCHIVE_PAGE:
            done = f"Archived (moved to the trash) the Notion page {_id_of(path)}."
            page = await connector.get_page_checked(_id_of(path))
            if not _is_archived(page):
                diffs.append("Notion still shows the page as not archived")
            checked.append("the archived state")
        elif tool == UPDATE_BLOCK:
            done = f"Changed the text of the Notion block {_id_of(path)}."
            block = await connector.get_block(_id_of(path))
            kind = next(iter(body))
            diffs = compare_blocks([{"type": kind, kind: body[kind]}], [block])
            diffs += leftover_markdown([block]) if kind != "code" else []
            checked.append("the block text")
        elif tool == DELETE_BLOCK:
            done = f"Deleted (moved to the trash) the Notion block {_id_of(path)}."
            try:
                block = await connector.get_block(_id_of(path))
                if not _is_archived(block):
                    diffs.append("Notion still shows the block as present")
            except NotionCallError as error:
                if error.response.status_code != 404:
                    raise
            checked.append("the block is gone")
        elif tool == ADD_COMMENT:
            done = f"Added the comment {first.get('id', '')} to the Notion page."
            sent = _plain(body["rich_text"])
            if _plain(first.get("rich_text")) != sent:
                diffs.append("the comment text Notion returned differs from what was sent")
            checked.append("the comment text (as returned by Notion)")
        else:
            done, diffs, checked = await _verify_replace(requests, responses, connector)
    except Exception as error:  # noqa: BLE001 - the write is done; only the check failed
        unverified = failure_message(error)
    return _report(done, checked, diffs, unverified)


async def _verify_replace(
    requests: list[dict[str, Any]], responses: list[dict[str, Any]], connector: NotionConnector
) -> tuple[str, list[str], list[str]]:
    page_id = _id_of(str(requests[0]["path"]))
    sent = requests[0]["body"]["children"]
    removed = [_id_of(str(r["path"])) for r in requests[1:]]
    done = (
        f"Replaced a section of the Notion page {page_id}: inserted {_count(len(sent), 'block')} "
        f"and removed {_count(len(removed), 'block')}."
    )
    existing = await _children(connector, page_id)
    if existing is None:
        return done, ["the page has too many blocks to read back; nothing was compared"], []
    present = {_norm(b.get("id")) for b in existing}
    diffs = [f"block {i} is still on the page" for i in removed if i in present]
    created = set(ids_of(responses[0]))
    read = [b for b in existing if _norm(b.get("id")) in created]
    if len(read) != len(sent):
        diffs.append(f"inserted {_count(len(sent), 'block')}, found {len(read)} on the page")
    else:
        diffs += compare_blocks(sent, read) + leftover_markdown(read)
    return done, diffs, ["the inserted top-level blocks", "the removed blocks are gone"]


__all__ = ["execute_edit", "propose_edit", "validate_edit"]
