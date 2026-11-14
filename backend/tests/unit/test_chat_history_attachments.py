"""A stored user turn keeps its attachment chips in the history the model reads (so a later
turn such as 'haan' can refer to the upload ids)."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.db.repositories.chat import ChatMessageRecord
from app.services.chat_service import _history_text

WHEN = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)


def _record(content: str, metadata: dict[str, Any]) -> ChatMessageRecord:
    return ChatMessageRecord(
        id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        sender="user",
        modality="text",
        content=content,
        metadata=metadata,
        created_at=WHEN,
    )


def test_a_user_turn_with_attachments_lists_each_chip_after_its_text() -> None:
    upload_id = "3f2c1a9e-5b7d-4c8e-9a0b-1d2e3f4a5b6c"
    record = _record(
        "Make the title bold",
        {"attachments": [{"id": upload_id, "name": "plan.docx", "kind": "docx"}]},
    )
    assert _history_text(record) == (f"Make the title bold\n[attached: plan.docx (id {upload_id})]")


def test_a_turn_without_attachments_is_unchanged() -> None:
    assert _history_text(_record("hello", {})) == "hello"
    assert _history_text(_record("hello", {"attachments": "bad"})) == "hello"


def test_malformed_chips_are_skipped() -> None:
    record = _record("see", {"attachments": [{"name": "no-id.txt"}, "junk"]})
    assert _history_text(record) == "see"
