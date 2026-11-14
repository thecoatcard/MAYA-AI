"""``notion_create_page_from_job``: a whole book to Notion, offline against the fake Notion API.

Covers the conversion (contents block, equations, code language aliases, tables over 100 rows,
text over 2000 characters), the batches (at most 100 blocks per request, nothing dropped), the
approval binding to the exact Markdown, rate limits, a failure part-way and how it continues, the
read-back check, the icon and cover rules, and the auto-approve guard for large pages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.mcp import notion_auto_approve as auto
from app.mcp import notion_book as nb
from app.mcp.approvals import compute_payload_hash
from app.mcp.client_manager import MCPClientManager
from app.mcp.gateway import McpGateway, builtin_spec
from app.mcp.permissions import PermissionDeniedError, ToolPermissionRegistry, ToolTier
from app.mcp.scope import tool_call_scope
from tests.support.notion_fake import PAGE, FakeNotionApi
from tests.unit.test_notion_auto_approve import plan_for, stored

WS = uuid4()
TASK = uuid4()


def book_markdown(chapters: int = 3, paragraphs: int = 30) -> str:
    parts = ["*A story-based learning book*", "[toc]"]
    for number in range(1, chapters + 1):
        parts.append(f"# Chapter {number}: Part {number}")
        parts.append("## Big part")
        parts.append("### Understanding")
        parts += [
            f"Paragraph {i} of chapter {number} with **bold** words." for i in range(paragraphs)
        ]
        parts.append("```py\nprint('x')\n```")
        parts.append("$$ a^2 + b^2 = c^2 $$")
        parts.append("| A | B |\n|---|---|\n| 1 | 2 |")
    return "\n\n".join(parts) + "\n"


def store_book(root: Path, markdown: str, name: str = "book.md") -> Path:
    folder = root / str(WS) / str(TASK)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(markdown, encoding="utf-8")
    return path


async def prepare(root: Path, **hints: Any) -> dict[str, Any]:
    return await nb.prepare_book_write(
        artifacts_root=root,
        workspace_id=WS,
        task_id=TASK,
        input_data={"notion_book": {"parent_page_id": PAGE, "title": "My Book", **hints}},
        artifact_rows=[],
    )


async def run(fake: FakeNotionApi, args: dict[str, Any], root: Path, **kwargs: Any) -> str:
    return await nb.execute_book_write(
        args,
        fake.connector(),
        approved=True,
        artifacts_root=root,
        workspace_id=WS,
        task_id=TASK,
        sleep=fake._sleep,
        **kwargs,
    )


# --------------------------------------------------------------------------- conversion
def test_the_contents_marker_equations_tables_and_code_aliases_convert() -> None:
    languages = ["js", "py", "c++", "shell", "java", "totallyunknown"]
    fences = "\n\n".join(f"```{lang}\ncode\n```" for lang in languages)
    markdown = f"[toc]\n\n$$ x^2 $$\n\n{fences}\n"
    blocks = nb.book_blocks(markdown)
    kinds = [b["type"] for b in blocks]
    assert kinds[0] == "table_of_contents" and kinds[1] == "equation"
    assert [b["code"]["language"] for b in blocks if b["type"] == "code"] == [
        "javascript",
        "python",
        "c++",
        "shell",
        "java",
        "plain text",  # unknown becomes plain text, never an invalid language
    ]


def test_long_text_and_big_tables_are_split_never_truncated() -> None:
    long_text = "word " * 1200  # 6000 characters: over Notion's 2000 per rich_text item
    rows = "\n".join(f"| r{i} | v{i} |" for i in range(250))
    markdown = f"{long_text}\n\n| K | V |\n|---|---|\n{rows}\n"
    blocks = nb.book_blocks(markdown)
    paragraph = blocks[0]["paragraph"]["rich_text"]
    assert all(len(item["text"]["content"]) <= 2000 for item in paragraph)
    assert "".join(i["text"]["content"] for i in paragraph) == long_text.strip()
    tables = [b for b in blocks if b["type"] == "table"]
    assert len(tables) == 3 and all(len(t["table"]["children"]) <= 100 for t in tables)
    body_rows = sum(len(t["table"]["children"]) - 1 for t in tables)  # minus repeated headers
    assert body_rows == 250


def test_batches_hold_at_most_100_blocks_and_together_are_every_block() -> None:
    blocks = nb.book_blocks("\n\n".join(f"Paragraph {i}" for i in range(250)))
    batches = nb.plan_batches(blocks)
    assert [len(b) for b in batches] == [100, 100, 50]
    assert [b for batch in batches for b in batch] == blocks
    for batch in batches:
        nb._check_batch(batch)  # the same allowed shapes as every Notion write


def test_batches_also_respect_the_element_and_size_limits() -> None:
    rows = "\n".join(f"| r{i} | v{i} |" for i in range(90))
    markdown = "\n\n".join(f"| K | V |\n|---|---|\n{rows}" for _ in range(20))
    batches = nb.plan_batches(nb.book_blocks(markdown))
    assert len(batches) > 1  # 20 tables of 91 rows are 1,800+ elements: not one request
    for batch in batches:
        assert sum(nb._elements(b) for b in batch) <= nb.MAX_ELEMENTS_PER_REQUEST


def test_a_book_without_content_or_too_big_is_refused_in_plain_words() -> None:
    with pytest.raises(nb.BookWriteError, match="no content"):
        nb.book_blocks("   \n")
    huge = "\n\n".join("p" for _ in range(nb.MAX_BOOK_BLOCKS + 1))
    with pytest.raises(nb.BookWriteError, match="Split it into two pages"):
        nb.book_blocks(huge)


# --------------------------------------------------------------------------- preparing
async def test_the_approval_arguments_bind_the_exact_markdown_and_show_the_outline(
    tmp_path: Path,
) -> None:
    markdown = book_markdown()
    store_book(tmp_path, markdown)
    prepared = await prepare(tmp_path)
    args = prepared["tool_args"]
    assert args["content_sha256"] == nb.sha256_text(markdown)
    assert args["content_file"] == "book.md" and args["parent_page_id"] == PAGE
    assert args["title"] == "My Book" and args["blocks"] == len(nb.book_blocks(markdown))
    assert args["chapters"] == 3 and args["code_blocks"] == 3 and args["tables"] == 3
    assert args["equations"] == 3 and args["words"] > 100
    diff = prepared["diff"]
    assert "My Book" in diff and "Chapter 1: Part 1" in diff and "3 chapters" in diff
    assert prepared["summary"]["outline"][0] == {"level": 1, "text": "Chapter 1: Part 1"}

    # the hash the user approves covers the content hash: one changed byte is another payload
    def digest(a: dict[str, Any]) -> str:
        return compute_payload_hash(
            tool=nb.CREATE_PAGE_FROM_JOB,
            args=a,
            workspace_id=WS,
            user_id=UUID(int=1),
            idempotency_key="k",
        )

    store_book(tmp_path, markdown + "x")
    changed = (await prepare(tmp_path))["tool_args"]
    assert digest(changed) != digest(args)


async def test_a_missing_parent_or_file_is_refused_before_anyone_is_asked(tmp_path: Path) -> None:
    with pytest.raises(nb.BookWriteError, match="not found in MAYA's storage"):
        await prepare(tmp_path)
    store_book(tmp_path, book_markdown())
    # A missing parent is no longer a failure: the step pauses and asks on a card (NeedsInput).
    from app.tasks.needs_input import NeedsInput

    with pytest.raises(NeedsInput):
        await nb.prepare_book_write(
            artifacts_root=tmp_path,
            workspace_id=WS,
            task_id=TASK,
            input_data={"notion_book": {"parent_page_id": ""}},
            artifact_rows=[],
        )
    with pytest.raises(nb.BookWriteError, match="not a Notion page link"):
        await prepare(tmp_path, parent_page_id="not a link")


async def test_icon_and_cover_rules(tmp_path: Path) -> None:
    store_book(tmp_path, book_markdown())
    good = "https://images.unsplash.com/photo-1?w=1200"
    ok = (await prepare(tmp_path, icon="\U0001f4da", cover_url=good))["tool_args"]
    assert ok["icon"] == "\U0001f4da" and ok["cover_url"] == good
    bad = await prepare(tmp_path, icon="book!", cover_url="http://evil.example/x.png")
    assert bad["tool_args"]["icon"] == nb.DEFAULT_ICON and bad["tool_args"]["cover_url"] == ""
    assert "cover image was skipped" in bad["diff"]
    assert nb.allowed_cover("https://evil.example/x.png") == ""  # not on the allow-list
    assert nb.allowed_cover("javascript:alert(1)") == ""


# --------------------------------------------------------------------------- executing
async def test_the_page_is_created_in_batches_and_read_back(tmp_path: Path) -> None:
    markdown = book_markdown(chapters=3, paragraphs=100)  # 3 x 106 blocks: three requests
    store_book(tmp_path, markdown)
    args = (await prepare(tmp_path, cover_url="https://images.unsplash.com/p.jpg"))["tool_args"]
    fake = FakeNotionApi()
    progress: list[str] = []

    async def note(text: str) -> None:
        progress.append(text)

    result = await run(fake, args, tmp_path, progress=note)
    sent = nb.book_blocks(markdown)
    bodies = fake.write_bodies()
    assert len(bodies) == len(nb.plan_batches(sent)) >= 3
    first = bodies[0]
    assert first["parent"] == {"page_id": PAGE} and first["icon"]["emoji"] == nb.DEFAULT_ICON
    assert first["cover"]["external"]["url"] == "https://images.unsplash.com/p.jpg"
    assert len(first["children"]) <= 100 and all(len(b["children"]) <= 100 for b in bodies[1:])
    page_id = next(k for k in fake.children if k not in (PAGE, "aaaabbbbccccddddeeeeffff00001111"))
    assert len(fake.children[page_id]) == len(sent)  # every block, in order
    assert fake.children[page_id][1]["type"] == "table_of_contents"  # after the intro line
    assert "VERIFIED by reading it back" in result and f"{len(sent)} blocks" in result
    assert "https://notion.so/" in result
    assert (
        progress[0].startswith("Writing to Notion: batch 1 of")
        and "reading it back" in progress[-1]
    )


async def test_nothing_runs_without_an_approval_or_with_a_changed_file(tmp_path: Path) -> None:
    store_book(tmp_path, book_markdown())
    args = (await prepare(tmp_path))["tool_args"]
    fake = FakeNotionApi()
    with pytest.raises(nb.BookWriteError, match="no approval"):
        await nb.execute_book_write(
            args, fake.connector(), approved=False, artifacts_root=tmp_path,
            workspace_id=WS, task_id=TASK,
        )  # fmt: skip
    store_book(tmp_path, book_markdown() + "\nTampered paragraph.\n")
    with pytest.raises(nb.BookWriteError, match="changed after you approved"):
        await run(fake, args, tmp_path)
    assert fake.writes == []  # not one request reached Notion
    forged = {**args, "content_file": "../../etc/passwd.md"}
    with pytest.raises(nb.BookWriteError, match="not a valid page write"):
        await run(fake, forged, tmp_path)
    assert fake.writes == []


async def test_the_gateway_refuses_the_tool_without_a_verified_approval(tmp_path: Path) -> None:
    store_book(tmp_path, book_markdown())
    args = (await prepare(tmp_path))["tool_args"]
    fake = FakeNotionApi()
    user = uuid4()

    class Connections:
        async def notion_connector(self, workspace_id: Any, user_id: Any) -> Any:
            return (fake.connector(), None) if user_id == user else None

    registry = ToolPermissionRegistry()
    assert registry.classify(nb.CREATE_PAGE_FROM_JOB) == ToolTier.APPROVAL_REQUIRED
    gateway = McpGateway(MCPClientManager(permission_registry=registry))
    gateway.register_spec(
        builtin_spec(github_token=None, connections=Connections(), artifacts_dir=tmp_path)  # type: ignore[arg-type]
    )
    try:
        with tool_call_scope(WS, TASK, user):
            with pytest.raises(PermissionDeniedError):
                await gateway.invoke(nb.CREATE_PAGE_FROM_JOB, args)
            with pytest.raises(PermissionDeniedError):  # the flag cannot be smuggled in
                await gateway.invoke(
                    nb.CREATE_PAGE_FROM_JOB, {**args, "has_valid_approval_token": True}
                )
            assert fake.writes == []
            result = await gateway.invoke(
                nb.CREATE_PAGE_FROM_JOB, args, has_valid_approval_token=True
            )
        assert "VERIFIED by reading it back" in result.clean_text
        assert len(fake.writes) >= 1
    finally:
        await gateway.close()


async def test_a_rate_limit_is_waited_out_and_the_request_sent_again(tmp_path: Path) -> None:
    store_book(tmp_path, book_markdown())
    args = (await prepare(tmp_path))["tool_args"]
    fake = FakeNotionApi()
    limited = httpx.Response(429, headers={"retry-after": "2"}, json={"code": "rate_limited"})
    fake.write_answers = [limited, limited, limited]  # the connector's own retry and ours
    result = await run(fake, args, tmp_path)
    assert fake.sleeps and max(fake.sleeps) <= nb.RATE_LIMIT_MAX_WAIT
    assert "VERIFIED by reading it back" in result


async def test_a_rate_limit_that_never_clears_is_reported_not_claimed(tmp_path: Path) -> None:
    store_book(tmp_path, book_markdown())
    args = (await prepare(tmp_path))["tool_args"]
    fake = FakeNotionApi()
    limited = httpx.Response(429, headers={"retry-after": "1"}, json={"code": "rate_limited"})
    fake.write_answers = [limited] * 20
    with pytest.raises(nb.BookWriteError, match="rate limiting"):
        await run(fake, args, tmp_path)


async def test_a_failure_part_way_reports_the_exact_count_and_can_continue(
    tmp_path: Path,
) -> None:
    markdown = book_markdown(chapters=4, paragraphs=70)  # more than 3 batches
    store_book(tmp_path, markdown)
    args = (await prepare(tmp_path))["tool_args"]
    sent = nb.book_blocks(markdown)
    batches = nb.plan_batches(sent)
    assert len(batches) >= 3
    fake = FakeNotionApi()
    connector = fake.connector()
    real = connector.append_block_children
    calls = {"n": 0}

    async def flaky(block_id: str, body: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 2:  # the second append fails: batches 1 and 2 are written
            raise httpx.ConnectError("boom")
        return await real(block_id, body)

    connector.append_block_children = flaky  # type: ignore[method-assign]
    with pytest.raises(nb.BookWriteError) as caught:
        await nb.execute_book_write(
            args, connector, approved=True, artifacts_root=tmp_path,
            workspace_id=WS, task_id=TASK, sleep=fake._sleep,
        )  # fmt: skip
    written = len(batches[0]) + len(batches[1])
    message = str(caught.value)
    assert f"Only {written} of {len(sent)} blocks were written" in message
    assert "I can continue" in message and f"resume_from_block {written}" in message
    assert "Nothing was removed" in message
    page_id = next(k for k in fake.children if len(fake.children[k]) == written)
    # continue: append only the rest to the same page (a new approval, new hash-bound arguments)
    resumed = nb.continue_args(args, page_id, written)
    result = await run(fake, resumed, tmp_path)
    assert len(fake.children[page_id]) == len(sent)  # nothing duplicated, nothing missing
    assert "VERIFIED by reading it back" in result
    with pytest.raises(nb.BookWriteError, match="resume point"):  # not on a batch boundary
        await run(fake, nb.continue_args(args, page_id, written + 1), tmp_path)


async def test_a_failed_first_request_writes_nothing_and_says_so(tmp_path: Path) -> None:
    store_book(tmp_path, book_markdown())
    args = (await prepare(tmp_path))["tool_args"]
    fake = FakeNotionApi()
    fake.write_answers = [httpx.Response(403, json={"code": "restricted_resource"})]
    with pytest.raises(nb.BookWriteError, match="not allowed to make this change"):
        await run(fake, args, tmp_path)
    assert not any(p for p in fake.pages if p not in (PAGE, "aaaabbbbccccddddeeeeffff00001111"))


# --------------------------------------------------------------------------- read-back
async def test_the_read_back_finds_a_block_that_differs(tmp_path: Path) -> None:
    markdown = book_markdown(chapters=1, paragraphs=5)
    store_book(tmp_path, markdown)
    args = (await prepare(tmp_path))["tool_args"]
    fake = FakeNotionApi()
    await run(fake, args, tmp_path)
    sent = nb.book_blocks(markdown)
    page_id = next(k for k in fake.children if len(fake.children[k]) == len(sent))
    connector = fake.connector()
    diffs, reason = await nb.verify_page(connector, page_id, sent)
    assert diffs == [] and reason is None
    changed = next(b for b in fake.children[page_id] if b["type"] == "heading_1")
    changed["heading_1"]["rich_text"][0]["plain_text"] = "Something else"
    diffs, _ = await nb.verify_page(connector, page_id, sent)
    assert any("heading_1" in d and "Something else" in d for d in diffs)
    fake.children[page_id].pop()
    diffs, _ = await nb.verify_page(connector, page_id, sent)
    assert any("Notion has" in d for d in diffs)  # a missing block is a count difference


async def test_when_the_read_back_cannot_run_the_result_says_not_verified(tmp_path: Path) -> None:
    store_book(tmp_path, book_markdown(chapters=1, paragraphs=3))
    args = (await prepare(tmp_path))["tool_args"]
    fake = FakeNotionApi()
    fake.fail_reads = True
    result = await run(fake, args, tmp_path)
    assert result.startswith("Created the Notion page") and "NOT VERIFIED" in result


# --------------------------------------------------------------------------- auto-approve
async def test_a_book_is_never_auto_approved_even_with_every_scope_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert nb.CREATE_PAGE_FROM_JOB in auto.ALWAYS_MANUAL_TOOLS
    assert nb.CREATE_PAGE_FROM_JOB not in auto.SCOPE_BY_TOOL
    every = stored(True, auto.SCOPES)
    plan = await plan_for(
        every, tool=nb.CREATE_PAGE_FROM_JOB, args={"blocks": 40}, monkeypatch=monkeypatch
    )
    assert plan.allowed is False


async def test_a_page_over_300_blocks_is_always_manual_even_for_create_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    every = stored(True, auto.SCOPES)
    assert auto.LARGE_WRITE_BLOCKS == nb.LARGE_WRITE_BLOCKS == 300
    big = {"blocks": 301, "requests": []}
    assert (await plan_for(every, args=big, monkeypatch=monkeypatch)).allowed is False
    assert auto.block_count({"blocks": 7}) == 7
    children = [{"object": "block"}] * 120
    two = {"requests": [{"body": {"children": children}}, {"body": {"children": children}}]}
    assert auto.block_count(two) == 240
    small = await plan_for(every, monkeypatch=monkeypatch)  # an ordinary small page still works
    assert small.allowed is True


def test_the_tool_is_classified_and_never_exposed_to_the_chat_model() -> None:
    from app.mcp.notion_write import NOTION_WRITE_TOOL_NAMES

    assert nb.CREATE_PAGE_FROM_JOB not in NOTION_WRITE_TOOL_NAMES  # only a job can propose it
    manifest = nb.book_write_mcp_manifest()
    assert manifest[0]["name"] == nb.CREATE_PAGE_FROM_JOB
    assert set(manifest[0]["inputSchema"]["required"]) == set(nb.REQUIRED_ARGS)
    assert json.dumps(manifest)  # serialisable


# --------------------------------------------------------------------------- worker hook
class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def scalars(self) -> _Rows:
        return self

    def all(self) -> list[Any]:
        return self.rows


class _Session:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    async def execute(self, _statement: Any) -> _Rows:
        return _Rows(self.rows)


async def test_the_worker_prepares_the_approval_from_the_stored_artifact(tmp_path: Path) -> None:
    import hashlib
    from types import SimpleNamespace

    from app.tasks.queue import SubtaskJob
    from app.tasks.worker import SubtaskWorker

    markdown = book_markdown()
    path = store_book(tmp_path, markdown)
    row = SimpleNamespace(
        id=uuid4(),
        file_name=path.name,
        storage_uri=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    job = SubtaskJob(
        workspace_id=WS, user_id=uuid4(), task_id=TASK, subtask_id=uuid4(), assigned_agent="pm"
    )
    subtask = SimpleNamespace(
        input_data={"notion_book": {"parent_page_id": PAGE, "title": "Worker Book"}}
    )
    host = SimpleNamespace(fleet_support=SimpleNamespace(artifacts_dir=tmp_path))
    prepared = await SubtaskWorker._prepare_notion_book(host, _Session([row]), job, subtask)  # type: ignore[arg-type]
    assert prepared["tool_args"]["content_artifact_id"] == str(row.id)
    assert prepared["tool_args"]["content_sha256"] == nb.sha256_text(markdown)
    assert prepared["tool_args"]["title"] == "Worker Book"
    # a stored copy that no longer matches its record is refused, not approved
    path.write_text(markdown + "edited", encoding="utf-8")
    with pytest.raises(nb.BookWriteError, match="does not match its record"):
        await SubtaskWorker._prepare_notion_book(host, _Session([row]), job, subtask)  # type: ignore[arg-type]


async def test_a_record_pointing_at_the_connected_folder_still_finds_the_artifact(
    tmp_path: Path,
) -> None:
    import hashlib
    from types import SimpleNamespace

    markdown = book_markdown()
    path = store_book(tmp_path / "artifacts", markdown)
    elsewhere = tmp_path / "folder" / "book (2).md"  # the copy in the user's connected folder
    elsewhere.parent.mkdir()
    elsewhere.write_text(markdown, encoding="utf-8")
    row = SimpleNamespace(
        id=uuid4(),
        file_name=elsewhere.name,
        storage_uri=str(elsewhere),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    prepared = await nb.prepare_book_write(
        artifacts_root=tmp_path / "artifacts",
        workspace_id=WS,
        task_id=TASK,
        input_data={"notion_book": {"parent_page_id": PAGE}},
        artifact_rows=[row],
    )
    args = prepared["tool_args"]
    assert args["content_artifact_id"] == str(row.id)  # the card can open it
    assert args["content_file"] == "book.md"  # execution reads MAYA's own copy
