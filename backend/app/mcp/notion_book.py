"""Large Notion writes from a finished job: ``notion_create_page_from_job`` (ADR-0031).

A learning book is 6,000 to 10,000 words: far more than one approval payload of explicit Notion
requests can carry (``notion_write`` allows 300 blocks per proposal). So this tool binds the
approval to the EXACT Markdown the job produced instead, and builds the requests at execution:

* :func:`prepare_book_write` runs in the worker just before the user is asked. It finds the
  stored Markdown artifact of the task, converts it with ``notion_markdown`` and returns small
  ``tool_args`` (title, parent, artifact id and file name, SHA-256 of the Markdown, counts, icon,
  cover) plus a readable summary. The approval payload hash covers those arguments, so it covers
  the content hash: change one byte of the Markdown and the approved run refuses.
* :func:`execute_book_write` runs in the MCP gateway only for a step whose approval the worker
  verified and claimed (``approved``). It re-reads the Markdown, checks the hash and the block
  count, creates the page with the first batch and appends the rest in batches (at most 100
  top-level blocks, 900 elements and 400 KB per request), waits and retries only on a 429 (Notion
  did not process it), never truncates, reads the page back and reports plain-language results.
  A failure part-way reports exactly how many blocks were written and offers to continue.

Nothing here talks to Notion except through the passed connector. The tool is APPROVAL_REQUIRED and
never auto-approved (``notion_auto_approve.ALWAYS_MANUAL_TOOLS``): a book is always read by a human
first.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse
from uuid import UUID

from app.automation.book_markdown import describe_markdown
from app.connections.providers import ConnectionInputError, normalize_notion_database_id
from app.mcp.connectors.notion import NotionCallError, NotionConnector, retry_after_seconds
from app.mcp.notion_markdown import MarkdownError, markdown_to_blocks
from app.mcp.notion_write import (
    ALLOWED_BLOCK_TYPES,
    NOT_APPROVED_MESSAGE,
    NotionWriteError,
    check_children,
    compare_blocks,
    failure_message,
    rich_text,
)
from app.tasks.needs_input import KIND_NOTION_PARENT, NeedsInput

logger = logging.getLogger(__name__)

CREATE_PAGE_FROM_JOB: Final = "notion_create_page_from_job"
NOTION_BOOK_TOOL_NAMES: Final = (CREATE_PAGE_FROM_JOB,)

MAX_BLOCKS_PER_REQUEST: Final = 100
MAX_ELEMENTS_PER_REQUEST: Final = 900  # Notion allows 1000 including table rows
MAX_BYTES_PER_REQUEST: Final = 400_000  # Notion allows 500 KB
MAX_BOOK_BLOCKS: Final = 5000
MAX_BOOK_BYTES: Final = 3_000_000
LARGE_WRITE_BLOCKS: Final = 300
"""Above this many blocks a write is always approved by a human (also the auto-approve rule)."""
READBACK_MAX_PAGES: Final = 60
RATE_LIMIT_TRIES: Final = 3
RATE_LIMIT_MAX_WAIT: Final = 30.0
PREVIEW_LINES: Final = 40
OUTLINE_IN_SUMMARY: Final = 80
DEFAULT_ICON: Final = "\U0001f4d8"

# Cover images: https only, from these hosts only; anything else is skipped (and said so).
COVER_HOSTS: Final = frozenset(
    {
        "images.unsplash.com",
        "upload.wikimedia.org",
        "images.pexels.com",
        "www.notion.so",
    }
)
_ARTIFACT_NAME: Final = re.compile(r"^[^\\/\x00-\x1f]{1,200}\.md$")
_HEX32: Final = re.compile(r"^[0-9a-f]{32}$")

REQUIRED_ARGS: Final = ("title", "parent_page_id", "content_file", "content_sha256", "blocks")


class BookWriteError(NotionWriteError):
    """A learning-book write could not be prepared or run (plain sentence)."""


# ------------------------------------------------------------------------------- manifest
def book_write_mcp_manifest() -> list[dict[str, Any]]:
    """The tool as an MCP manifest entry (served, never planner-visible: the plan names it only
    through ``app.agents.learning_book_plan``)."""
    return [
        {
            "name": CREATE_PAGE_FROM_JOB,
            "description": (
                "Approved Notion write (requires human approval): create ONE page from the "
                "Markdown a finished job produced, in batches, then read it back."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "parent_page_id": {"type": "string"},
                    "content_artifact_id": {"type": "string"},
                    "content_file": {"type": "string"},
                    "content_sha256": {"type": "string"},
                    "blocks": {"type": "integer"},
                    "icon": {"type": "string"},
                    "cover_url": {"type": "string"},
                },
                "required": list(REQUIRED_ARGS),
            },
        }
    ]


# ------------------------------------------------------------------------------ helpers
def sha256_text(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def normalize_icon(raw: Any) -> str:
    """One emoji (at most 8 code points, no letters or digits) or the default book icon."""
    text = str(raw or "").strip()
    if (
        text
        and len(text) <= 8
        and not text.isascii()
        and not any(ch.isalnum() and ch.isascii() for ch in text)
        and not any(ch.isspace() for ch in text)
    ):
        return text
    return DEFAULT_ICON


def allowed_cover(raw: Any) -> str:
    """``raw`` when it is an https image URL on an allow-listed host, else ``""`` (skipped)."""
    url = str(raw or "").strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in COVER_HOSTS
        and len(url) <= 2000
        and not any(c.isspace() for c in url)
    ):
        return url
    return ""


def book_blocks(markdown: str) -> list[dict[str, Any]]:
    """The top-level Notion blocks of the book (a ``[toc]`` line is the contents block)."""
    try:
        blocks = markdown_to_blocks(markdown)
    except MarkdownError as error:
        raise BookWriteError(str(error)) from None
    if not blocks:
        raise BookWriteError("The book has no content to write.")
    if len(blocks) > MAX_BOOK_BLOCKS:
        raise BookWriteError(
            f"The book is {len(blocks)} blocks, more than the {MAX_BOOK_BLOCKS} one page write "
            "carries. Split it into two pages."
        )
    return blocks


def _elements(block: dict[str, Any]) -> int:
    inner = block.get(str(block.get("type")))
    kids = inner.get("children") if isinstance(inner, dict) else None
    return (
        1 + sum(_elements(k) for k in kids if isinstance(k, dict)) if isinstance(kids, list) else 1
    )


def plan_batches(blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Cut ``blocks`` into requests of at most 100 top-level blocks, 900 elements (table rows
    count) and 400 KB. Nothing is dropped or shortened: the batches joined are ``blocks``."""
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    elements = size = 0
    for block in blocks:
        weight = _elements(block)
        bytes_ = len(json.dumps(block, ensure_ascii=False).encode("utf-8"))
        if current and (
            len(current) >= MAX_BLOCKS_PER_REQUEST
            or elements + weight > MAX_ELEMENTS_PER_REQUEST
            or size + bytes_ > MAX_BYTES_PER_REQUEST
        ):
            batches.append(current)
            current, elements, size = [], 0, 0
        current.append(block)
        elements += weight
        size += bytes_
    if current:
        batches.append(current)
    return batches


