"""Transactional outbox implementation for durable task events."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TaskOutboxEvent
from app.tasks.events import TaskEvent, TaskEventPublisher, TaskEventType

logger = logging.getLogger(__name__)


async def write_outbox_event(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    event_type: str | TaskEventType,
    payload: dict[str, Any],
) -> TaskOutboxEvent:
    """Persist an outbox event within the caller's active database transaction.

    This ensures that database state changes (e.g. task/subtask status updates)
    and their corresponding events are committed atomically.
    """
    evt_type_str = event_type.value if isinstance(event_type, TaskEventType) else str(event_type)
    outbox_event = TaskOutboxEvent(
        workspace_id=workspace_id,
        task_id=task_id,
        event_type=evt_type_str,
        payload=payload,
        status="pending",
    )
    session.add(outbox_event)
    await session.flush()
    return outbox_event


class OutboxDispatcher:
    """Background dispatcher that relays pending outbox events to Redis Pub/Sub."""

    def __init__(self, publisher: TaskEventPublisher) -> None:
        self.publisher = publisher

    async def dispatch_pending_events(
        self,
        session: AsyncSession,
        batch_size: int = 50,
    ) -> int:
        """Poll pending outbox events and relay them to Redis Pub/Sub.

        Uses SELECT ... FOR UPDATE SKIP LOCKED to prevent duplicate processing
        across multiple dispatcher workers.
        """
        stmt = (
            select(TaskOutboxEvent)
            .where(TaskOutboxEvent.status == "pending")
            .order_by(TaskOutboxEvent.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(stmt)
        events = list(result.scalars().all())

        if not events:
            return 0

        dispatched_count = 0
        for event in events:
            try:
                created_dt = event.created_at or datetime.now(UTC)
                task_event = TaskEvent(
                    event_type=TaskEventType(event.event_type)
                    if event.event_type in TaskEventType._value2member_map_
                    else TaskEventType.SUBTASK_STATUS_CHANGED,
                    session_id=str(event.task_id),
                    workspace_id=str(event.workspace_id),
                    payload=event.payload,
                    timestamp=created_dt if created_dt.tzinfo else created_dt.replace(tzinfo=UTC),
                )
                published = await self.publisher.publish(task_event)
                # Treat True or non-bool int (including 0 subscribers) as successful Redis dispatch
                is_success = (published is True) or (
                    isinstance(published, int) and not isinstance(published, bool)
                )
                if is_success:
                    event.status = "dispatched"
                    event.dispatched_at = datetime.now(UTC)
                    dispatched_count += 1
                else:
                    # Redis failed or disconnected; leave as pending for retry
                    logger.warning(
                        "Outbox event %s could not be published to Redis; keeping pending",
                        event.id,
                    )
            except Exception as exc:
                logger.error(
                    "Error dispatching outbox event %s: %s",
                    event.id,
                    exc,
                    exc_info=True,
                )
                # Keep as pending or mark failed if permanent error
                break

        await session.flush()
        return dispatched_count
