"""Unit tests for Transactional Outbox and OutboxDispatcher."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import TaskOutboxEvent
from app.tasks.events import TaskEventType
from app.tasks.outbox import OutboxDispatcher, write_outbox_event


@pytest.mark.asyncio
async def test_write_outbox_event() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    ws_id = uuid4()
    t_id = uuid4()

    event = await write_outbox_event(
        session=session,
        workspace_id=ws_id,
        task_id=t_id,
        event_type=TaskEventType.SUBTASK_DISPATCHED,
        payload={"subtask_id": "step_1", "agent": "coding"},
    )

    assert event.workspace_id == ws_id
    assert event.task_id == t_id
    assert event.event_type == TaskEventType.SUBTASK_DISPATCHED.value
    assert event.status == "pending"
    session.add.assert_called_once_with(event)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbox_dispatcher_relays_pending_events() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    publisher = AsyncMock()
    publisher.publish.return_value = True

    evt1 = TaskOutboxEvent(
        id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        event_type="subtask_dispatched",
        payload={"subtask_id": "1"},
        status="pending",
    )
    evt2 = TaskOutboxEvent(
        id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        event_type="subtask_status_changed",
        payload={"subtask_id": "1", "status": "completed"},
        status="pending",
    )

    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [evt1, evt2]
    session.execute.return_value = mock_res

    dispatcher = OutboxDispatcher(publisher=publisher)
    dispatched = await dispatcher.dispatch_pending_events(session=session)

    assert dispatched == 2
    assert evt1.status == "dispatched"
    assert evt1.dispatched_at is not None
    assert evt2.status == "dispatched"
    assert evt2.dispatched_at is not None
    assert publisher.publish.await_count == 2
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbox_dispatcher_marks_dispatched_with_zero_subscribers() -> None:
    """Verifies that Redis returning integer 0 (no live subscribers) still marks event."""
    session = AsyncMock()
    session.add = MagicMock()
    publisher = AsyncMock()
    publisher.publish.return_value = 0

    evt = TaskOutboxEvent(
        id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        event_type="task_plan_created",
        payload={"task_id": "abc"},
        status="pending",
    )

    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [evt]
    session.execute.return_value = mock_res

    dispatcher = OutboxDispatcher(publisher=publisher)
    dispatched = await dispatcher.dispatch_pending_events(session=session)

    assert dispatched == 1
    assert evt.status == "dispatched"
    assert evt.dispatched_at is not None


@pytest.mark.asyncio
async def test_outbox_dispatcher_keeps_pending_when_redis_fails() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    publisher = AsyncMock()
    # Explicit False return indicates failure
    publisher.publish.return_value = False

    evt = TaskOutboxEvent(
        id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        event_type="task_plan_created",
        payload={"task_id": "abc"},
        status="pending",
    )

    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [evt]
    session.execute.return_value = mock_res

    dispatcher = OutboxDispatcher(publisher=publisher)
    dispatched = await dispatcher.dispatch_pending_events(session=session)

    assert dispatched == 0
    assert evt.status == "pending"
    assert evt.dispatched_at is None


@pytest.mark.asyncio
async def test_outbox_dispatcher_handles_connection_error() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    publisher = AsyncMock()
    publisher.publish.side_effect = ConnectionError("Redis offline")

    evt = TaskOutboxEvent(
        id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        event_type="task_plan_created",
        payload={"task_id": "abc"},
        status="pending",
    )

    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [evt]
    session.execute.return_value = mock_res

    dispatcher = OutboxDispatcher(publisher=publisher)
    dispatched = await dispatcher.dispatch_pending_events(session=session)

    assert dispatched == 0
    assert evt.status == "pending"
    assert evt.dispatched_at is None