def _check_batch(batch: list[dict[str, Any]]) -> None:
    """The same allowed shapes as every Notion write, plus the contents block."""
    plain = [b for b in batch if b.get("type") != "table_of_contents"]
    for block in batch:
        kind = block.get("type")
        if kind != "table_of_contents" and kind not in ALLOWED_BLOCK_TYPES:
            raise BookWriteError("The book has a block type that is not allowed. Nothing written.")
    if plain:
        check_children(plain)


def summarize(markdown: str) -> dict[str, Any]:
    """The counts and outline shown on the approval card and in the job result."""
    blocks = book_blocks(markdown)
    facts = describe_markdown(markdown)
    chapters = [h for h in facts["outline"] if h["level"] == 1 and h["text"].startswith("Chapter ")]
    lines = markdown.split("\n")
    return {
        "words": facts["words"],
        "blocks": len(blocks),
        "batches": len(plan_batches(blocks)),
        "chapters": len(chapters),
        "headings": facts["headings"],
        "code_blocks": facts["code_blocks"],
        "tables": facts["tables"],
        "equations": facts["equations"],
        "outline": facts["outline"][:OUTLINE_IN_SUMMARY],
        "preview": "\n".join(lines[:PREVIEW_LINES]),
    }


def diff_text(args: dict[str, Any], facts: dict[str, Any], notes: list[str]) -> str:
    """The plain-language text of the approval card (what will be written)."""
    lines = [
        f"Create the Notion page '{args['title']}' under {args.get('parent_label') or 'the page'} "
        f"{args['parent_page_id']}.",
        f"{facts['words']:,} words, {facts['chapters']} chapters, {facts['blocks']} blocks "
        f"({facts['code_blocks']} code blocks, {facts['tables']} tables, {facts['equations']} "
        f"equations), written in {facts['batches']} requests, then read back to check it.",
        "Chapters:",
    ]
    lines += [f"{'  ' * (h['level'] - 1)}- {h['text']}" for h in facts["outline"]]
    lines += notes
    return "\n".join(lines)[:20_000]


