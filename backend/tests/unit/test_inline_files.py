"""Inline chat files: the name sanitiser, the extension allowlist and exclusive chat storage."""

from pathlib import Path
from uuid import uuid4

import pytest

from app.services.artifact_service import ArtifactService
from app.services.inline_files import InlineFileError, sanitize_inline_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("notes.md", "notes.md"),
        ("../../etc/passwd.md", "passwd.md"),
        ("folder\\sub\\todo.txt", "todo.txt"),
        ("a<b>c.md", "a_b_c.md"),
        ("notes", "notes.md"),
        ("CON.txt", "_CON.txt"),
        ("My Report (final).csv", "My Report (final).csv"),
        (".hidden.md", "hidden.md"),
        ("x" * 300 + ".md", "x" * 100 + ".md"),
    ],
)
def test_names_are_plain_safe_and_keep_an_allowed_extension(raw: str, expected: str) -> None:
    assert sanitize_inline_name(raw) == expected


@pytest.mark.parametrize("raw", ["run.sh", "setup.exe", "image.svg", ".env", "notes."])
def test_types_outside_the_allowlist_are_refused(raw: str) -> None:
    with pytest.raises(InlineFileError):
        sanitize_inline_name(raw)


def test_a_chat_file_is_never_overwritten(tmp_path: Path) -> None:
    service = ArtifactService(storage_root=tmp_path)
    workspace, session = uuid4(), uuid4()
    first = service.store_chat_file(
        workspace_id=workspace, session_id=session, file_name="notes.md", data=b"one"
    )
    second = service.store_chat_file(
        workspace_id=workspace, session_id=session, file_name="notes.md", data=b"two"
    )
    assert first.name == "notes.md" and second.name == "notes (2).md"
    assert first.path.read_bytes() == b"one" and second.path.read_bytes() == b"two"
    assert second.sha256 != first.sha256 and second.size_bytes == 3
