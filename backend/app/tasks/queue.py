"""Queue abstraction for subtask background jobs conforming to ADR-0003 & Decision D8."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class SubtaskJob:
    """Exactly one subtask per queued job message (Decision D8)."""

    workspace_id: UUID
    user_id: UUID
    task_id: UUID
    subtask_id: UUID
    assigned_agent: str
    timeout_seconds: int = 300

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": str(self.workspace_id),
            "user_id": str(self.user_id),
            "task_id": str(self.task_id),
            "subtask_id": str(self.subtask_id),
            "assigned_agent": self.assigned_agent,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubtaskJob:
        return cls(
            workspace_id=UUID(data["workspace_id"]),
            user_id=UUID(data["user_id"]),
            task_id=UUID(data["task_id"]),
            subtask_id=UUID(data["subtask_id"]),
            assigned_agent=data["assigned_agent"],
            timeout_seconds=data.get("timeout_seconds", 300),
        )


class SubtaskQueue:
    """Async in-memory queue with Redis compatibility for subtask dispatch."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[SubtaskJob] = asyncio.Queue()

    async def enqueue(self, job: SubtaskJob) -> None:
        """Push a subtask job to the queue."""
        await self._queue.put(job)
        logger.debug(
            "Enqueued subtask job %s for task %s (agent=%s)",
            job.subtask_id,
            job.task_id,
            job.assigned_agent,
        )

    async def dequeue(self, timeout_seconds: float | None = None) -> SubtaskJob | None:
        """Pop the next subtask job, or return None on timeout."""
        if timeout_seconds is None:
            return await self._queue.get()
        try:
            async with asyncio.timeout(timeout_seconds):
                return await self._queue.get()
        except TimeoutError:
            return None

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()

    @property
    def is_empty(self) -> bool:
        return self._queue.empty()
