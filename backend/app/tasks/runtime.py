"""In-process task runtime: tenant-scoped worker loop, outbox relay and startup reconciler.

Every tenant table is FORCE row-level security and the API runs as the restricted ``maya_app``
role, so background work must run inside a tenant context (``app.workspace_id``) exactly like a
request does. A session without that context silently sees zero rows. This module therefore
opens one tenant-scoped session per unit of work.

Cross-workspace discovery: ``workspaces`` is itself FORCE RLS, so the runtime lists tenants with
open work through the SECURITY DEFINER function ``public.list_workspaces_with_open_work()``
(migration 0014), called with no tenant context, and then works each one in its own context.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.models import PlanStatus, Subtask, SubtaskStatus, Task
from app.services import task_result
from app.tasks.events import TaskEventPublisher, TaskEventType
from app.tasks.outbox import OutboxDispatcher, write_outbox_event
from app.tasks.progress_steps import emit_task_step, subtask_step_id
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.worker import MAX_SUBTASK_RETRIES, SubtaskWorker, resolve_subtask_uuid

logger = logging.getLogger("maya")

OPEN_TASK_STATUSES = ("planning", "pending", "executing", "awaiting_approval")
_LIST_OPEN_WORK = text("SELECT * FROM public.list_workspaces_with_open_work()")
_SET_WORKSPACE = text("SELECT set_config('app.workspace_id', :workspace_id, true)")

DEFAULT_WORKER_CONCURRENCY = 3
MIN_WORKER_CONCURRENCY = 1
MAX_WORKER_CONCURRENCY = 8

# A job that crashes outside the handler is tried once more in a fresh session, then the step is
# failed with a plain reason (never left pending with nothing queued).
JOB_CRASH_ATTEMPTS = 2
# Recovery sweep: re-enqueue runnable work that has no queue entry (see ``sweep_stalled``).
SWEEP_INTERVAL_SECONDS = 60.0
MAX_SWEEP_JOBS = 50  # per pass
MAX_SWEEPS_PER_SUBTASK = 5  # a step the sweep keeps re-enqueueing is left alone after this

AgentHandler = Callable[[SubtaskJob, dict[str, Any]], Awaitable[dict[str, Any]]]


@asynccontextmanager
async def workspace_session(engine: AsyncEngine, workspace_id: UUID) -> AsyncIterator[AsyncSession]:
    """An AsyncSession whose every transaction carries ``app.workspace_id``.

    The setting is re-applied at the start of each transaction, so ``session.commit()`` can be
    called mid-unit (the next statement begins a new, still tenant-scoped transaction).
    """
    async with AsyncSession(engine, expire_on_commit=False) as session:

        @event.listens_for(session.sync_session, "after_begin")
        def _apply_tenant(_session: Any, _transaction: Any, connection: Any) -> None:
            connection.execute(_SET_WORKSPACE, {"workspace_id": str(workspace_id)})

        yield session


def error_where(error: BaseException, depth: int = 3) -> str:
    """Where an error was raised, innermost last: ``file:line:function < ...``. Never values."""
    frames = traceback.extract_tb(error.__traceback__)[-depth:]
    return " < ".join(
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames
    )


class TaskRuntime:
    """Owns the subtask queue consumer, the outbox relay and startup reconciliation."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        queue: SubtaskQueue,
        publisher: TaskEventPublisher,
        worker: SubtaskWorker,
        agent_handler: AgentHandler,
        outbox_interval_seconds: float = 0.5,
        concurrency: int = DEFAULT_WORKER_CONCURRENCY,
        sweep_interval_seconds: float = SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self.engine = engine
        self.queue = queue
        self.worker = worker
        self.agent_handler = agent_handler
        self.dispatcher = OutboxDispatcher(publisher)
        self.outbox_interval_seconds = outbox_interval_seconds
        self.concurrency = max(MIN_WORKER_CONCURRENCY, min(MAX_WORKER_CONCURRENCY, concurrency))
        # Subtasks being processed right now: a duplicate job for one of them is dropped, so a
        # subtask is never claimed by two workers at once.
        self._in_flight: set[UUID] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._delayed: set[asyncio.Task[None]] = set()
        self.sweep_interval_seconds = sweep_interval_seconds
        # Subtasks whose delayed (backoff) job has not reached the queue yet.
        self._scheduled: set[UUID] = set()
        # Crashes per subtask (cleared on a clean run) and the recovery sweep's memory.
        self._job_failures: dict[UUID, int] = {}
        self._sweep_seen: set[UUID] = set()
        self._swept: dict[UUID, int] = {}

    # ------------------------------------------------------------------ lifecycle

    async def start(self, *, reconcile: bool = True) -> None:
        """Reconcile once, then start the worker pool and the outbox loop."""
        if reconcile:
            try:
                await self.reconcile()
            except Exception:
                logger.warning("task_reconciler_error", exc_info=True)
            await self.repost_input_cards()  # never raises: a lost question card comes back
        self.queue.capacity = self.concurrency
        self._tasks = [
            asyncio.create_task(self._worker_loop(), name=f"maya-task-worker-{index + 1}")
            for index in range(self.concurrency)
        ]
        self._tasks.append(asyncio.create_task(self._outbox_loop(), name="maya-task-outbox"))
        self._tasks.append(asyncio.create_task(self._sweep_loop(), name="maya-task-sweeper"))

    async def stop(self) -> None:
        for task in (*self._tasks, *self._delayed):
            task.cancel()
        for task in (*self._tasks, *self._delayed):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        self._delayed.clear()

    # ------------------------------------------------------------------ enqueue helpers

    async def enqueue_after_commit(self, jobs: list[SubtaskJob]) -> None:
        for job in jobs:
            delay = job.retry_delay_seconds
            if delay > 0:
                job.retry_delay_seconds = 0.0
                self._schedule(job, delay)
            else:
                await self.queue.enqueue(job)

    def _schedule(self, job: SubtaskJob, delay: float) -> None:
        self._scheduled.add(job.subtask_id)

        async def _later() -> None:
            try:
                await asyncio.sleep(delay)
                await self.queue.enqueue(job)
            finally:
                self._scheduled.discard(job.subtask_id)

        handle = asyncio.create_task(_later())
        self._delayed.add(handle)
        handle.add_done_callback(self._delayed.discard)

    # ------------------------------------------------------------------ worker

    async def process_job(self, job: SubtaskJob) -> None:
        """Run one job in its own tenant transaction and enqueue follow-ups after commit.

        Each job gets its own session, so one job's failure, timeout or rollback never touches
        another's. A job for a subtask that is already being processed is dropped (claimed once).
        """
        if job.subtask_id in self._in_flight:
            logger.warning("subtask_job_duplicate_dropped subtask=%s", job.subtask_id)
            return
        self._in_flight.add(job.subtask_id)
        try:
            deferred: list[SubtaskJob] = []
            async with workspace_session(self.engine, job.workspace_id) as session:
                try:
                    await self.worker.execute_job(
                        session=session,
                        job=job,
                        agent_handler=self.agent_handler,
                        deferred_jobs=deferred,
                    )
                    await session.commit()
                except BaseException:
                    # Never leave the session in a failed state (PendingRollbackError for whatever
                    # runs next on it); the caller's recovery works in a fresh session.
                    with contextlib.suppress(Exception):
                        await session.rollback()
                    raise
            self._job_failures.pop(job.subtask_id, None)
            await self.enqueue_after_commit(deferred)
        finally:
            self._in_flight.discard(job.subtask_id)

    async def _worker_loop(self) -> None:
        while True:
            job: SubtaskJob | None = None
            try:
                job = await self.queue.dequeue(timeout_seconds=1.0)
                if job is None:
                    continue
                try:
                    await self.process_job(job)
                finally:
                    self.queue.task_done(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "subtask_worker_loop_error error=%s where=%s subtask=%s",
                    type(exc).__name__,
                    error_where(exc),
                    job.subtask_id if job is not None else None,
                )
                if job is not None:
                    await self._recover_failed_job(job, exc)
                await asyncio.sleep(1.0)

    async def _recover_failed_job(self, job: SubtaskJob, exc: Exception) -> None:
        """Never leave a step stuck after its job crashed outside the handler, in a fresh session.

        ``in_progress``: the bounded retry path. ``pending`` (the crash rolled the work back):
        tried once more, then failed with a plain reason so the task finishes. A finished step
        whose dependents were not queued (the crash came after the commit): they are queued now.
        """
        attempts = self._job_failures[job.subtask_id] = (
            self._job_failures.get(job.subtask_id, 0) + 1
        )
        try:
            deferred: list[SubtaskJob] = []
            promote = False
            async with workspace_session(self.engine, job.workspace_id) as session:
                subtask = (
                    (
                        await session.execute(
                            select(Subtask).where(
                                Subtask.id == job.subtask_id,
                                Subtask.workspace_id == job.workspace_id,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if subtask is None:
                    return
                if subtask.status == SubtaskStatus.IN_PROGRESS:
                    await self.worker._record_failure(
                        session,
                        job,
                        subtask,
                        reason=f"The worker failed while running this step ({type(exc).__name__}).",
                        error_code=type(exc).__name__,
                        claimed_approval_id=None,
                        worker_id="recovery",
                        retryable=True,
                        deferred_jobs=deferred,
                    )
                elif subtask.status == SubtaskStatus.PENDING:
                    if attempts < JOB_CRASH_ATTEMPTS:
                        job.retry_delay_seconds = 1.0
                        deferred.append(job)
                        logger.warning(
                            "subtask_job_retry subtask=%s error=%s",
                            job.subtask_id,
                            type(exc).__name__,
                        )
                    else:
                        self._job_failures.pop(job.subtask_id, None)
                        logger.error(
                            "subtask_job_failed_plainly subtask=%s error=%s",
                            job.subtask_id,
                            type(exc).__name__,
                        )
                        await self.worker.fail_step_plainly(
                            session,
                            job,
                            subtask,
                            reason=f"an internal error ({type(exc).__name__})",
                            deferred_jobs=deferred,
                        )
                        await emit_task_step(
                            session,
                            workspace_id=job.workspace_id,
                            task_id=job.task_id,
                            subtask_id=job.subtask_id,
                            step_id=subtask_step_id(job.subtask_id),
                            kind="step",
                            label=await self.worker._step_label(session, job),
                            state="failed",
                            detail="The step did not finish. The task shows the reason.",
                        )
                else:
                    # completed / failed: the crash was after the work; make sure nothing that
                    # depends on it is left pending with no queue entry.
                    promote = subtask.status in (SubtaskStatus.COMPLETED, SubtaskStatus.FAILED)
                await session.commit()
            await self.enqueue_after_commit(deferred)
            if promote:
                await self.sweep_stalled(
                    workspace_ids=[job.workspace_id], task_id=job.task_id, grace=False
                )
        except Exception as recovery_error:
            logger.error(
                "subtask_job_recovery_failed subtask=%s error=%s where=%s",
                job.subtask_id,
                type(recovery_error).__name__,
                error_where(recovery_error),
            )

    # ------------------------------------------------------------------ outbox relay

    async def list_workspaces_with_open_work(self) -> list[UUID]:
        """Workspaces with open tasks/subtasks or pending outbox rows (no tenant context).

        Uses the SECURITY DEFINER function from migration 0014; ``maya_app`` cannot read the
        tenant tables, or ``workspaces``, without a workspace context.
        """
        async with self.engine.connect() as connection:
            rows = await connection.execute(_LIST_OPEN_WORK)
            return [row[0] for row in rows]

    async def relay_outbox_once(self) -> int:
        """Relay pending outbox events for every workspace with open work."""
        total = 0
        for workspace_id in await self.list_workspaces_with_open_work():
            try:
                async with workspace_session(self.engine, workspace_id) as session:
                    total += await self.dispatcher.dispatch_pending_events(
                        session, workspace_id=workspace_id
                    )
                    await session.commit()
                self._post_results(workspace_id, list(self.dispatcher.terminal_tasks))
            except Exception:
                logger.warning("outbox_relay_error workspace=%s", workspace_id, exc_info=True)
        return total

    def _post_results(self, workspace_id: UUID, task_ids: list[UUID]) -> None:
        """Write the "what the task got" chat message of each task that just ended, in the
        background (a model call must not hold the relay). Never raises; see
        ``app.services.task_result``."""
        service = task_result.current()
        if service is None or not task_ids:
            return

        async def post(task_id: UUID) -> None:
            await service.ensure(workspace_id, task_id)

        for task_id in dict.fromkeys(task_ids):
            handle = asyncio.create_task(post(task_id))
            self._delayed.add(handle)
            handle.add_done_callback(self._delayed.discard)

    async def _outbox_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.outbox_interval_seconds)
                await self.relay_outbox_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("outbox_poller_tick_error", exc_info=True)
                await asyncio.sleep(1.0)

    # ------------------------------------------------------------------ recovery sweep

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.sweep_interval_seconds)
                await self.sweep_stalled()
                await self.repost_input_cards()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "task_sweep_tick_error error=%s where=%s",
                    type(error).__name__,
                    error_where(error),
                )

    async def repost_input_cards(self, *, workspace_ids: list[UUID] | None = None) -> int:
        """Put back the question card of a paused task (``needs_input``) whose card is no longer
        the open one in its chat (see ``app.tasks.input_cards``). Idempotent; never raises."""
        from app.tasks.input_cards import repost_missing_cards

        posted = 0
        targets = (
            workspace_ids
            if workspace_ids is not None
            else await self.list_workspaces_with_open_work()
        )
        for workspace_id in targets:
            try:
                async with workspace_session(self.engine, workspace_id) as session:
                    posted += await repost_missing_cards(session, workspace_id)
                    await session.commit()
            except Exception as error:  # noqa: BLE001 - tried again on the next pass
                logger.warning(
                    "needs_input_repost_error workspace=%s error=%s",
                    workspace_id,
                    type(error).__name__,
                )
        return posted

    def _has_queue_entry(self, subtask_id: UUID) -> bool:
        return (
            subtask_id in self._in_flight
            or subtask_id in self._scheduled
            or self.queue.contains(subtask_id)
        )

    async def sweep_stalled(
        self,
        *,
        workspace_ids: list[UUID] | None = None,
        task_id: UUID | None = None,
        grace: bool = True,
    ) -> int:
        """Re-enqueue work that is runnable but has no queue entry; returns the jobs enqueued.

        A task is "wedged" when one of its pending steps has every dependency done, yet nothing
        is queued, claimed or scheduled for it (a crash between the commit and the enqueue, a
        lost job). Only ``executing`` tasks are swept: a task waiting for an approval or an answer
        is waiting on purpose. A step that is ``in_progress`` with no worker holding it is failed
        into the bounded retry path. Idempotent (a duplicate job is dropped or sees the step done)
        and bounded (``MAX_SWEEP_JOBS`` per pass, ``MAX_SWEEPS_PER_SUBTASK`` per step). With
        ``grace`` a step is only acted on when the previous pass saw it stalled too, so work that
        is being committed and enqueued right now is never doubled.
        """
        seen_now: set[UUID] = set()
        enqueued = 0
        targets = (
            workspace_ids
            if workspace_ids is not None
            else await self.list_workspaces_with_open_work()
        )
        for workspace_id in targets:
            if enqueued >= MAX_SWEEP_JOBS:
                break
            try:
                deferred: list[SubtaskJob] = []
                async with workspace_session(self.engine, workspace_id) as session:
                    await self._sweep_workspace(
                        session, workspace_id, task_id, grace, seen_now, deferred
                    )
                    await session.commit()
                await self.enqueue_after_commit(deferred)
                enqueued += len(deferred)
            except Exception as error:
                logger.warning(
                    "task_sweep_error workspace=%s error=%s where=%s",
                    workspace_id,
                    type(error).__name__,
                    error_where(error),
                )
        if grace:
            self._sweep_seen = seen_now
        if enqueued:
            logger.warning("task_sweep_requeued jobs=%d", enqueued)
        return enqueued

    async def _sweep_workspace(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        only_task: UUID | None,
        grace: bool,
        seen_now: set[UUID],
        deferred: list[SubtaskJob],
    ) -> None:
        stmt = select(Task).where(Task.workspace_id == workspace_id, Task.status == "executing")
        if only_task is not None:
            stmt = stmt.where(Task.id == only_task)
        for task in (await session.execute(stmt)).scalars().all():
            subtasks = list(
                (
                    await session.execute(
                        select(Subtask).where(
                            Subtask.task_id == task.id, Subtask.workspace_id == workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            done = {str(s.id) for s in subtasks if self.worker._satisfied(s)}
            for st in subtasks:
                if len(deferred) >= MAX_SWEEP_JOBS:
                    return
                if self._has_queue_entry(st.id):
                    continue
                if st.status == SubtaskStatus.PENDING:
                    if not all(
                        str(d) in done or str(resolve_subtask_uuid(task.id, str(d))) in done
                        for d in (st.dependencies or [])
                    ):
                        continue
                elif st.status != SubtaskStatus.IN_PROGRESS:
                    continue
                if grace and st.id not in self._sweep_seen:
                    seen_now.add(st.id)  # stalled for one pass: confirm on the next
                    continue
                if self._swept.get(st.id, 0) >= MAX_SWEEPS_PER_SUBTASK:
                    continue
                self._swept[st.id] = self._swept.get(st.id, 0) + 1
                job = SubtaskJob(
                    workspace_id=workspace_id,
                    user_id=task.user_id,
                    task_id=task.id,
                    subtask_id=st.id,
                    assigned_agent=str(getattr(st.assigned_agent, "value", st.assigned_agent)),
                )
                if st.status == SubtaskStatus.IN_PROGRESS:
                    # Nobody holds it (a worker holding it would be in flight): the bounded
                    # retry path, never left running forever.
                    await self.worker._record_failure(
                        session,
                        job,
                        st,
                        reason="The step was interrupted before it finished.",
                        error_code="StepInterrupted",
                        claimed_approval_id=None,
                        worker_id="sweep",
                        retryable=True,
                        deferred_jobs=deferred,
                    )
                else:
                    deferred.append(job)
                logger.warning("task_sweep_step subtask=%s status=%s", st.id, st.status.value)

    # ------------------------------------------------------------------ reconciler

    async def reconcile(self) -> int:
        """Re-enqueue runnable work found in the database after a restart; returns job count."""
        enqueued = 0
        for workspace_id in await self.list_workspaces_with_open_work():
            async with workspace_session(self.engine, workspace_id) as session:
                jobs = await self._reconcile_workspace(session, workspace_id)
                await session.commit()
            await self.enqueue_after_commit(jobs)
            enqueued += len(jobs)
        if enqueued:
            logger.info("task_reconciler_enqueued jobs=%d", enqueued)
        return enqueued

    async def _reconcile_workspace(
        self, session: AsyncSession, workspace_id: UUID
    ) -> list[SubtaskJob]:
        tasks = list(
            (
                await session.execute(
                    select(Task).where(
                        Task.workspace_id == workspace_id,
                        Task.status.in_(OPEN_TASK_STATUSES),
                    )
                )
            )
            .scalars()
            .all()
        )
        jobs: list[SubtaskJob] = []
        for task in tasks:
            subtasks = list(
                (
                    await session.execute(
                        select(Subtask).where(
                            Subtask.task_id == task.id, Subtask.workspace_id == workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not subtasks:
                task.status = "failed"
                task.plan_status = PlanStatus.FAILED
                await write_outbox_event(
                    session,
                    workspace_id,
                    task.id,
                    TaskEventType.TASK_FAILED,
                    {"task_id": str(task.id), "error": "no_plan", "message": "Task has no plan"},
                )
                continue

            failed_task = False
            for st in subtasks:
                if st.status == SubtaskStatus.IN_PROGRESS:
                    # Interrupted by a restart: bounded retry, never left in_progress.
                    st.retry_count += 1
                    if st.retry_count >= MAX_SUBTASK_RETRIES:
                        st.status = SubtaskStatus.FAILED
                        st.error_message = "Interrupted by a service restart too many times"
                        failed_task = True
                    else:
                        st.status = SubtaskStatus.PENDING
            if failed_task:
                task.status = "failed"
                task.plan_status = PlanStatus.FAILED
                await write_outbox_event(
                    session,
                    workspace_id,
                    task.id,
                    TaskEventType.TASK_FAILED,
                    {"task_id": str(task.id), "error": "interrupted"},
                )
                continue

            completed = {
                str(s.id)
                for s in subtasks
                if s.status == SubtaskStatus.COMPLETED
                or (
                    s.status == SubtaskStatus.FAILED
                    and self.worker._is_critical(s) is False  # tolerated (non-critical) failure
                )
            }
            for st in subtasks:
                if st.status != SubtaskStatus.PENDING:
                    continue
                deps = st.dependencies or []
                if all(
                    str(d) in completed or str(resolve_subtask_uuid(task.id, str(d))) in completed
                    for d in deps
                ):
                    jobs.append(
                        SubtaskJob(
                            workspace_id=workspace_id,
                            user_id=task.user_id,
                            task_id=task.id,
                            subtask_id=st.id,
                            assigned_agent=str(
                                getattr(st.assigned_agent, "value", st.assigned_agent)
                            ),
                        )
                    )
        await session.flush()
        return jobs