# ------------------------------------------------------------------------------ preparing
def _task_dir(root: Path, workspace_id: UUID, task_id: UUID) -> Path:
    return Path(root).resolve() / str(workspace_id) / str(task_id)


def _read_markdown(path: Path) -> str:
    if path.stat().st_size > MAX_BOOK_BYTES:
        raise BookWriteError("The book file is too large to write as one page.")
    return path.read_text(encoding="utf-8")


def _candidates(directory: Path, rows: list[Any]) -> list[tuple[Path, str]]:
    """Candidate Markdown files of the task: ``(path, artifact id or "")`` newest row first."""
    found: list[tuple[Path, str]] = []
    for row in rows:
        for raw in (
            Path(str(getattr(row, "storage_uri", ""))),
            directory / Path(str(getattr(row, "file_name", ""))).name,
        ):
            if raw.is_file():
                found.append((raw, str(getattr(row, "id", ""))))
    if directory.is_dir():
        found += [(p, "") for p in sorted(directory.glob("*.md"), key=lambda p: -p.stat().st_mtime)]
    return found


async def prepare_book_write(
    *,
    artifacts_root: Path,
    workspace_id: UUID,
    task_id: UUID,
    input_data: dict[str, Any],
    artifact_rows: list[Any],
) -> dict[str, Any]:
    """``{"tool_args", "diff", "payload_diff", "summary"}`` for the approval, or raises
    :class:`BookWriteError` with a plain message. ``artifact_rows`` are the task's ``md``
    ``FileArtifact`` rows, newest first (may be empty: the file in the task folder still counts)."""
    raw_hints = input_data.get("notion_book")
    hints: dict[str, Any] = raw_hints if isinstance(raw_hints, dict) else {}
    raw_parent = str(hints.get("parent_page_id") or "").strip()
    if not raw_parent:
        # Not a failure: the worker pauses this step and asks on a card (with real pages as
        # options); the finished book Markdown stays in the job's files and the answer resumes
        # here (see app.tasks.needs_input).
        raise NeedsInput(
            KIND_NOTION_PARENT,
            "Which Notion page should the book go under? The finished book is kept; only the "
            "Notion page still needs a parent.",
            header="Notion page",
            offer_default=True,
        )
    try:
        parent = normalize_notion_database_id(raw_parent[:600])
    except ConnectionInputError:
        raise BookWriteError(
            "That is not a Notion page link or id. Copy the page link from Notion."
        ) from None
    directory = _task_dir(artifacts_root, workspace_id, task_id)
    inline = hints.get("inline_markdown")
    if isinstance(inline, str) and inline.strip():
        return await _prepare_existing(directory, parent, hints, inline)
    chosen: tuple[Path, str] | None = None
    for path, artifact_id in await asyncio.to_thread(_candidates, directory, artifact_rows):
        if path.resolve().parent == directory:
            chosen = (path, artifact_id)
            break
    if chosen is None:
        raise BookWriteError(
            "The finished book file was not found in MAYA's storage, so nothing was prepared."
        )
    path, artifact_id = chosen
    markdown = await asyncio.to_thread(_read_markdown, path)
    row_sha = next(
        (str(r.sha256) for r in artifact_rows if str(getattr(r, "id", "")) == artifact_id), ""
    )
    digest = sha256_text(markdown)
    file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if row_sha and row_sha != file_sha:
        raise BookWriteError("The stored book does not match its record, so nothing was prepared.")
    if not artifact_id:
        # The file was found in the task folder (the record may point at a copy in the connected
        # folder): the record with the same SHA-256 is the artifact the card opens.
        artifact_id = next(
            (str(r.id) for r in artifact_rows if str(getattr(r, "sha256", "")) == file_sha), ""
        )
    facts = summarize(markdown)
    title = " ".join(str(hints.get("title") or "").split()) or _title_from(markdown) or "Study book"
    notes: list[str] = []
    cover_raw = str(hints.get("cover_url") or "").strip()
    cover = allowed_cover(cover_raw)
    if cover_raw and not cover:
        notes.append("A cover image was skipped: only https images from known hosts are used.")
    args = {
        "title": title[:200],
        "parent_page_id": parent,
        "parent_label": str(hints.get("parent_label") or "")[:120],
        "content_artifact_id": artifact_id,
        "content_file": path.name,
        "content_sha256": digest,
        "content_bytes": len(markdown.encode("utf-8")),
        "blocks": facts["blocks"],
        "words": facts["words"],
        "chapters": facts["chapters"],
        "code_blocks": facts["code_blocks"],
        "tables": facts["tables"],
        "equations": facts["equations"],
        "icon": normalize_icon(hints.get("icon")),
        "cover_url": cover,
    }
    text = diff_text(args, facts, notes)
    return {
        "tool_args": args,
        "diff": text,
        "payload_diff": text,
        "summary": {k: v for k, v in facts.items() if k != "preview"},
    }


