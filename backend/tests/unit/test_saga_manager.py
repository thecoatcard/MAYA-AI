"""Unit tests for SagaManager compensation engine."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import Compensation, PlanStatus, Task
from app.tasks.saga import SagaManager


@pytest.mark.asyncio
async def test_register_reversible_compensation() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    ws_id = uuid4()
    t_id = uuid4()
    st_id = uuid4()

    comp = await SagaManager.register_compensation(
        session=session,
        workspace_id=ws_id,
        task_id=t_id,
        subtask_id=st_id,
        forward_tool="github_create_branch",
        forward_ref={"branch": "feat/my-feature"},
        compensating_tool="github_delete_branch",
        compensating_args={"branch": "feat/my-feature"},
        permission_level="CONTROLLED_WRITE",
    )

    assert comp.workspace_id == ws_id
    assert comp.task_id == t_id
    assert comp.subtask_id == st_id
    assert comp.forward_tool == "github_create_branch"
    assert comp.compensating_tool == "github_delete_branch"
    assert comp.status == "registered"
    session.add.assert_called_once_with(comp)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_irreversible_compensation() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    ws_id = uuid4()
    t_id = uuid4()

    comp = await SagaManager.register_compensation(
        session=session,
        workspace_id=ws_id,
        task_id=t_id,
        forward_tool="gmail_send",
        forward_ref={"message_id": "msg-123"},
        compensating_tool=None,
    )

    assert comp.forward_tool == "gmail_send"
    assert comp.compensating_tool is None
    assert comp.compensating_args is None
    assert comp.status == "registered"


@pytest.mark.asyncio
async def test_execute_rollback_order_and_irreversible_handling() -> None:
    ws_id = uuid4()
    t_id = uuid4()

    # Reversible compensation 1 (older)
    comp1 = Compensation(
        id=uuid4(),
        workspace_id=ws_id,
        task_id=t_id,
        forward_tool="notion_create_page",
        forward_ref={"page_id": "page-1"},
        compensating_tool="notion_archive_page",
        compensating_args={"page_id": "page-1"},
        permission_level="CONTROLLED_WRITE",
        status="registered",
    )

    # Irreversible compensation 2 (newer)
    comp2 = Compensation(
        id=uuid4(),
        workspace_id=ws_id,
        task_id=t_id,
        forward_tool="gmail_send",
        forward_ref={"to": "client@example.com"},
        compensating_tool=None,
        compensating_args=None,
        permission_level="APPROVAL_REQUIRED",
        status="registered",
    )

    task = Task(
        id=t_id,
        workspace_id=ws_id,
        status="executing",
        plan_status=PlanStatus.EXECUTING,
    )

    session = AsyncMock()

    # Mock execute for list_compensations (reverse order: comp2, then comp1)
    # and execute for task lookup
    async def mock_execute(stmt: Any) -> MagicMock:
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "FROM compensations" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [comp2, comp1]
        elif "FROM tasks" in stmt_str:
            mock_result.scalars.return_value.first.return_value = task
        return mock_result

    session.execute = AsyncMock(side_effect=mock_execute)

    executed_tools: list[str] = []

    async def dummy_executor(tool: str, args: dict[str, Any]) -> bool:
        executed_tools.append(tool)
        return True

    res = await SagaManager.execute_rollback(
        session=session,
        workspace_id=ws_id,
        task_id=t_id,
        tool_executor=dummy_executor,
    )

    # comp2 was irreversible, marked not_applicable
    assert comp2.status == "not_applicable"
    assert res.not_applicable == 1
    assert len(res.notifications) == 1
    assert "gmail_send" in res.notifications[0]

    # comp1 was executed
    assert comp1.status == "executed"
    assert res.executed == 1
    assert executed_tools == ["notion_archive_page"]

    # task was marked cancelled
    assert task.status == "cancelled"
    assert task.plan_status == PlanStatus.FAILED
    assert res.is_successful
