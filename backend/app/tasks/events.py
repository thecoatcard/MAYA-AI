"""Redis Pub/Sub event bus for task and subtask execution lifecycle events."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class TaskEventType(StrEnum):
    """Supported task lifecycle transition event types."""

    PLANNING_STARTED = "planning_started"
    SUBTASK_DISPATCHED = "subtask_dispatched"
    AWAITING_APPROVAL = "awaiting_approval"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"

    # Aliases
    TASK_PLAN_CREATED = "planning_started"
    SUBTASK_STATUS_CHANGED = "subtask_dispatched"


class TaskEvent(BaseModel):
    """Schema for task events published across Redis Pub/Sub."""

    event_type: TaskEventType
    session_id: str
    workspace_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Event timestamp must be timezone-aware")
        return value.astimezone(UTC)


def get_task_channel(workspace_id: str | UUID) -> str:
    """Derive Redis Pub/Sub channel for workspace task events."""
    return f"maya:tasks:{workspace_id}"


class TaskEventPublisher:
    """Publishes validated task lifecycle events to tenant-isolated Redis Pub/Sub channels."""

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    async def publish(self, event: TaskEvent) -> int:
        """Publish a TaskEvent as JSON to the workspace channel.

        Returns:
            The number of clients that received the message.
        """
        channel = get_task_channel(event.workspace_id)
        message = event.model_dump_json()
        logger.debug("publishing_task_event channel=%s event_type=%s", channel, event.event_type)
        return await self._redis.publish(channel, message)

    async def publish_planning_started(
        self,
        session_id: str,
        workspace_id: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        event = TaskEvent(
            event_type=TaskEventType.PLANNING_STARTED,
            session_id=session_id,
            workspace_id=workspace_id,
            payload=payload or {},
        )
        return await self.publish(event)

    async def publish_subtask_dispatched(
        self,
        session_id: str,
        workspace_id: str,
        subtask_id: str,
        assigned_agent: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        data = {**(payload or {}), "subtask_id": subtask_id, "assigned_agent": assigned_agent}
        event = TaskEvent(
            event_type=TaskEventType.SUBTASK_DISPATCHED,
            session_id=session_id,
            workspace_id=workspace_id,
            payload=data,
        )
        return await self.publish(event)

    async def publish_awaiting_approval(
        self,
        session_id: str,
        workspace_id: str,
        subtask_id: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        data = {**(payload or {}), "subtask_id": subtask_id}
        event = TaskEvent(
            event_type=TaskEventType.AWAITING_APPROVAL,
            session_id=session_id,
            workspace_id=workspace_id,
            payload=data,
        )
        return await self.publish(event)

    async def publish_task_completed(
        self,
        session_id: str,
        workspace_id: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        event = TaskEvent(
            event_type=TaskEventType.TASK_COMPLETED,
            session_id=session_id,
            workspace_id=workspace_id,
            payload=payload or {},
        )
        return await self.publish(event)

    async def publish_task_failed(
        self,
        session_id: str,
        workspace_id: str,
        error_message: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        data = {**(payload or {}), "error": error_message}
        event = TaskEvent(
            event_type=TaskEventType.TASK_FAILED,
            session_id=session_id,
            workspace_id=workspace_id,
            payload=data,
        )
        return await self.publish(event)
