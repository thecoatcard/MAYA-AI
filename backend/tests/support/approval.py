"""Simulate a human approval for a paused subtask in worker tests (real token, mocked DB claim)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.db.models import Subtask, Task
from app.mcp.approvals import compute_payload_hash
from app.mcp.tokens import mint_approval_token

TEST_JWT_KEY = "a" * 32


def approve_paused_subtask(session: Any, subtask: Subtask, task: Task) -> None:
    """Mint the signed token the approvals API would store, and let the atomic claim succeed.

    Uses the approval binding the worker persisted when it paused (``approval_tool``,
    ``approval_args``, ``idempotency_key``), so the test exercises the real verification.
    """
    data = dict(subtask.input_data or {})
    request_id = uuid4()
    tool = str(data["approval_tool"])
    token, _ = mint_approval_token(
        TEST_JWT_KEY,
        user_id=task.user_id,
        workspace_id=subtask.workspace_id,
        request_id=request_id,
        tool_name=tool,
        payload_hash=compute_payload_hash(
            tool=tool,
            args=data["approval_args"],
            workspace_id=subtask.workspace_id,
            user_id=task.user_id,
            idempotency_key=str(data.get("idempotency_key") or subtask.id),
        ),
    )
    data["approval_token"] = token
    subtask.input_data = data
    claim = MagicMock()
    claim.first.return_value = (request_id,)
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=claim)
    conn.info = {}
    session.connection = AsyncMock(return_value=conn)
