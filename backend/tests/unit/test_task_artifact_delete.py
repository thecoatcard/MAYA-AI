"""Deleting files and tasks: storage safety and working-state pruning (no database)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.services.chat_service import ChatService
from app.services.document_tools import WorkingState
from app.services.task_deletion import (
    MAX_BULK_DELETE,
    _remove_folder,
    managed_file,
    remove_file,
)


def test_a_file_inside_storage_is_managed(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    target = root / "ws" / "task" / "a.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    assert managed_file(str(target), root) == target.resolve()


def test_dot_dot_and_outside_paths_are_never_managed(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "user-folder" / "mine.pdf"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    assert managed_file(str(outside), root) is None
    assert managed_file(str(root / ".." / "user-folder" / "mine.pdf"), root) is None
    assert managed_file(str(root / "ws" / ".." / ".." / "user-folder" / "mine.pdf"), root) is None


def test_a_symlink_leading_outside_storage_is_not_managed_and_target_survives(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "user-folder" / "mine.pdf"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    link = root / "link.pdf"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    assert managed_file(str(link), root) is None
    assert outside.exists()


def test_remove_file_is_quiet_when_the_file_is_already_gone(tmp_path: Path) -> None:
    gone = tmp_path / "missing.txt"
    assert remove_file(gone) is True
    present = tmp_path / "here.txt"
    present.write_text("x")
    assert remove_file(present) is True and not present.exists()


def test_chat_folder_removal_only_takes_regular_files(tmp_path: Path) -> None:
    folder = tmp_path / "chat" / "s1"
    folder.mkdir(parents=True)
    (folder / "a.md").write_text("a")
    (folder / "b.md").write_text("b")
    assert _remove_folder(folder) == 2
    assert not folder.exists()


def test_bulk_cap_is_one_hundred() -> None:
    assert MAX_BULK_DELETE == 100


def _service(root: Path) -> Any:
    return SimpleNamespace(_wiring=SimpleNamespace(artifacts=SimpleNamespace(storage_root=root)))


def test_working_state_drops_a_deleted_file_and_the_document_that_pointed_at_it(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    kept = root / "ws" / "chat" / "s" / "keep.md"
    kept.parent.mkdir(parents=True)
    kept.write_text("k")
    gone_id, kept_id = str(uuid4()), str(uuid4())
    state = WorkingState(
        document={"format": "docx", "title": "Report", "file_id": gone_id, "spec": {"a": 1}},
        files=[
            {"file_id": gone_id, "storage_key": "ws/chat/s/deleted.docx", "name": "deleted.docx"},
            {"file_id": kept_id, "storage_key": "ws/chat/s/keep.md", "name": "keep.md"},
        ],
        jobs=["j1"],
    )
    result = ChatService._without_deleted_files(_service(root), state)
    assert [f["file_id"] for f in result.files] == [kept_id]
    assert result.document is None
    assert result.find_file(gone_id) is None
    assert result.jobs == ["j1"]


def test_working_state_is_unchanged_when_every_file_still_exists(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "a.md").write_text("a")
    state = WorkingState(files=[{"file_id": "1", "storage_key": "a.md"}])
    assert ChatService._without_deleted_files(_service(root), state) is state
