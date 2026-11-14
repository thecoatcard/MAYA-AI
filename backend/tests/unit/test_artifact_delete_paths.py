"""Path rules behind artifact deletion: MAYA deletes only inside its own storage."""

from __future__ import annotations

from pathlib import Path

from app.api.v1.artifacts import _remove_file, is_in_managed_storage


def test_managed_storage_is_inside_root_only(tmp_path: Path) -> None:
    root = tmp_path / "storage" / "artifacts"
    inside = root / "ws" / "task" / "a.pdf"
    folder_file = tmp_path / "user-folder" / "site" / "index.html"
    sneaky = root / ".." / ".." / "user-folder" / "x.txt"

    assert is_in_managed_storage(str(inside), root) is True
    assert is_in_managed_storage(str(folder_file), root) is False
    assert is_in_managed_storage(str(sneaky), root) is False
    assert is_in_managed_storage(str(root.parent / "artifacts-evil" / "a.pdf"), root) is False


def test_remove_file_is_quiet_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x")
    _remove_file(target)
    assert not target.exists()
    _remove_file(target)  # already gone: no error
