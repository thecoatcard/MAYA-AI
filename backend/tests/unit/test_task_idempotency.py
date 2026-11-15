"""Unit tests for TaskIdempotencyService."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import PlanStatus, Task
from app.tasks.idempotency import TaskIdempotencyService


@pytest.mark.asyncio
async def test_idempotency_returns_existing_task() -> None:
    session = AsyncMock()
    ws_id = uuid4()
    u_id = uuid4()
    key = "idem-existing-123"

    existing_task = Task(
        id=uuid4(),
        workspace_id=ws_id,
        user_id=u_id,
        session_id="session-1",
        user_instruction="Instruction",
        idempotency_key=key,
        status="pending",
        plan_status=PlanStatus.PLANNING,
    )

    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = existing_task
    session.execute.return_value = mock_res

    task, is_new = await TaskIdempotencyService.get_or_create_task(
        session=session,
        workspace_id=ws_id,
        user_id=u_id,
        idempotency_key=key,
        user_instruction="Instruction",
    )

    assert task == existing_task
    assert not is_new
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_idempotency_creates_new_task() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    ws_id = uuid4()
    u_id = uuid4()
    key = "idem-new-456"

    # First lookup returns None
    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = None
    session.execute.return_value = mock_res

    task, is_new = await TaskIdempotencyService.get_or_create_task(
        session=session,
        workspace_id=ws_id,
        user_id=u_id,
        idempotency_key=key,
        user_instruction="New analysis",
        title="Custom Title",
        budget_usd=Decimal("2.50"),
    )

    assert is_new
    assert task.workspace_id == ws_id
    assert task.user_id == u_id
    assert task.idempotency_key == key
    assert task.title == "Custom Title"
    assert task.budget_usd == Decimal("2.50")
    assert session.add.call_count == 2
    assert session.add.call_args_list[1][0][0] == task
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_idempotency_creates_new_task_with_existing_session() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    ws_id = uuid4()
    u_id = uuid4()
    s_id = uuid4()
    key = "idem-new-with-session-789"

    mock_res_task = MagicMock()
    mock_res_task.scalars.return_value.first.return_value = None

    mock_res_sess = MagicMock()
    mock_res_sess.scalars.return_value.first.return_value = s_id

    session.execute = AsyncMock(side_effect=[mock_res_task, mock_res_sess])

    task, is_new = await TaskIdempotencyService.get_or_create_task(
        session=session,
        workspace_id=ws_id,
        user_id=u_id,
        idempotency_key=key,
        user_instruction="New analysis with session",
        title="Custom Title",
        budget_usd=Decimal("2.50"),
        session_id=str(s_id),
    )

    assert is_new
    assert task.workspace_id == ws_id
    assert task.user_id == u_id
    assert task.session_id == s_id
    assert task.idempotency_key == key
    session.add.assert_called_once_with(task)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_idempotency_concurrent_race_fallback() -> None:
    """When a concurrent request commits first, the second handles IntegrityError."""
    session = AsyncMock()
    session.add = MagicMock()
    ws_id = uuid4()
    u_id = uuid4()
    key = "idem-race-789"

    winner_task = Task(
        id=uuid4(),
        workspace_id=ws_id,
        user_id=u_id,
        session_id="session-race",
        user_instruction="Concurrent instruction",
        idempotency_key=key,
        status="pending",
        plan_status=PlanStatus.PLANNING,
    )

    # 1st lookup: None
    mock_res_empty = MagicMock()
    mock_res_empty.scalars.return_value.first.return_value = None

    # 2nd lookup after IntegrityError: returns winner_task
    mock_res_winner = MagicMock()
    mock_res_winner.scalars.return_value.first.return_value = winner_task

    session.execute = AsyncMock(side_effect=[mock_res_empty, mock_res_winner])
    session.flush = AsyncMock(
        side_effect=IntegrityError("duplicate key", params=None, orig=Exception())
    )

    task, is_new = await TaskIdempotencyService.get_or_create_task(
        session=session,
        workspace_id=ws_id,
        user_id=u_id,
        idempotency_key=key,
        user_instruction="Concurrent instruction",
    )

    assert not is_new
    assert task == winner_task
    session.rollback.assert_awaited_once()
