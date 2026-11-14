"""A single approval row must never break the whole approval list (a real 500 on the owner's run).

An e-mail approval stores a plain-text diff in a JSONB column. The driver hands such a value back
as a ``str`` that is not JSON text, and the old record builder ran ``json.loads`` on it, so the list
endpoint raised ``JSONDecodeError`` and every inline approval card said "Failed to list approval
requests".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.db.repositories.approvals import _decode_json, _row_to_record


def row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid4(),
        "workspace_id": uuid4(),
        "user_id": uuid4(),
        "task_id": uuid4(),
        "subtask_id": uuid4(),
        "tool_name": "mail_send",
        "tool_args": {"to": "a@example.com"},
        "payload_hash": "h" * 64,
        "tier": "approval_required",
        "action_description": "Send a mail",
        "payload_diff": {"to": "a@example.com"},
        "status": "pending",
        "approval_token": None,
        "token_jti": None,
        "requested_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC),
        "decided_at": None,
        "consumed_at": None,
        "decision_channel": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_a_plain_text_diff_is_kept_as_text_not_parsed() -> None:
    text = "--- original\n+++ new\n+Happy Diwali!"
    record = _row_to_record(row(payload_diff=text))
    assert record.payload_diff == text


def test_json_text_and_already_decoded_values_both_work() -> None:
    assert _row_to_record(row(payload_diff=json.dumps({"a": 1}))).payload_diff == {"a": 1}
    assert _row_to_record(row(payload_diff={"a": 1})).payload_diff == {"a": 1}
    assert _row_to_record(row(payload_diff=None)).payload_diff is None
    assert _row_to_record(row(payload_diff=json.dumps([1, 2]))).payload_diff == [1, 2]


def test_tool_args_that_are_not_an_object_become_an_empty_object() -> None:
    assert _row_to_record(row(tool_args="not json at all")).tool_args == {}
    assert _row_to_record(row(tool_args=json.dumps(["x"]))).tool_args == {}
    assert _row_to_record(row(tool_args=json.dumps({"k": "v"}))).tool_args == {"k": "v"}


def test_a_bad_row_among_good_rows_does_not_stop_the_others() -> None:
    rows = [row(payload_diff="plain text"), row(), row(payload_diff=json.dumps({"x": 2}))]
    records = [_row_to_record(r) for r in rows]
    assert [r.payload_diff for r in records] == ["plain text", {"to": "a@example.com"}, {"x": 2}]


def test_decode_json_leaves_non_strings_alone() -> None:
    assert _decode_json(5) == 5
    assert _decode_json(None) is None
    assert _decode_json("") == ""
