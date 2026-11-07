"""Voice-to-Task Decoupling Event Bridge (Decision D3).

Subscribes to tenant Redis task events, filters for the session's active tasks,
and translates task lifecycle transitions into spoken natural language announcements
that can be injected into Gemini Live via send_client_content.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.tasks.events import (
    DEFAULT_EVENT_CHANNEL_PREFIX,
    TaskEvent,
    TaskEventType,
    get_event_channel,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpokenTaskAnnouncement:
    """Spoken notification queued for injection into a live voice session."""

    task_id: str
    event_type: str
    spoken_text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


def format_spoken_task_update(event_type: str | TaskEventType, payload: dict[str, Any]) -> str:
    """Format a task lifecycle event payload into spoken language."""
    ev_type = str(event_type).lower()

    if ev_type in (
        str(TaskEventType.TASK_PLAN_CREATED).lower(),
        str(TaskEventType.PLANNING_STARTED).lower(),
    ):
        steps = payload.get("subtask_count", payload.get("steps_count", 0))
        intent = payload.get("intent", payload.get("instruction", "your request"))
        return f"I've created a plan with {steps} steps for: {intent}."

    if ev_type == str(TaskEventType.SUBTASK_DISPATCHED).lower():
        desc = payload.get("description", payload.get("title", "the next action"))
        return f"Starting step: {desc}."

    if ev_type == str(TaskEventType.SUBTASK_STATUS_CHANGED).lower():
        desc = payload.get("description", payload.get("title", "Step"))
        status = payload.get("status", "")
        if status == "completed":
            return f"Step completed: {desc}."
        return f"Step status: {status} for {desc}."

    if ev_type in (
        str(TaskEventType.TASK_PAUSED_APPROVAL).lower(),
        str(TaskEventType.AWAITING_APPROVAL).lower(),
    ):
        action = payload.get("action_description", payload.get("tool_name", "a sensitive action"))
        return f"Awaiting your approval for {action} in the web interface."

    if ev_type == str(TaskEventType.TASK_COMPLETED).lower():
        return "Your task has completed successfully."

    if ev_type == str(TaskEventType.TASK_FAILED).lower():
        err = payload.get("error", payload.get("error_message", "unknown failure"))
        return f"Task encountered an issue: {err}."

    return "Task progress updated."


class VoiceEventBridge:
    """Bridges Redis task event bus to real-time voice spoken announcements."""

    def __init__(
        self,
        workspace_id: UUID,
        redis_client: Redis | None = None,
        channel_prefix: str = DEFAULT_EVENT_CHANNEL_PREFIX,
    ) -> None:
        self.workspace_id = workspace_id
        self._redis = redis_client
        self._channel_prefix = channel_prefix
        self._tracked_tasks: set[str] = set()
        self._queue: asyncio.Queue[SpokenTaskAnnouncement] = asyncio.Queue()
        self._listener_task: asyncio.Task[None] | None = None
        self._closed = False

    def track_task(self, task_id: str) -> None:
        """Register a task ID to track for spoken notifications."""
        self._tracked_tasks.add(str(task_id))

    def untrack_task(self, task_id: str) -> None:
        """Unregister a task ID."""
        self._tracked_tasks.discard(str(task_id))

    def is_tracking(self, task_id: str) -> bool:
        """Check if a task ID is tracked."""
        return str(task_id) in self._tracked_tasks

    @property
    def announcement_count(self) -> int:
        """Number of announcements waiting in the queue."""
        return self._queue.qsize()

    async def get_next_announcement(self) -> SpokenTaskAnnouncement:
        """Retrieve the next queued announcement."""
        return await self._queue.get()

    async def handle_event(self, event: TaskEvent | dict[str, Any]) -> None:
        """Process an event, filter for tracked tasks, and queue spoken text."""
        if isinstance(event, TaskEvent):
            event_type = event.event_type
            payload = event.payload
        else:
            event_type = event.get("event_type", "")
            payload = event.get("payload", {})

        task_id = str(
            payload.get("task_id")
            or payload.get("id")
            or (event.session_id if isinstance(event, TaskEvent) else "")
        )

        if not self.is_tracking(task_id):
            return

        spoken_text = format_spoken_task_update(event_type, payload)
        announcement = SpokenTaskAnnouncement(
            task_id=task_id,
            event_type=str(event_type),
            spoken_text=spoken_text,
        )
        await self._queue.put(announcement)

    async def start_listening(self) -> None:
        """Start listening on the workspace channel if Redis is available."""
        if self._redis is None or self._listener_task is not None:
            return

        redis_client = self._redis
        channel_name = get_event_channel(self.workspace_id, prefix=self._channel_prefix)

        async def _listen() -> None:
            try:
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(channel_name)
                logger.info("voice_bridge_subscribed channel=%s", channel_name)
                while not self._closed:
                    try:
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=1.0
                        )
                        if message and message.get("type") == "message":
                            raw_data = message.get("data")
                            if isinstance(raw_data, bytes):
                                raw_data = raw_data.decode("utf-8")
                            parsed = json.loads(raw_data)
                            event = TaskEvent.model_validate(parsed)
                            await self.handle_event(event)
                    except asyncio.CancelledError:
                        break
                    except Exception as err:
                        logger.warning("voice_bridge_listen_error: %s", err)
                        await asyncio.sleep(0.5)
            finally:
                try:
                    await pubsub.unsubscribe(channel_name)
                    await pubsub.close()
                except Exception:
                    pass

        self._listener_task = asyncio.create_task(_listen())

    async def close(self) -> None:
        """Close subscriber and clear queue."""
        self._closed = True
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None
