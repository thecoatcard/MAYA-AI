"""The file tools: tiers, deny-by-default registration, scope-bound folder, path safety, limits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.mcp.file_tools import (
    FILE_HANDLERS,
    FILE_MANIFEST,
    file_edit_as_new_version,
    file_list,
    file_read,
    file_replace_original,
    file_write_new,
    reset_write_counters,
)
from app.mcp.permissions import DEFAULT_TOOL_PERMISSIONS, ToolPermissionRegistry, ToolTier
from app.mcp.scope import tool_call_scope
from app.mcp.transports.base import MCPError
from tests.support.files import OTHER_WORKSPACE, USER, WORKSPACE, make_docx


@pytest.fixture(autouse=True)
def _counters() -> None:
    reset_write_counters()


def _scope(task_id: Any = None, workspace: Any = WORKSPACE, user: Any = USER) -> Any:
    return tool_call_scope(workspace, task_id or uuid4(), user)


FILE_TOOL_TIERS = {
    "file_list": ToolTier.READ,
    "file_read": ToolTier.READ,
    "file_write_new": ToolTier.WRITE,
    "file_edit_as_new_version": ToolTier.WRITE,
    "file_replace_original": ToolTier.APPROVAL_REQUIRED,
}


def test_tiers_and_manifest_match() -> None:
    for name, tier in FILE_TOOL_TIERS.items():
        assert DEFAULT_TOOL_PERMISSIONS[name] == tier
        assert name in FILE_HANDLERS
    assert {t["name"] for t in FILE_MANIFEST} == set(FILE_TOOL_TIERS)


def test_there_is_no_delete_or_move_tool() -> None:
    names = {t["name"] for t in FILE_MANIFEST} | set(FILE_HANDLERS)
    assert not [n for n in names if any(w in n for w in ("delete", "remove", "move", "rename"))]


def test_unregistered_file_tools_are_denied_by_default() -> None:
    registry = ToolPermissionRegistry(initial_permissions={})
    for name in ("file_delete", "file_move", "file_rename", *FILE_TOOL_TIERS):
        assert not registry.is_registered(name)


def test_gateway_serves_the_file_tools() -> None:
    from app.mcp.gateway import BUILTIN_MANIFEST

    served = {str(t["name"]) for t in BUILTIN_MANIFEST}
    assert set(FILE_TOOL_TIERS) <= served


def test_manifest_schemas_do_not_accept_a_folder_or_user() -> None:
    for tool in FILE_MANIFEST:
        props = set(tool["inputSchema"]["properties"])
        assert not props & {"folder", "root", "workspace_id", "user_id", "directory", "dir"}


async def test_list_and_read(file_env: Any) -> None:
    _services, folder = file_env
    (folder / "a.txt").write_text("hello world")
    (folder / "sub").mkdir()
    with _scope():
        listing = json.loads(await file_list({}))
        assert {e["name"] for e in listing["entries"]} == {"a.txt", "sub"}
        read = json.loads(await file_read({"path": "a.txt"}))
    assert read["text"] == "hello world" and read["untrusted_content"] is True


async def test_read_is_bounded(file_env: Any) -> None:
    _services, folder = file_env
    (folder / "big.txt").write_text("z" * 100_000)
    with _scope():
        out = json.loads(await file_read({"path": "big.txt", "max_chars": 300}))
    assert len(out["text"]) == 300 and out["truncated"] is True


@pytest.mark.parametrize(
    "bad",
    [
        "../x.txt",
        "..\\x.txt",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "a/../../x",
        "con.txt",
        "x\x00",
    ],
)
async def test_paths_from_arguments_cannot_escape(file_env: Any, bad: str) -> None:
    _services, folder = file_env
    with _scope():
        for call, args in (
            (file_read, {"path": bad}),
            (file_list, {"path": bad}),
            (file_write_new, {"path": bad, "content": "x"}),
            (file_edit_as_new_version, {"path": bad, "content": "x"}),
            (file_replace_original, {"path": bad, "new_version": "a.txt"}),
            (file_replace_original, {"path": "a.txt", "new_version": bad}),
        ):
            with pytest.raises(MCPError):
                await call(args)
    assert not list(folder.parent.glob("x.txt"))


async def test_write_new_creates_but_never_overwrites(file_env: Any) -> None:
    services, folder = file_env
    with _scope():
        out = json.loads(await file_write_new({"path": "docs/a.md", "content": "# hi"}))
        assert out["created"] == "docs/a.md"
        assert (folder / "docs" / "a.md").read_text() == "# hi"
        with pytest.raises(MCPError, match="already exists"):
            await file_write_new({"path": "docs/a.md", "content": "other"})
    assert (folder / "docs" / "a.md").read_text() == "# hi"
    assert services.audits[-1]["tool"] == "file_write_new"


async def test_write_new_limits(file_env: Any) -> None:
    with _scope():
        with pytest.raises(MCPError, match="5 MB"):
            await file_write_new({"path": "big.txt", "content": "x" * (5 * 1024 * 1024 + 1)})
        with pytest.raises(MCPError, match="plain text"):
            await file_write_new({"path": "run.exe", "content": "x"})
        with pytest.raises(MCPError):
            await file_write_new({"path": "a.txt", "content": 5})


async def test_write_new_caps_files_per_task(file_env: Any) -> None:
    task = uuid4()
    with _scope(task):
        for index in range(300):
            await file_write_new({"path": f"f{index}.txt", "content": "x"})
        with pytest.raises(MCPError, match="At most 300"):
            await file_write_new({"path": "one-too-many.txt", "content": "x"})
    with _scope(uuid4()):  # another task has its own budget
        await file_write_new({"path": "other.txt", "content": "x"})


async def test_edit_as_new_version_and_replace_original(file_env: Any) -> None:
    services, folder = file_env
    (folder / "a.txt").write_text("old\n")
    with _scope():
        # file_edit_as_new_version updates existing folder files in place with backup
        edited = json.loads(await file_edit_as_new_version({"path": "a.txt", "content": "new\n"}))
        assert edited["new_version"] == "a.txt"
        assert (folder / "a.txt").read_text() == "new\n"
        assert (folder / ".maya-backups").is_dir()

        # file_replace_original replaces a target with another file
        (folder / "candidate.txt").write_text("latest\n")
        replaced = json.loads(
            await file_replace_original({"path": "a.txt", "new_version": "candidate.txt"})
        )
    assert replaced["status"] == "replaced"
    assert (folder / "a.txt").read_text() == "latest\n"
    assert (folder / replaced["backup"]).read_text() == "new\n"
    assert services.audits[-1]["tool"] == "file_replace_original"


async def test_edit_docx_through_the_tool(file_env: Any) -> None:
    from docx import Document

    _services, folder = file_env
    make_docx(folder / "a.docx", ["One", "Two"])
    with _scope():
        out = json.loads(
            await file_edit_as_new_version(
                {"path": "a.docx", "edits": [{"paragraph_index": 0, "new_text": "Uno"}]}
            )
        )
    assert out["new_version"] == "a.docx"
    assert [p.text for p in Document(str(folder / "a.docx")).paragraphs] == ["Uno", "Two"]


async def test_the_folder_comes_from_the_scope_not_the_arguments(file_env: Any) -> None:
    services, folder = file_env
    (folder / "a.txt").write_text("mine")
    with _scope(workspace=OTHER_WORKSPACE):  # a different tenant has no folder connected
        with pytest.raises(MCPError, match="No usable folder"):
            await file_read({"path": "a.txt", "folder": str(folder)})
    with tool_call_scope(WORKSPACE, uuid4(), None):  # no user in scope
        with pytest.raises(MCPError):
            await file_list({})


async def test_tools_are_unavailable_without_file_services() -> None:
    with _scope(), pytest.raises(MCPError, match="not available"):
        await file_list({})


async def test_symlink_in_folder_is_not_listed_or_read(file_env: Any, tmp_path: Path) -> None:
    import os

    _services, folder = file_env
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    try:
        os.symlink(outside, folder / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("this OS refused to create a symlink")
    with _scope():
        names = [e["name"] for e in json.loads(await file_list({}))["entries"]]
        assert "link.txt" not in names
        with pytest.raises(MCPError):
            await file_read({"path": "link.txt"})


async def test_file_read_of_an_upload_is_scoped_to_the_workspace(file_env: Any) -> None:
    services, _folder = file_env
    record = services.uploads.save(WORKSPACE, USER, "u.txt", b"upload text")
    with _scope():
        out = json.loads(await file_read({"upload_id": record.id}))
    assert out["text"] == "upload text"
    with _scope(workspace=OTHER_WORKSPACE), pytest.raises(MCPError):
        await file_read({"upload_id": record.id})
