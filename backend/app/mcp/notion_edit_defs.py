"""Declarations of the Notion tools that change EXISTING content (pure data, no logic).

All of them are APPROVAL_REQUIRED (``app/mcp/permissions.py``): the chat tool call only prepares
the exact Notion request (``app/mcp/notion_edit``), and it is sent once, after the user approved
that payload, by ``app/mcp/notion_write.execute_write``.
"""

from __future__ import annotations

from typing import Any, Final

UPDATE_PAGE: Final = "notion_update_page"
ARCHIVE_PAGE: Final = "notion_archive_page"
UPDATE_BLOCK: Final = "notion_update_block"
DELETE_BLOCK: Final = "notion_delete_block"
ADD_COMMENT: Final = "notion_add_comment"
REPLACE_SECTION: Final = "notion_replace_section"
EDIT_TOOL_NAMES: Final = (
    UPDATE_PAGE,
    ARCHIVE_PAGE,
    UPDATE_BLOCK,
    DELETE_BLOCK,
    ADD_COMMENT,
    REPLACE_SECTION,
)

_NOTE: Final = (
    " Nothing is changed by this call: it prepares the exact Notion request, which waits for the "
    "user's approval. Say it waits for approval; never say it was changed."
)
_PAGE_ID: Final = {"type": "string", "description": "The page link or id."}
_MARKDOWN: Final = {
    "type": "string",
    "description": (
        "The content as Markdown (headings, **bold**, lists, to-dos, code fences, tables, "
        "> [!NOTE] callouts, links). It is converted to Notion blocks; the approval shows them."
    ),
}

EDIT_TOOL_DEFS: Final[list[dict[str, Any]]] = [
    {
        "name": UPDATE_PAGE,
        "description": (
            "Ask to change an existing page: its title, its emoji icon and, for a database row, "
            "column values typed from the real schema (call notion_get_database first). Relation "
            "and rollup columns are read-only." + _NOTE
        ),
        "properties": {
            "page_id": _PAGE_ID,
            "title": {"type": "string", "description": "The new title."},
            "icon": {"type": "string", "description": "One emoji for the page icon."},
            "properties": {
                "type": "array",
                "description": "Column values: exact column 'name' and a text 'value'.",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                    "required": ["name", "value"],
                },
            },
        },
        "required": ["page_id"],
    },
    {
        "name": ARCHIVE_PAGE,
        "description": (
            "Ask to ARCHIVE a page (move it to the Notion trash; it can be restored from there). "
            "This is destructive: use it only when the user clearly asked to delete or archive "
            "that page." + _NOTE
        ),
        "properties": {"page_id": _PAGE_ID},
        "required": ["page_id"],
    },
    {
        "name": UPDATE_BLOCK,
        "description": (
            "Ask to change the text of one existing block (get its id from notion_get_page). The "
            "block keeps its type. Inline Markdown such as **bold** is allowed." + _NOTE
        ),
        "properties": {
            "block_id": {"type": "string", "description": "The block id."},
            "text": {"type": "string", "description": "The new text of the block."},
            "checked": {"type": "boolean", "description": "To-do blocks only."},
        },
        "required": ["block_id", "text"],
    },
    {
        "name": DELETE_BLOCK,
        "description": (
            "Ask to delete one block (it moves to the Notion trash). Destructive: use it only "
            "when the user asked to remove that content." + _NOTE
        ),
        "properties": {"block_id": {"type": "string", "description": "The block id."}},
        "required": ["block_id"],
    },
    {
        "name": ADD_COMMENT,
        "description": (
            "Ask to add a comment to a page. It needs the integration's comment capability." + _NOTE
        ),
        "properties": {"page_id": _PAGE_ID, "text": {"type": "string"}},
        "required": ["page_id", "text"],
    },
    {
        "name": REPLACE_SECTION,
        "description": (
            "Ask to replace one section of a page: the blocks under a heading (up to the next "
            "heading of the same or a higher level) are removed and the new Markdown is inserted "
            "in their place. Name the heading text, or give start_block_id. The approval lists "
            "exactly which blocks are removed. Never use it to clear a whole page." + _NOTE
        ),
        "properties": {
            "page_id": _PAGE_ID,
            "heading": {"type": "string", "description": "Text of the heading that starts it."},
            "start_block_id": {"type": "string", "description": "Or the heading block's id."},
            "markdown": _MARKDOWN,
        },
        "required": ["page_id", "markdown"],
    },
]

# Parameters added to the existing write tools (see notion_write.py).
MARKDOWN_PARAM: Final = _MARKDOWN
ALLOW_DUPLICATE_PARAM: Final = {
    "type": "boolean",
    "description": (
        "Set true only when the user explicitly wants a second page or row with a title that "
        "already exists."
    ),
}
AFTER_BLOCK_PARAM: Final = {
    "type": "string",
    "description": "Insert the content after this block id instead of at the end of the page.",
}

NOTION_EDIT_CHAT_INSTRUCTION: Final = (
    "Working with existing Notion content: find the page with notion_find_page (by the title the "
    "user names) or the row with notion_query_database, read it with notion_get_page, then use "
    "notion_append_blocks (add at the end, or after_block_id to insert after a block), "
    "notion_replace_section, notion_update_block, notion_update_page (title, icon, row columns), "
    "notion_add_comment, notion_delete_block or notion_archive_page. Write note content as "
    "Markdown in the 'markdown' argument; it is converted to real Notion blocks. If a page or "
    "row with the same title already exists, do not create a duplicate: say so and offer to "
    "update or append to it. Relation and rollup columns cannot be written. Every change waits "
    "for the user's approval and is checked by reading it back; report only that."
)
