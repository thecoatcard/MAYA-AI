"""Queue abstraction for subtask background jobs conforming to ADR-0003 & Decision D8."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
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
    # Backoff before a retry becomes runnable; 0 means immediately. Not persisted.
    retry_delay_seconds: float = 0.0
    # Set at submission when every worker was busy: the worker then closes the task's "Queued: N
    # tasks ahead" progress step when this job starts. In memory only.
    queued_step: bool = False

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
    """Async in-memory queue with per-workspace and per-task fairness.

    Jobs are held per workspace and, inside a workspace, per task. ``dequeue`` serves the
    workspace with the fewest jobs currently running (ties rotate round-robin), then rotates
    over that workspace's tasks, so one busy workspace or one task with many ready subtasks
    cannot starve another. It is work conserving: an idle worker always takes any waiting job.
    The order of one task's own jobs is first in, first out; DAG ordering is enforced by the
    worker through dependencies, not by this queue.
    """

    def __init__(self) -> None:
        # workspace -> task -> jobs. Dict order is the rotation order (served keys move last).
        self._pending: dict[UUID, dict[UUID, deque[SubtaskJob]]] = {}
        self._running: dict[UUID, int] = {}
        self._available = asyncio.Semaphore(0)
        self._size = 0
        # How many jobs the worker pool runs side by side; set by the runtime (None = unknown).
        self.capacity: int | None = None

    def waiting_ahead(self) -> int | None:
        """``None`` when a worker is free, else how many OTHER tasks are queued ahead of a job
        enqueued now (0 means it is next in line). Advisory, for the "Queued" progress step."""
        if self.capacity is None or self.running_count() + self._size < self.capacity:
            return None
        return len({task_id for tasks in self._pending.values() for task_id in tasks})

    async def enqueue(self, job: SubtaskJob) -> None:
        """Push a subtask job to the queue."""
        self._pending.setdefault(job.workspace_id, {}).setdefault(job.task_id, deque()).append(job)
        self._size += 1
        self._available.release()
        logger.debug(
            "Enqueued subtask job %s for task %s (agent=%s)",
            job.subtask_id,
            job.task_id,
            job.assigned_agent,
        )

    def _pop_fair(self) -> SubtaskJob:
        workspace_id = min(self._pending, key=lambda w: self._running.get(w, 0))
        tasks = self._pending.pop(workspace_id)
        task_id = next(iter(tasks))
        jobs = tasks.pop(task_id)
        job = jobs.popleft()
        if jobs:
            tasks[task_id] = jobs  # rotated to the back: other tasks go first
        if tasks:
            self._pending[workspace_id] = tasks  # rotated to the back among workspaces
        self._size -= 1
        self._running[workspace_id] = self._running.get(workspace_id, 0) + 1
        return job

    async def dequeue(self, timeout_seconds: float | None = None) -> SubtaskJob | None:
        """Pop the next fair subtask job, or return None on timeout.

        The caller must report the job finished with ``task_done(job)``.
        """
        if timeout_seconds is None:
            await self._available.acquire()
        else:
            try:
                async with asyncio.timeout(timeout_seconds):
                    await self._available.acquire()
            except TimeoutError:
                return None
        return self._pop_fair()

    def task_done(self, job: SubtaskJob | None = None) -> None:
        """Mark a dequeued job as no longer running (frees its workspace's fairness slot)."""
        if job is None:
            return
        remaining = self._running.get(job.workspace_id, 0) - 1
        if remaining > 0:
            self._running[job.workspace_id] = remaining
        else:
            self._running.pop(job.workspace_id, None)

    def running_count(self, workspace_id: UUID | None = None) -> int:
        if workspace_id is None:
            return sum(self._running.values())
        return self._running.get(workspace_id, 0)

    def contains(self, subtask_id: UUID) -> bool:
        """Whether a job for this subtask is waiting in the queue (not yet dequeued)."""
        return any(
            job.subtask_id == subtask_id
            for tasks in self._pending.values()
            for jobs in tasks.values()
            for job in jobs
        )

    def qsize(self) -> int:
        return self._size

    @property
    def is_empty(self) -> bool:
        return self._size == 0
