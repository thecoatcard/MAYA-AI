"""The scheduler engine: finds due schedules, starts their runs and finishes them (ADR-0033).

One ``tick`` every ``poll_seconds`` (30 s). For each registered user and schedule, under a
per-schedule lock, it does exactly one of: poll the run in flight, start a retry, skip a run that
is too old, or start the due run. A run is an ordinary task (source ``scheduler``) limited to safe
steps (``safety.py``). Nothing here approves anything: a task that needs an approval or an answer
ends the run as ``needs_attention`` and the card stays for the user.

Missed runs (the app was closed) coalesce into ONE run when the app starts: ``next_run_at`` is in
the past, the run uses today's date, and ``next_run_at`` then moves past now. A run whose latest
missed occurrence is older than 48 hours is recorded as ``skipped`` instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Final, Protocol
from uuid import UUID

from app.agents.prompts.scheduled_report import build_instruction, spoken_summary
from app.agents.state import SubTask
from app.core.clock import Clock
from app.core.email import EmailDeliveryError, EmailSender
from app.schedules.locks import RunLock
from app.schedules.messages import date_label, report_email
from app.schedules.models import MAX_SUMMARY_CHARS, Doc, LastRun, PendingRun, Schedule
from app.schedules.next_run import load_zone, next_run, occurrences_between
from app.schedules.recipients import AccountEmails, is_verified
from app.schedules.safety import make_plan_filter
from app.schedules.service import NotFound, ScheduleService
from app.schedules.task_probe import TaskOutcome, TaskProbe

logger = logging.getLogger("maya.schedules")

STALE_AFTER: Final = timedelta(hours=48)
RETRY_DELAY: Final = timedelta(minutes=10)
AUTO_PAUSE_AFTER: Final = 3
LOCK_TTL_SECONDS: Final = 300


class AlreadyRunning(RuntimeError):
    """This schedule has a run in flight."""


class RunRefused(RuntimeError):
    """The task could not be started; the message is plain text for the user."""


class RunSubmitter(Protocol):
    async def submit(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        idempotency_key: str,
        instruction: str,
        title: str | None,
        session_id: UUID | None,
        source: str = "api",
        deep_research: dict[str, Any] | None = None,
        plan_filter: Callable[[list[SubTask]], list[SubTask]] | None = None,
    ) -> Any:  # a TaskSubmission; the runner only reads ``task_id`` / ``ok`` / ``reason``
        ...


def occurrence_key(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def idempotency_key(schedule_id: str, occurrence: str, attempt: int) -> str:
    digest = hashlib.sha256(f"{schedule_id}|{occurrence}|{attempt}".encode()).hexdigest()[:40]
    return f"sched-{digest}"  # the worker treats the "sched-" prefix as unattended


class ScheduleRunner:
    def __init__(
        self,
        *,
        service: ScheduleService,
        submitter: RunSubmitter,
        probe: TaskProbe,
        accounts: AccountEmails,
        sender: EmailSender | None,
        lock: RunLock,
        clock: Clock,
        poll_seconds: int = 30,
        max_minutes: int = 15,
    ) -> None:
        self._service = service
        self._store = service.store
        self._submitter = submitter
        self._probe = probe
        self._accounts = accounts
        self._sender = sender
        self._lock = lock
        self._clock = clock
        self._poll = poll_seconds
        self._max_minutes = max_minutes
        self._loop: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._loop is None:
            self._stopping.clear()
            self._loop = asyncio.create_task(self._run(), name="maya-schedule-runner")

    async def stop(self) -> None:
        self._stopping.set()
        if self._loop is not None:
            self._loop.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._loop
            self._loop = None

    async def _run(self) -> None:
        # The first tick is the catch-up: due schedules whose time passed while the app was closed.
        while not self._stopping.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - the loop must survive anything
                logger.warning("schedule_tick_failed type=%s", type(error).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self._poll)

    # ----------------------------------------------------------------------- tick

    async def tick(self) -> int:
        """One pass over every registered user. Returns how many state changes were made."""
        changes = 0
        for workspace_id, user_id in await self._store.index.members():
            doc = await self._store.load(workspace_id, user_id)
            for schedule in list(doc.items):
                try:
                    changes += await self._advance(workspace_id, user_id, schedule.id)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - one schedule never blocks the rest
                    logger.warning("schedule_advance_failed type=%s", type(error).__name__)
        return changes

    async def _advance(self, workspace_id: UUID, user_id: UUID, schedule_id: str) -> int:
        key = f"{workspace_id}:{user_id}:{schedule_id}"
        token = await self._lock.acquire(key, LOCK_TTL_SECONDS)
        if token is None:
            return 0
        try:
            doc = await self._store.load(workspace_id, user_id)
            schedule = next((s for s in doc.items if s.id == schedule_id), None)
            if schedule is None:
                return 0
            now = self._clock.now()
            if schedule.pending_run is not None:
                return await self._poll_run(workspace_id, user_id, schedule, doc, now)
            if not schedule.enabled:
                return 0
            if schedule.retry_at is not None:
                if schedule.retry_at > now:
                    return 0
                occurrence = schedule.last_occurrence or occurrence_key(now)
                return await self._start(workspace_id, user_id, schedule, now, occurrence, 2)
            if schedule.next_run_at is not None and schedule.next_run_at <= now:
                return await self._start_due(workspace_id, user_id, schedule, now)
            return 0
        finally:
            await self._lock.release(key, token)

    # ------------------------------------------------------------------- starting

    async def _start_due(
        self, workspace_id: UUID, user_id: UUID, schedule: Schedule, now: datetime
    ) -> int:
        assert schedule.next_run_at is not None
        planned = occurrences_between(
            schedule.cadence, schedule.next_run_at - timedelta(seconds=1), now
        )
        latest = planned[-1] if planned else schedule.next_run_at
        if now - latest > STALE_AFTER:
            await self._record_skipped(workspace_id, user_id, schedule, now, latest)
            return 1
        return await self._start(workspace_id, user_id, schedule, now, occurrence_key(latest), 1)

    async def _record_skipped(
        self, workspace_id: UUID, user_id: UUID, schedule: Schedule, now: datetime, latest: datetime
    ) -> None:
        reason = (
            "MAYA was closed for more than 48 hours, so the missed report was not run. "
            "The next one runs on schedule."
        )

        def change(doc: Doc) -> None:
            item = _find(doc, schedule.id)
            if item is None or item.pending_run is not None:
                return
            item.last_run = LastRun(at=now, status="skipped", reason=reason, announced=True)
            item.next_run_at = next_run(item.cadence, now) if item.enabled else None
            item.last_occurrence = occurrence_key(latest)

        await self._store.update(workspace_id, user_id, change)

    async def _start(
        self,
        workspace_id: UUID,
        user_id: UUID,
        schedule: Schedule,
        now: datetime,
        occurrence: str,
        attempt: int,
        *,
        manual: bool = False,
    ) -> int:
        """Create the task and record the run in flight. Raises ``RunRefused`` for a manual run
        that could not start; a scheduled run records the failure policy instead."""
        zone = load_zone(schedule.cadence.timezone)
        title = f"{schedule.name} — {date_label(now.astimezone(zone).date())}"
        instruction = build_instruction(
            name=schedule.name,
            instructions=schedule.instructions,
            topic=schedule.topic,
            day=now.astimezone(zone).date(),
            timezone=schedule.cadence.timezone,
            file_format=schedule.format,
            sources_target=schedule.depth.sources_target,
        )
        deep = (
            {"sources": schedule.depth.sources_target} if schedule.depth.use_deep_research else None
        )
        try:
            submission = await self._submitter.submit(
                workspace_id=workspace_id,
                user_id=user_id,
                idempotency_key=idempotency_key(schedule.id, occurrence, attempt),
                instruction=instruction,
                title=title[:240],
                session_id=None,
                source="scheduler",
                deep_research=deep,
                plan_filter=make_plan_filter(schedule.format, instruction),
            )
        except Exception as error:  # noqa: BLE001
            logger.warning("schedule_submit_failed type=%s", type(error).__name__)
            return await self._after_failure(
                workspace_id,
                user_id,
                schedule,
                now,
                "The task could not be started.",
                attempt,
                manual,
                occurrence,
            )
        if getattr(submission, "ok", True) is False:
            reason = str(getattr(submission, "reason", "") or "The task could not be started.")
            return await self._after_failure(
                workspace_id, user_id, schedule, now, reason, attempt, manual, occurrence
            )
        task_id = str(submission.task_id)

        def change(doc: Doc) -> bool:
            item = _find(doc, schedule.id)
            if item is None or item.pending_run is not None:
                return False
            item.pending_run = PendingRun(
                task_id=task_id, occurrence=occurrence, started_at=now, attempt=attempt
            )
            item.retry_at = None
            if not manual:
                item.last_occurrence = occurrence
                if attempt == 1:
                    item.next_run_at = next_run(item.cadence, now)
            return True

        await self._store.update(workspace_id, user_id, change)
        return 1

    async def run_now(self, workspace_id: UUID, user_id: UUID, schedule_id: str) -> str:
        """Start one run immediately (manual). Returns the task id."""
        key = f"{workspace_id}:{user_id}:{schedule_id}"
        token = await self._lock.acquire(key, LOCK_TTL_SECONDS)
        if token is None:
            raise AlreadyRunning("This schedule is already running.")
        try:
            doc = await self._store.load(workspace_id, user_id)
            schedule = _find(doc, schedule_id)
            if schedule is None:
                raise NotFound("That schedule does not exist.")
            if schedule.pending_run is not None:
                raise AlreadyRunning("This schedule is already running.")
            now = self._clock.now()
            await self._start(
                workspace_id,
                user_id,
                schedule,
                now,
                f"manual-{occurrence_key(now)}",
                1,
                manual=True,
            )
            fresh = _find(await self._store.load(workspace_id, user_id), schedule_id)
            if fresh is None or fresh.pending_run is None:
                reason = fresh.last_run.reason if fresh and fresh.last_run else ""
                raise RunRefused(reason or "The run could not be started.")
            return fresh.pending_run.task_id
        finally:
            await self._lock.release(key, token)

    # -------------------------------------------------------------------- polling

    async def _poll_run(
        self, workspace_id: UUID, user_id: UUID, schedule: Schedule, doc: Doc, now: datetime
    ) -> int:
        pending = schedule.pending_run
        assert pending is not None
        outcome = await self._probe.check(workspace_id, user_id, pending.task_id)
        manual = pending.occurrence.startswith("manual-")
        if outcome.state == "running":
            if now - pending.started_at > timedelta(minutes=self._max_minutes):
                await self._probe.cancel(workspace_id, user_id, pending.task_id)
                reason = f"The run took longer than {self._max_minutes} minutes and was stopped."
                return await self._after_failure(
                    workspace_id,
                    user_id,
                    schedule,
                    now,
                    reason,
                    pending.attempt,
                    manual,
                    pending.occurrence,
                    clear_pending=True,
                )
            return 0
        if outcome.state == "needs_attention":
            await self._finish(
                workspace_id,
                user_id,
                schedule,
                now,
                pending,
                "needs_attention",
                reason=outcome.reason,
            )
            return 1
        if outcome.state == "completed":
            if not outcome.report.strip() and not outcome.artifact_ids:
                return await self._after_failure(
                    workspace_id,
                    user_id,
                    schedule,
                    now,
                    "The run finished but produced no report.",
                    pending.attempt,
                    manual,
                    pending.occurrence,
                    clear_pending=True,
                )
            await self._complete(workspace_id, user_id, schedule, doc, now, pending, outcome)
            return 1
        reason = outcome.reason or "The task did not finish."
        return await self._after_failure(
            workspace_id,
            user_id,
            schedule,
            now,
            reason,
            pending.attempt,
            manual,
            pending.occurrence,
            clear_pending=True,
        )

    async def _complete(
        self,
        workspace_id: UUID,
        user_id: UUID,
        schedule: Schedule,
        doc: Doc,
        now: datetime,
        pending: PendingRun,
        outcome: TaskOutcome,
    ) -> None:
        report = outcome.report.strip()
        mail_status, notes = await self._deliver_mail(
            workspace_id, user_id, schedule, doc, now, report, outcome
        )
        await self._finish(
            workspace_id,
            user_id,
            schedule,
            now,
            pending,
            "completed",
            summary=report[:MAX_SUMMARY_CHARS],
            spoken=spoken_summary(report),
            artifact_ids=list(outcome.artifact_ids),
            mail_status=mail_status,
            reason=" ".join(notes),
        )

    async def _finish(
        self,
        workspace_id: UUID,
        user_id: UUID,
        schedule: Schedule,
        now: datetime,
        pending: PendingRun,
        status: Any,
        *,
        summary: str = "",
        spoken: str = "",
        artifact_ids: list[str] | None = None,
        mail_status: str | None = None,
        reason: str = "",
    ) -> None:
        def change(doc: Doc) -> None:
            item = _find(doc, schedule.id)
            if item is None or item.pending_run is None:
                return
            item.pending_run = None
            item.consecutive_failures = 0
            item.last_run = LastRun(
                at=now,
                status=status,
                task_id=pending.task_id,
                summary=summary,
                spoken_summary=spoken,
                announced=False,
                artifact_ids=artifact_ids or [],
                mail_status=mail_status,
                reason=reason,
            )

        await self._store.update(workspace_id, user_id, change)

    # ------------------------------------------------------------------- failures

    async def _after_failure(
        self,
        workspace_id: UUID,
        user_id: UUID,
        schedule: Schedule,
        now: datetime,
        reason: str,
        attempt: int,
        manual: bool,
        occurrence: str,
        *,
        clear_pending: bool = False,
    ) -> int:
        """Retry once after 10 minutes, then record 'failed'; three failures in a row pause it."""

        def change(doc: Doc) -> None:
            item = _find(doc, schedule.id)
            if item is None:
                return
            task_id = item.pending_run.task_id if item.pending_run else None
            item.pending_run = None
            if attempt == 1 and not manual:
                item.retry_at = now + RETRY_DELAY
                item.last_occurrence = occurrence
                if item.enabled and item.next_run_at is not None and item.next_run_at <= now:
                    item.next_run_at = next_run(item.cadence, now)  # the retry covers this one
                return
            item.retry_at = None
            item.last_run = LastRun(
                at=now, status="failed", task_id=task_id, reason=reason[:300], announced=False
            )
            if manual:
                return
            item.consecutive_failures += 1
            if item.consecutive_failures >= AUTO_PAUSE_AFTER:
                item.enabled = False
                item.paused_reason = "failures"
                item.next_run_at = None
                item.last_run.reason = (
                    f"{reason[:240]} Paused after {AUTO_PAUSE_AFTER} failed runs in a row. "
                    "Turn it back on in Settings > Schedules."
                )

        await self._store.update(workspace_id, user_id, change)
        return 1

    # ----------------------------------------------------------------------- mail

    async def _deliver_mail(
        self,
        workspace_id: UUID,
        user_id: UUID,
        schedule: Schedule,
        doc: Doc,
        now: datetime,
        report: str,
        outcome: TaskOutcome,
    ) -> tuple[str, list[str]]:
        """Send the report through MAYA's own system mailer (never ``app.mail``)."""
        notes: list[str] = []
        if outcome.sources and outcome.sources < schedule.depth.sources_target:
            notes.append(
                f"Only {outcome.sources} "
                f"{'source' if outcome.sources == 1 else 'sources'} could be read "
                f"(aimed for {schedule.depth.sources_target})."
            )
        if not schedule.deliver.mail:
            return "not_requested", notes
        if self._sender is None:
            notes.append("Mail was not sent: MAYA's mail service is not configured on this server.")
            return "mail_not_configured", notes
        account = await self._accounts.email_for(workspace_id, user_id)
        allowed = [r for r in schedule.deliver.recipients if is_verified(doc, r, account)]
        skipped = [r for r in schedule.deliver.recipients if r not in allowed]
        if skipped:
            notes.append(
                f"{len(skipped)} recipient(s) are not verified yet, so they got no mail. "
                "Verify them in Settings > Schedules."
            )
        if not allowed:
            return "recipient_unverified", notes
        day = now.astimezone(load_zone(schedule.cadence.timezone)).date()
        sent = 0
        for address in allowed:
            message = report_email(
                to=address,
                schedule_name=schedule.name,
                day=day,
                markdown=report,
                account_email=account or "your MAYA AI account",
                file_made=bool(outcome.artifact_ids),
                notes=tuple(notes),
            )
            try:
                await self._sender.send(message)
                sent += 1
            except EmailDeliveryError:
                logger.warning("schedule_mail_failed")
        if sent == len(allowed) and not skipped:
            return "sent", notes
        if sent == 0:
            notes.append("The mail could not be delivered.")
            return "failed", notes
        return "partial", notes


def _find(doc: Doc, schedule_id: str) -> Schedule | None:
    return next((s for s in doc.items if s.id == schedule_id), None)