SAVED_NOTES_FILE: Final = "saved_notes.md"
EXISTING_PREVIEW_CHARS: Final = 15_000


def _write_inline(directory: Path, markdown: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / SAVED_NOTES_FILE
    path.write_text(markdown, encoding="utf-8", newline="\n")
    return path


def existing_diff_text(args: dict[str, Any], facts: dict[str, Any], markdown: str) -> str:
    """The approval text of a SAVE of existing content: where, what, counts and the content."""
    as_is = bool(args.get("as_is"))
    lines = [
        f"Save existing content to Notion as the new page '{args['title']}' under "
        f"{args.get('parent_label') or 'the page'} {args['parent_page_id']}.",
        f"Source: {args.get('source_label') or 'this conversation'}.",
        "As it is: the text below is copied unchanged; nothing was rewritten or added."
        if as_is
        else "Only light cleanup (blank lines, spacing) was applied; no facts were added.",
        f"{facts['words']:,} words, {facts['blocks']} blocks ({facts['headings']} headings, "
        f"{facts['code_blocks']} code blocks, {facts['tables']} tables), written in "
        f"{facts['batches']} requests, then read back to check it.",
    ]
    if args.get("source_note"):
        lines.append(str(args["source_note"]))
    shown = markdown[:EXISTING_PREVIEW_CHARS]
    lines.append("Content:")
    lines.append(shown)
    if len(markdown) > len(shown):
        lines.append(
            f"... (shown in part: the first {len(shown):,} of {len(markdown):,} characters; the "
            "whole content is what is written and what the approval is bound to)"
        )
    return "\n".join(lines)[:20_000]


async def _prepare_existing(
    directory: Path, parent: str, hints: dict[str, Any], markdown: str
) -> dict[str, Any]:
    """Approval content of a save of EXISTING text: the Markdown from the hints is stored in the
    job's folder (so the executor re-reads and hash-checks it exactly like a finished book),
    converted to blocks, and bound to the approval by its SHA-256."""
    if len(markdown.encode("utf-8")) > MAX_BOOK_BYTES:
        raise BookWriteError("The content is too large to write as one page.")
    path = await asyncio.to_thread(_write_inline, directory, markdown)
    facts = summarize(markdown)
    title = " ".join(str(hints.get("title") or "").split()) or _title_from(markdown) or "Notes"
    args = {
        "title": title[:200],
        "parent_page_id": parent,
        "parent_label": str(hints.get("parent_label") or "")[:120],
        "content_artifact_id": "",
        "content_file": path.name,
        "content_sha256": sha256_text(markdown),
        "content_bytes": len(markdown.encode("utf-8")),
        "blocks": facts["blocks"],
        "words": facts["words"],
        "chapters": facts["chapters"],
        "code_blocks": facts["code_blocks"],
        "tables": facts["tables"],
        "equations": facts["equations"],
        "icon": normalize_icon(hints.get("icon")) if hints.get("icon") else "\U0001f4dd",
        "cover_url": "",
        "source": "existing",
        "as_is": bool(hints.get("as_is")),
        "source_label": str(hints.get("source_label") or "")[:120],
        "source_note": str(hints.get("source_note") or "")[:300],
    }
    text = existing_diff_text(args, facts, markdown)
    return {
        "tool_args": args,
        "diff": text,
        "payload_diff": text,
        "summary": {k: v for k, v in facts.items() if k != "preview"},
    }


def _title_from(markdown: str) -> str:
    first = next((ln for ln in markdown.split("\n") if ln.strip().startswith("# ")), "")
    return re.sub(r"^#\s+(?:Chapter\s+\d+\s*[:.—-]\s*)?", "", first.strip())[:200]


# ------------------------------------------------------------------------------ executing
async def _send(
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """One request; a 429 (Notion did not process it) is waited out and sent again, at most
    :data:`RATE_LIMIT_TRIES` times. Any other failure is raised at once: it may have been applied,
    so it is never repeated."""
    for attempt in range(1, RATE_LIMIT_TRIES + 1):
        try:
            return await call()
        except NotionCallError as error:
            if error.response.status_code != 429 or attempt == RATE_LIMIT_TRIES:
                raise
            wait = retry_after_seconds(error.response) or 2.0 * attempt
            await sleep(min(wait, RATE_LIMIT_MAX_WAIT))
    raise AssertionError("unreachable")  # pragma: no cover


def _count(number: int, word: str) -> str:
    return f"{number} {word}" + ("" if number == 1 else "s")


def _validate_args(args: Any) -> dict[str, Any]:
    bad = BookWriteError("The approved payload is not a valid page write. Nothing written.")
    if not isinstance(args, dict):
        raise bad
    for key in REQUIRED_ARGS:
        if args.get(key) in (None, ""):
            raise bad
    if _HEX32.match(str(args["parent_page_id"])) is None:
        raise bad
    if _ARTIFACT_NAME.match(str(args["content_file"])) is None or "/" in str(args["content_file"]):
        raise bad
    if re.fullmatch(r"[0-9a-f]{64}", str(args["content_sha256"])) is None:
        raise bad
    if not isinstance(args["blocks"], int) or args["blocks"] < 1:
        raise bad
    resume = str(args.get("resume_page_id") or "")
    if resume and (
        _HEX32.match(resume) is None or not isinstance(args.get("resume_from_block"), int)
    ):
        raise bad
    return args


async def _read_children(connector: NotionConnector, block_id: str) -> list[dict[str, Any]] | None:
    blocks: list[dict[str, Any]] = []
    cursor: str | None = None
    for _page in range(READBACK_MAX_PAGES):
        data = await connector.list_block_children(block_id, start_cursor=cursor)
        results = data.get("results")
        blocks.extend(
            b for b in (results if isinstance(results, list) else []) if isinstance(b, dict)
        )
        cursor = str(data.get("next_cursor") or "") or None
        if not data.get("has_more") or cursor is None:
            return blocks
    return None


async def verify_page(
    connector: NotionConnector, page_id: str, sent: list[dict[str, Any]]
) -> tuple[list[str], str | None]:
    """Read the page's children back and compare them with what was sent.

    Returns ``(differences, reason the check could not run or None)``: the block count, the
    sequence of headings, and each block's type and text through ``compare_blocks``."""
    try:
        read = await _read_children(connector, page_id)
    except Exception as error:  # noqa: BLE001 - the write is done; only the check failed
        return [], failure_message(error)
    if read is None:
        return [], "the page has too many blocks to read back"
    diffs = compare_blocks(sent, read)
    sent_heads = [
        (b["type"], _heading_text(b)) for b in sent if str(b["type"]).startswith("heading_")
    ]
    read_heads = [
        (b.get("type"), _heading_text(b)) for b in read if str(b.get("type")).startswith("heading_")
    ]
    if len(sent_heads) != len(read_heads):
        diffs.insert(0, f"sent {len(sent_heads)} headings, Notion has {len(read_heads)}")
    return diffs, None


def _heading_text(block: dict[str, Any]) -> str:
    data = block.get(str(block.get("type")))
    parts = data.get("rich_text") if isinstance(data, dict) else None
    out = []
    for part in parts if isinstance(parts, list) else []:
        text = part.get("text") if isinstance(part, dict) else None
        out.append(str((text or {}).get("content") or part.get("plain_text") or ""))
    return "".join(out).strip()


def _verdict(sent: int, diffs: list[str], unverified: str | None) -> str:
    if unverified is not None:
        return f"NOT VERIFIED: the read-back check could not run ({unverified})."
    if diffs:
        shown = "\n".join(f"- {d}" for d in diffs[:20])
        more = f"\n- and {len(diffs) - 20} more" if len(diffs) > 20 else ""
        return (
            f"VERIFICATION FOUND {_count(len(diffs), 'DIFFERENCE')} between what was sent and "
            f"what Notion now has:\n{shown}{more}"
        )
    return f"VERIFIED by reading it back: {_count(sent, 'block')} and every heading match."


async def execute_book_write(
    args: dict[str, Any],
    connector: NotionConnector | None,
    *,
    approved: bool,
    artifacts_root: Path,
    workspace_id: UUID | None,
    task_id: UUID | None,
    progress: Callable[[str], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Create the page from the approved Markdown and return the result text (read-back check
    included). Raises :class:`BookWriteError` (plain sentence) when nothing, or only a part, was
    written. ``approved`` must be the gateway's ``has_valid_approval_token`` flag."""
    if approved is not True or connector is None:
        raise BookWriteError(NOT_APPROVED_MESSAGE)
    checked = _validate_args(args)
    if workspace_id is None or task_id is None:
        raise BookWriteError("This write has no job, so it cannot find the book. Nothing written.")
    path = _task_dir(artifacts_root, workspace_id, task_id) / str(checked["content_file"])
    if not path.is_file():
        raise BookWriteError("The approved book file is gone from storage. Nothing was written.")
    markdown = await asyncio.to_thread(_read_markdown, path)
    if sha256_text(markdown) != checked["content_sha256"]:
        raise BookWriteError(
            "The book file changed after you approved it, so nothing was written. Ask for a new "
            "approval."
        )
    blocks = book_blocks(markdown)
    if len(blocks) != checked["blocks"]:
        raise BookWriteError(
            "The approved book no longer converts to the same blocks. Nothing was written."
        )
    batches = plan_batches(blocks)
    for batch in batches:
        _check_batch(batch)
    resume = str(checked.get("resume_page_id") or "")
    skip = int(checked.get("resume_from_block") or 0)
    page_id, url, written = resume, "", skip
    total = len(batches)

    async def note(text: str) -> None:
        if progress is not None:
            await progress(text)

    try:
        if not resume:
            body: dict[str, Any] = {
                "parent": {"page_id": checked["parent_page_id"]},
                "properties": {"title": {"title": rich_text(str(checked["title"]), what="Title")}},
                "icon": {"type": "emoji", "emoji": normalize_icon(checked.get("icon"))},
                "children": batches[0],
            }
            cover = allowed_cover(checked.get("cover_url"))
            if cover:
                body["cover"] = {"type": "external", "external": {"url": cover}}
            await note(f"Writing to Notion: batch 1 of {total}")
            created = await _send(lambda: connector.create_page(body), sleep=sleep)
            page_id = str(created.get("id") or "").replace("-", "")
            url = str(created.get("url") or "")
            if not page_id:
                raise BookWriteError(
                    "Notion returned no page id. Check Notion before asking again."
                )
            written = len(batches[0])
            start = 1
        else:
            start, written = _resume_batches(batches, skip)
        for number in range(start, total):
            await note(f"Writing to Notion: batch {number + 1} of {total}")
            batch = batches[number]
            await _send(
                lambda batch=batch: connector.append_block_children(  # type: ignore[misc]
                    page_id, {"children": batch}
                ),
                sleep=sleep,
            )
            written += len(batch)
    except BookWriteError:
        raise
    except Exception as error:  # noqa: BLE001 - mapped to one plain sentence
        reason = failure_message(error)
        if not page_id:
            raise BookWriteError(reason) from None
        link = f" Link: {url}" if url else ""
        raise BookWriteError(
            f"Only {written} of {len(blocks)} blocks were written to the page {page_id}.{link} "
            f"The request after that failed: {reason} Nothing was removed. I can continue by "
            f"appending the remaining {len(blocks) - written} blocks (resume_page_id "
            f"{page_id}, resume_from_block {written}) once you approve it."
        ) from None
    await note("Checking the page by reading it back")
    diffs, unverified = await verify_page(connector, page_id, blocks)
    link = f" Link: {url}" if url else ""
    done = (
        f"Created the Notion page {page_id}: {_count(len(blocks), 'block')} in "
        f"{_count(total, 'request')}.{link}"
    )
    return f"{done}\n{_verdict(len(blocks), diffs, unverified)}"


def _resume_batches(batches: list[list[dict[str, Any]]], written: int) -> tuple[int, int]:
    """``(first batch index still to send, blocks those before it hold)`` for a resume at
    ``written`` (must fall on a batch boundary of the same plan)."""
    done = 0
    for index, batch in enumerate(batches):
        if done == written:
            return index, done
        done += len(batch)
    if done == written:
        return len(batches), done
    raise BookWriteError(
        "The resume point does not match the batches of this book. Nothing was written."
    )


def continue_args(args: dict[str, Any], page_id: str, written: int) -> dict[str, Any]:
    """New (hash-bound) arguments that append only the blocks not yet written."""
    return {**args, "resume_page_id": page_id, "resume_from_block": written}


__all__ = [
    "COVER_HOSTS",
    "CREATE_PAGE_FROM_JOB",
    "LARGE_WRITE_BLOCKS",
    "NOTION_BOOK_TOOL_NAMES",
    "BookWriteError",
    "allowed_cover",
    "book_blocks",
    "book_write_mcp_manifest",
    "continue_args",
    "execute_book_write",
    "normalize_icon",
    "plan_batches",
    "prepare_book_write",
    "sha256_text",
    "summarize",
    "verify_page",
]
