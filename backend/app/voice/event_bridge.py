"""Voice-to-Task Decoupling Event Bridge (Decision D3).

Subscribes to tenant Redis task events, filters for the session's active tasks,
and translates task lifecycle transitions into spoken natural language announcements
that can be injected into Gemini Live via send_client_content. Task text that reaches the model
is sanitised (it can contain tool output, which is untrusted).
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
from app.voice.gemini_live import sanitize_injected_text

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from app.voice.task_watch import TaskAnnouncer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpokenTaskAnnouncement:
    """Spoken notification queued for injection into a live voice session."""

    task_id: str
    event_type: str
    spoken_text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = field(default_factory=dict)
    # Only milestones are spoken; every event is still forwarded to the client as TASK_EVENT.
    speak: bool = True


def _clean(value: Any, default: str) -> str:
    cleaned = sanitize_injected_text(str(value)) if value else ""
    return cleaned or default


# Milestones only: a document, an email or an image that is ready. Other steps are not spoken.
_SPOKEN_STEP_KINDS = frozenset({"render", "email", "image"})


def _spoken_step(payload: dict[str, Any]) -> str | None:
    """One short sentence for a finished milestone step of a task, or None to stay silent."""
    step = payload.get("step")
    if not isinstance(step, dict) or step.get("state") != "done":
        return None
    if step.get("kind") not in _SPOKEN_STEP_KINDS:
        return None
    return f"Finished: {_clean(step.get('label'), 'a step')}."


def _should_speak(event_type: str, payload: dict[str, Any]) -> bool:
    ev = str(event_type).lower()
    if ev == str(TaskEventType.SUBTASK_DISPATCHED).lower():
        return False
    if ev == str(TaskEventType.TASK_PROGRESS).lower():
        return _spoken_step(payload) is not None
    if ev == str(TaskEventType.SUBTASK_STATUS_CHANGED).lower():
        return str(payload.get("status", "")).lower() in ("completed", "failed")
    return True


def format_spoken_task_update(event_type: str | TaskEventType, payload: dict[str, Any]) -> str:
    """Format a task lifecycle event payload into spoken language."""
    ev_type = str(event_type).lower()

    if ev_type in (
        str(TaskEventType.TASK_PLAN_CREATED).lower(),
        str(TaskEventType.PLANNING_STARTED).lower(),
    ):
        steps = payload.get(
            "subtasks_count", payload.get("subtask_count", payload.get("steps_count", 0))
        )
        intent = _clean(payload.get("intent", payload.get("instruction")), "your request")
        return f"I've created a plan with {steps} steps for: {intent}."

    if ev_type == str(TaskEventType.SUBTASK_DISPATCHED).lower():
        desc = _clean(payload.get("description", payload.get("title")), "the next action")
        return f"Starting step: {desc}."

    if ev_type == str(TaskEventType.SUBTASK_STATUS_CHANGED).lower():
        desc = _clean(payload.get("description", payload.get("title")), "a step")
        status = _clean(payload.get("status"), "")
        if status == "completed":
            # Never the vague "a step": a finished step is spoken only by the TaskAnnouncer, with
            # its real title. Without a title there is nothing exact to say.
            known = _clean(payload.get("description", payload.get("title")), "")
            return f"Step completed: {known}." if known else ""
        return f"Step status: {status} for {desc}."

    if ev_type in (
        str(TaskEventType.TASK_PAUSED_APPROVAL).lower(),
        str(TaskEventType.AWAITING_APPROVAL).lower(),
    ):
        action = _clean(
            payload.get("action_description", payload.get("tool_name")), "a sensitive action"
        )
        return f"Awaiting your approval for {action} in the web interface."

    if ev_type == str(TaskEventType.TASK_PROGRESS).lower():
        return _spoken_step(payload) or ""

    if ev_type == str(TaskEventType.TASK_COMPLETED).lower():
        return "Your task has completed successfully."

    if ev_type == str(TaskEventType.TASK_FAILED).lower():
        err = _clean(payload.get("error", payload.get("error_message")), "an unknown failure")
        return f"Task encountered an issue: {err}."

    return ""


class VoiceEventBridge:
    """Bridges Redis task event bus to real-time voice spoken announcements."""

    def __init__(
        self,
        workspace_id: UUID,
        redis_client: Redis | None = None,
        channel_prefix: str = DEFAULT_EVENT_CHANNEL_PREFIX,
        session_id: str | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        # Tasks of this conversation (Task.session_id) are announced even when they were created
        # from the UI or text chat rather than on this socket.
        self.session_id = str(session_id) if session_id else None
        self._redis = redis_client
        self._channel_prefix = channel_prefix
        self._tracked_tasks: set[str] = set()
        self._queue: asyncio.Queue[SpokenTaskAnnouncement] = asyncio.Queue()
        self._listener_task: asyncio.Task[None] | None = None
        self._closed = False
        # When set, milestones (done, failed, cancelled, approval, needs input) of ANY task of the
        # user are spoken by the announcer (once, queued); this bridge then stays silent for them.
        self.watcher: TaskAnnouncer | None = None

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

        if self.watcher is not None:
            await self.watcher.on_event(str(event_type), payload)
        task_id = str(payload.get("task_id") or payload.get("id") or "")
        event_session = str(
            (event.session_id if isinstance(event, TaskEvent) else event.get("session_id")) or ""
        )
        same_session = bool(self.session_id) and event_session == self.session_id
        if not task_id and not same_session:
            return
        if not (self.is_tracking(task_id) or same_session):
            return

        spoken_text = format_spoken_task_update(event_type, payload)
        announcement = SpokenTaskAnnouncement(
            task_id=task_id,
            event_type=str(event_type),
            spoken_text=spoken_text,
            payload=dict(payload),
            # Spoken updates come only from the TaskAnnouncer (exact step titles, coalesced,
            # spaced, one live model). The generic text above is never injected into the model:
            # that path produced "a step finished" repeated once per step.
            speak=False,
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
                    await pubsub.aclose()  # type: ignore[no-untyped-call]
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
