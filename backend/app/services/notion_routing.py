"""Notes and long content for Notion go through the learning-book job, never a direct write.

The chat or voice model must not write notes itself with ``notion_create_page`` or
``notion_append_blocks`` (a title and a few bullets, then "too large for one page"). A request
for notes, detailed or content-rich text, a stated number of pages or chapters, a book, or content
that would exceed about 15 blocks is a job: ``submit_task`` / ``start_job`` runs the learning-book
pipeline, which writes it chapter by chapter and shows one approval card for the finished book.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

CREATE_PAGE: Final = "notion_create_page"
APPEND_BLOCKS: Final = "notion_append_blocks"
MAX_DIRECT_BLOCKS: Final = 15

_LONG: Final = re.compile(
    r"\b(?:notes?|detailed|content[- ]rich|chapters?|book|vistrit|lamba)\b"
    r"|\b\d+\s*(?:pages?|chapters?)\b",
    re.IGNORECASE,
)

BOOK_ROUTE_MESSAGE: Final = (
    "Nothing was written. Notes, detailed or long content for Notion are never written with a "
    "notion tool call: call submit_task now (deliverable 'notion') with the full brief: the "
    "user's topic, the size they asked for (pages or chapters) and the Notion page they named as "
    "the parent. The learning-book job writes it chapter by chapter and shows one approval card "
    "with the finished book. Do not say it is too large for Notion, and do not make a shorter "
    "version meanwhile."
)


_LONG_FOR_APPEND: Final = re.compile(
    r"\b(?:detailed|content[- ]rich|chapters?|book|vistrit|lamba)\b|\b\d+\s*(?:pages?|chapters?)\b",
    re.IGNORECASE,
)


def wants_long_notion_content(user_text: str, *, append: bool = False) -> bool:
    """``append``: adding a few notes to an existing page is fine; only a stated size or a
    detailed / book request is long there."""
    pattern = _LONG_FOR_APPEND if append else _LONG
    return pattern.search(user_text or "") is not None


def block_count(args: Mapping[str, Any]) -> int:
    """How many blocks / lines a direct write would carry."""
    blocks = args.get("blocks") or args.get("children")
    if isinstance(blocks, list):
        return len(blocks)
    for key in ("content", "markdown", "text", "body"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return len([line for line in value.splitlines() if line.strip()])
    return 0


def must_use_book_job(tool: str, args: Mapping[str, Any], user_text: str) -> bool:
    """True when a direct page write must be refused in favour of the learning-book job."""
    if tool not in (CREATE_PAGE, APPEND_BLOCKS):
        return False
    wants = wants_long_notion_content(user_text, append=tool == APPEND_BLOCKS)
    return wants or block_count(args) > MAX_DIRECT_BLOCKS
