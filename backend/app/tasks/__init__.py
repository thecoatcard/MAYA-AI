"""Tasks module for background execution and lifecycle events."""

from app.tasks.events import (
    DEFAULT_EVENT_CHANNEL_PREFIX,
    TaskEvent,
    TaskEventPublisher,
    TaskEventType,
    get_event_channel,
    get_task_channel,
)
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.outbox import OutboxDispatcher, write_outbox_event
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.saga import SagaCompensationResult, SagaManager
from app.tasks.worker import (
    DEFAULT_SUBTASK_TIMEOUT_SECONDS,
    SubtaskExecutionError,
    SubtaskTimeoutError,
    SubtaskWorker,
)

__all__ = [
    "DEFAULT_EVENT_CHANNEL_PREFIX",
    "DEFAULT_SUBTASK_TIMEOUT_SECONDS",
    "OutboxDispatcher",
    "SagaCompensationResult",
    "SagaManager",
    "SubtaskExecutionError",
    "SubtaskJob",
    "SubtaskQueue",
    "SubtaskTimeoutError",
    "SubtaskWorker",
    "TaskEvent",
    "TaskEventPublisher",
    "TaskEventType",
    "TaskIdempotencyService",
    "get_event_channel",
    "get_task_channel",
    "write_outbox_event",
]
