"""One way to turn an instruction into a planned, queued task (chat and ``POST /api/v1/tasks``).

``TaskSubmissionService.submit`` runs, in one tenant transaction: claim the idempotency key (or
return the task that already holds it), plan the work, write the task and subtask rows, write the
``task.plan_created`` outbox event and one audit row. Only after that transaction has committed are
the ready subtasks enqueued; enqueueing inside it raced the commit and lost jobs.

The task is linked to the session it is given (``session_id``), so a chat task belongs to the chat
the user is in and no hidden, empty session is created for it. A session id that does not resolve
inside the workspace falls back to the previous behaviour (a new session), see
``TaskIdempotencyService``.

Voice keeps its own wiring (``app.voice.task_wiring``): it must answer the model before planning
finishes, so it plans in the background. It uses the same tables, outbox event and queue.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents import capabilities
from app.agents.capabilities import Refusal, UnsupportedRequestError
from app.agents.custom import called_step, enabled_agents_for
from app.agents.planner import DAGPlanner
from app.agents.requirements import attach_preferences
from app.agents.state import SubTask, persisted_input_data
from app.db.models import ChatMessage, PlanStatus, Subtask, SubtaskAgent, SubtaskStatus, Task
from app.db.repositories.user_settings import UserSettingsStore
from app.db.tenant import tenant_session
from app.gateway.model_gateway import ModelGateway
from app.memory.chat_memory import load_standing_block
from app.research.plan import apply_deep_research
from app.services import required_details
from app.services.audit_service import AuditWriteError, record_audit
from app.services.projection_service import resolve_subtask_uuid
from app.tasks import needs_input
from app.tasks.events import TaskEventType
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.outbox import write_outbox_event
from app.tasks.pipeline import length_notice
from app.tasks.progress_steps import emit_task_step
from app.tasks.queue import SubtaskJob, SubtaskQueue

logger = logging.getLogger(__name__)


# Rewrites a freshly planned step list (a scheduled run keeps only safe steps, ADR-0033).
PlanFilter = Callable[[list[SubTask]], list[SubTask]]


@dataclass(frozen=True, slots=True)
class TaskSubmission:
    """The result of a submission. ``is_new`` is False when the key already held a task."""

    task_id: UUID
    title: str
    status: str
    is_new: bool
    created_at: datetime | None = None
    # An honest expectation for the reply, set when the work is long (the long-document pipeline).
    notice: str | None = None
    # How many steps the plan has (the system-written "Started: ... N steps" message).
    step_count: int | None = None


@dataclass(frozen=True, slots=True)
class TaskRefusal(TaskSubmission):
    """A refusal that is also a ``TaskSubmission`` (so the submitter keeps one return type).

    Returned when the request needs data or an integration this deployment does not have. No task
    row exists for it (``task_id`` is the nil UUID, ``status`` is the refusal kind); consumers check
    ``ok`` and show ``reason`` (what cannot be done) and ``hint`` (what to connect).
    """

    ok: bool = False
    code: str = "unsupported"
    reason: str = ""
    hint: str = ""
    # ``status`` is ``unsupported`` or ``needs_clarification`` (then ``question`` and ``missing``
    # are set); ``is_new`` is False and ``task_id`` is the nil UUID: nothing was created.
    question: str = ""
    missing: tuple[str, ...] = ()

    @classmethod
    def of(cls, refusal: Refusal) -> TaskRefusal:
        return cls(
            task_id=UUID(int=0),
            title="",
            status=refusal.status,
            is_new=False,
            reason=refusal.reason,
            hint=refusal.hint,
            code=refusal.code,
            question=refusal.question,
            missing=refusal.missing,
        )

    def as_result(self) -> dict[str, object]:
        return Refusal(
            reason=self.reason,
            hint=self.hint,
            code=self.code,
            status=self.status,
            question=self.question,
            missing=self.missing,
        ).as_result()


class TaskSubmitter(Protocol):
    """What the chat service needs: submit one task, idempotently."""

    async def submit(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        idempotency_key: str,
        instruction: str,
        title: str | None,
        session_id: UUID | None,
        budget_usd: Decimal | None = None,
        source: str = "api",
        input_files: Sequence[str] | None = None,
        deep_research: dict[str, Any] | None = None,
    ) -> TaskSubmission: ...


def chat_task_idempotency_key(workspace_id: UUID, user_id: UUID, message_id: UUID) -> str:
    """Stable per chat message, so a retried message can never create a second task."""
    digest = hashlib.sha256(f"{workspace_id}|{user_id}|{message_id}".encode()).hexdigest()
    return f"chat-{digest[:32]}"


def queued_label(ahead: int) -> str:
    """The progress label of a task that waits for a free worker."""
    if ahead <= 0:
        return "Queued: next in line"
    return f"Queued: {ahead} task ahead" if ahead == 1 else f"Queued: {ahead} tasks ahead"


def _root_subtask(instruction: str) -> list[SubTask]:
    return [
        SubTask(
            id="step_1",
            assigned_agent="research",
            description=instruction,
            dependencies=[],
            status="pending",
            input_data={},
        )
    ]


async def _attachments_exist(workspace_id: UUID, upload_ids: list[str]) -> bool:
    """True when every id names an upload of this workspace (another workspace's id does not)."""
    from app.files.attachments import AttachmentNotFound, load_attachments  # local: light import

    try:
        await load_attachments(workspace_id, upload_ids, max_chars=1)
    except AttachmentNotFound:
        return False
    return True


async def _attach_file_context(
    nodes: list[SubTask],
    workspace_id: UUID,
    user_id: UUID,
    input_files: list[str],
    instruction: str = "",
) -> list[SubTask]:
    """Put ``input_files`` and ``output_dir_mode`` (folder | artifacts) on every step (ADR-0022).

    A request about an attached file is repaired first (research is never critical, and is not
    the source; see ``attached_source_repairs``). Returns the steps to store.

    The mode is informational: the document agent and the project writer resolve the folder
    again when they run (it may have been connected or removed since), so tasks started by
    voice, which plan elsewhere, route their output the same way.
    """
    from app.agents.plan_repair import attached_source_repairs, drop_invented_edit_targets
    from app.files.services import OutputTarget  # local: light import

    try:
        mode = (await OutputTarget().resolve(workspace_id, user_id)).mode
    except Exception:
        mode = "artifacts"
    if input_files:
        nodes = attached_source_repairs(nodes, instruction, input_files)
    drop_invented_edit_targets(nodes, input_files)
    for node in nodes:
        data = dict(node.input_data or {})
        data["output_dir_mode"] = mode
        if input_files:
            data["input_files"] = list(input_files)
        node.input_data = data
    return nodes


# A follow-up turn ("haan", "make ppt of this") refers to a file sent earlier in the same chat.
_REFERS_TO_FILE = re.compile(
    r"\bthis\b(?!\s+(?:week|month|year|morning|evening|afternoon|quarter|weekend))|"
    r"\b(?:that|it|file|files|attached|attachment|upload|uploaded|document|doc|pdf|ppt|"
    r"pptx|deck|slides?|docx|word|xlsx|excel|csv|sheet|budget)\b|"
    r"\b(?:haan|ha|yes|ok|isko|iska|usko|uska)\b|\bkar (?:do|de|dein)\b|\bkaro\b",
    re.IGNORECASE,
)
# How many of the session's latest user messages are searched for the file they sent.
SESSION_ATTACHMENT_SCAN = 12
# How many of the session's latest messages the required-details backstop reads.
REQUIRED_DETAILS_SCAN = 14


class TaskSubmissionService:
    """Plans, persists and enqueues tasks. Built per request; holds no state of its own."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        gateway: ModelGateway | None,
        queue: SubtaskQueue | None,
    ) -> None:
        self._engine = engine
        self._gateway = gateway
        self._queue = queue

    async def submit(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        idempotency_key: str,
        instruction: str,
        title: str | None,
        session_id: UUID | None,
        budget_usd: Decimal | None = None,
        source: str = "api",
        input_files: Sequence[str] | None = None,
        steps: Sequence[SubTask] | None = None,
        deep_research: dict[str, Any] | None = None,
        plan_filter: PlanFilter | None = None,
    ) -> TaskSubmission:
        """``plan_filter`` (scheduled runs) rewrites the planned steps before they are stored, so
        an unattended task can be limited to safe steps. ``steps`` is a plan the caller already
        built and validated (a chat tool that prepares
        one approval-gated tool step): the planner and the request prechecks are skipped and
        exactly those steps are stored. The worker's permission gate still applies to each."""
        if steps:
            return await self._submit(
                workspace_id=workspace_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
                instruction=instruction,
                title=title,
                session_id=session_id,
                budget_usd=budget_usd,
                source=source,
                steps=list(steps),
            )
        files = list(dict.fromkeys(str(x) for x in (input_files or [])))[:10]
        if not files and session_id is not None and _REFERS_TO_FILE.search(instruction):
            # The follow-up names a file sent earlier in this chat (the chat history keeps the
            # text only, so the model cannot pass its id): the session's latest upload is it.
            files = await self._session_attachments(workspace_id, user_id, session_id)
        if files and not await _attachments_exist(workspace_id, files):
            return TaskRefusal.of(
                Refusal(
                    reason="One of the attached files was not found.",
                    hint="Attach the file again and resend the request.",
                    code="attachment_not_found",
                )
            )
        if session_id is not None and source in ("chat", "voice"):
            # The user's own request must carry its topic and Notion page; the model's
            # instruction never does (a saved preference is not a topic). Fails open on a read
            # error: the chat gate already asked.
            missing = await self._required_details_refusal(
                workspace_id, user_id, session_id, instruction, bool(files)
            )
            if missing is not None:
                logger.info("task_refused_missing_details source=%s", source)
                return TaskRefusal.of(missing)
        early = capabilities.precheck(
            instruction, await capabilities.for_user(workspace_id, user_id), attached=bool(files)
        )
        if early is not None:
            # Decided before anything is written: no task, no failed ghost step.
            logger.info("task_refused_unsupported source=%s", source)
            return TaskRefusal.of(early)
        try:
            return await self._submit(
                workspace_id=workspace_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
                instruction=instruction,
                title=title,
                session_id=session_id,
                budget_usd=budget_usd,
                source=source,
                input_files=files,
                deep_research=deep_research,
                plan_filter=plan_filter,
            )
        except UnsupportedRequestError as unsupported:
            # Raised inside the transaction, so the task row it created was rolled back.
            logger.info("task_refused_by_planner source=%s", source)
            return TaskRefusal.of(unsupported.refusal)

    async def _submit(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        idempotency_key: str,
        instruction: str,
        title: str | None,
        session_id: UUID | None,
        budget_usd: Decimal | None,
        source: str,
        input_files: list[str] | None = None,
        steps: list[SubTask] | None = None,
        deep_research: dict[str, Any] | None = None,
        plan_filter: PlanFilter | None = None,
    ) -> TaskSubmission:
        jobs: list[SubtaskJob] = []
        standing = await self._standing_block(workspace_id, user_id)
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            task, is_new = await TaskIdempotencyService.get_or_create_task(
                session=session,
                workspace_id=workspace_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
                user_instruction=instruction,
                title=title,
                budget_usd=budget_usd,
                session_id=str(session_id) if session_id else None,
            )
            if not is_new:
                return TaskSubmission(
                    task_id=task.id,
                    title=task.title or "",
                    status=task.status or "pending",
                    is_new=False,
                    created_at=task.created_at,
                )

            if steps:
                nodes = list(steps)
            else:
                nodes = await self._plan(
                    instruction, workspace_id, task.id, user_id, attached=bool(input_files)
                )
                nodes = await _attach_file_context(
                    nodes, workspace_id, user_id, input_files or [], instruction
                )
            if plan_filter is not None and not steps:
                nodes = plan_filter(nodes)
            if deep_research is not None and not steps:
                # ADR-0032: the research step becomes the wide, cited deep-research run. An EMPTY
                # spec ({}: no source count requested) still means deep research: it must not be
                # tested for truthiness, or the plain planned research steps would run instead.
                nodes = apply_deep_research(nodes, instruction, deep_research)
            attach_preferences(nodes, standing)
            task.active_plan = [node.model_dump() for node in nodes]
            task.status = "executing"
            task.plan_status = PlanStatus.EXECUTING

            ready: list[tuple[UUID, str]] = []
            for node in nodes:
                agent_str = str(getattr(node.assigned_agent, "value", node.assigned_agent))
                agent_enum = (
                    SubtaskAgent(agent_str)
                    if agent_str in SubtaskAgent._value2member_map_
                    else SubtaskAgent.RESEARCH
                )
                subtask_id = resolve_subtask_uuid(task.id, str(node.id))
                session.add(
                    Subtask(
                        id=subtask_id,
                        task_id=task.id,
                        workspace_id=workspace_id,
                        assigned_agent=agent_enum,
                        description=node.description,
                        dependencies=[
                            str(resolve_subtask_uuid(task.id, str(dep)))
                            for dep in node.dependencies
                        ],
                        status=SubtaskStatus.PENDING,
                        input_data=persisted_input_data(node),
                    )
                )
                if not node.dependencies:
                    ready.append((subtask_id, agent_str))

            task.execution_trace = [{"action": "plan_generated", "subtask_count": len(nodes)}]
            await write_outbox_event(
                session=session,
                workspace_id=workspace_id,
                task_id=task.id,
                event_type=TaskEventType.TASK_PLAN_CREATED,
                payload={
                    "task_id": str(task.id),
                    "instruction": instruction,
                    "subtasks_count": len(nodes),
                },
            )
            await emit_task_step(
                session,
                workspace_id=workspace_id,
                task_id=task.id,
                subtask_id=None,
                step_id=f"plan-{task.id}",
                kind="plan",
                label="Planned the work",
                state="done",
                detail=f"{len(nodes)} steps",
            )
            await self._audit(session, workspace_id, user_id, task.id, source, len(nodes))

            # Every worker is busy (another task is running): this task queues fairly and says so.
            waiting_ahead = getattr(self._queue, "waiting_ahead", None)
            ahead = waiting_ahead() if callable(waiting_ahead) else None
            if ahead is not None:
                await emit_task_step(
                    session,
                    workspace_id=workspace_id,
                    task_id=task.id,
                    subtask_id=None,
                    step_id=f"queued-{task.id}",
                    kind="step",
                    label=queued_label(ahead),
                    state="running",
                )

            jobs = [
                SubtaskJob(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    task_id=task.id,
                    subtask_id=subtask_id,
                    assigned_agent=agent_name,
                    queued_step=ahead is not None,
                )
                for subtask_id, agent_name in ready
            ]
            result = TaskSubmission(
                task_id=task.id,
                title=task.title or "",
                status="executing",
                is_new=True,
                created_at=task.created_at,
                notice=length_notice(list(nodes)),
                step_count=len(nodes),
            )

        # The transaction has committed: only now may a worker look the rows up.
        if self._queue is not None:
            for job in jobs:
                await self._queue.enqueue(job)
        return result

    async def resume_with_input(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        task_id: UUID,
        subtask_id: UUID,
        answers: Sequence[dict[str, Any]],
    ) -> str:
        """Resume a step that paused to ask the user for a detail (``app.tasks.needs_input``).

        The answer only fills the named argument (a Notion parent); the finished work stays and
        the approval payload is built afterwards from the final arguments, so its hash is bound
        to the chosen value. A confirmed "use by default" is stored as a user setting. Returns a
        short plain sentence; an answer with no usable value changes nothing."""
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            subtask = (
                (
                    await session.execute(
                        select(Subtask).where(
                            Subtask.id == subtask_id,
                            Subtask.task_id == task_id,
                            Subtask.workspace_id == workspace_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            data = dict(subtask.input_data or {}) if subtask is not None else {}
            pause = data.get(needs_input.NEEDS_INPUT_KEY)
            if subtask is None or not isinstance(pause, dict):
                return "That question is no longer waiting."
            resolved = needs_input.resolve_answer(pause, answers)
            if resolved is None:
                return "I did not get a page link or a choice, so the task is still waiting."
            hints = dict(data.get("notion_book") or {})
            hints.update(parent_page_id=resolved.value, parent_label=resolved.label)
            data["notion_book"] = hints
            data.pop(needs_input.NEEDS_INPUT_KEY, None)
            subtask.input_data = data
            subtask.status = SubtaskStatus.PENDING
            task = (
                (
                    await session.execute(
                        select(Task).where(Task.id == task_id, Task.workspace_id == workspace_id)
                    )
                )
                .scalars()
                .first()
            )
            if task is not None:
                task.status = "executing"
                task.plan_status = PlanStatus.EXECUTING
            await session.commit()
            agent = str(getattr(subtask.assigned_agent, "value", subtask.assigned_agent))
        if resolved.make_default:
            try:
                await UserSettingsStore(self._engine).set_text(
                    workspace_id,
                    user_id,
                    needs_input.DEFAULT_PARENT_KEY,
                    needs_input.format_default(resolved.value, resolved.label),
                )
            except Exception as exc:  # noqa: BLE001 - the task still resumes
                logger.warning("default_parent_not_saved type=%s", type(exc).__name__)
        if self._queue is not None:
            await self._queue.enqueue(
                SubtaskJob(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    assigned_agent=agent,
                )
            )
        return f"Continuing the task with the Notion page: {resolved.label or 'your link'}."

    async def _required_details_refusal(
        self,
        workspace_id: UUID,
        user_id: UUID,
        session_id: UUID,
        instruction: str,
        has_files: bool,
    ) -> Refusal | None:
        """``needs_clarification`` when the session's latest request lacks a required detail or
        the instruction's topic is not in the conversation (see ``required_details``)."""
        try:
            async with tenant_session(self._engine, workspace_id, user_id) as session:
                found = (
                    await session.execute(
                        select(ChatMessage.sender, ChatMessage.content, ChatMessage.metadata_json)
                        .where(
                            ChatMessage.workspace_id == workspace_id,
                            ChatMessage.session_id == session_id,
                        )
                        .order_by(ChatMessage.created_at.desc())
                        .limit(REQUIRED_DETAILS_SCAN)
                    )
                ).all()
        except Exception as exc:
            logger.warning("task_required_details_read_failed type=%s", type(exc).__name__)
            return None
        rows = [
            (str(sender), str(content or ""), meta if isinstance(meta, dict) else {})
            for sender, content, meta in found
        ]
        refusal = required_details.backstop_refusal(rows, instruction, has_files=has_files)
        if refusal is not None:
            return refusal
        if required_details.instruction_invents_topic(rows, instruction, has_files=has_files):
            return Refusal(
                reason="The topic did not come from the conversation.",
                hint="What should it be about?",
                code="needs_clarification",
                status="needs_clarification",
                question="What should it be about?",
                missing=("topic",),
            )
        return None

    async def _standing_block(self, workspace_id: UUID, user_id: UUID) -> str:
        """The user's saved preferences as a prompt block for the agents, or ``""`` when memory
        is off, empty or unreadable. Never raises: a task never fails over a preference."""
        try:
            if not await UserSettingsStore(self._engine).memory_in_chat_enabled(
                workspace_id, user_id
            ):
                return ""
            return await load_standing_block(self._engine, workspace_id, user_id)
        except Exception as error:
            logger.warning("task_standing_preferences_failed error=%s", type(error).__name__)
            return ""

    async def _session_attachments(
        self, workspace_id: UUID, user_id: UUID, session_id: UUID
    ) -> list[str]:
        """The upload ids of the session's latest user message that had attachments (none when
        there is none or the read fails: the task then runs without the file, as before)."""
        try:
            async with tenant_session(self._engine, workspace_id, user_id) as session:
                rows = (
                    (
                        await session.execute(
                            select(ChatMessage.metadata_json)
                            .where(
                                ChatMessage.workspace_id == workspace_id,
                                ChatMessage.session_id == session_id,
                                ChatMessage.sender == "user",
                            )
                            .order_by(ChatMessage.created_at.desc())
                            .limit(SESSION_ATTACHMENT_SCAN)
                        )
                    )
                    .scalars()
                    .all()
                )
        except Exception as exc:
            logger.warning("task_session_attachments_failed type=%s", type(exc).__name__)
            return []
        for meta in rows:
            chips = meta.get("attachments") if isinstance(meta, dict) else None
            ids = [str(c["id"]) for c in chips or [] if isinstance(c, dict) and c.get("id")]
            if ids:
                return ids[:10]
        return []

    async def _plan(
        self,
        instruction: str,
        workspace_id: UUID,
        task_id: UUID,
        user_id: UUID | None = None,
        *,
        attached: bool = False,
    ) -> list[SubTask]:
        custom = await enabled_agents_for(workspace_id, user_id) if user_id is not None else []
        direct = called_step(instruction, custom)
        if direct is not None:
            # The user addressed a custom agent by name (ADR-0023): one step, no planner call.
            return direct
        if self._gateway is None:
            return _root_subtask(instruction)
        try:
            # GitHub / Notion availability is per user (Settings > Connections): the planner and
            # its prechecks read it through ``capabilities.current()``.
            caps = (
                await capabilities.for_user(workspace_id, user_id)
                if user_id is not None
                else capabilities.current()
            )
            with capabilities.bound(caps):
                _intent, nodes = await DAGPlanner(gateway=self._gateway).plan(
                    instruction=instruction,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    custom_agents=[(a.name, a.description) for a in custom],
                    attached=attached,
                )
        except UnsupportedRequestError:
            raise
        except Exception as exc:
            logger.warning("task_planner_failed type=%s", type(exc).__name__)
            return _root_subtask(instruction)
        return list(nodes) or _root_subtask(instruction)

    @staticmethod
    async def _audit(
        session: Any,
        workspace_id: UUID,
        user_id: UUID,
        task_id: UUID,
        source: str,
        subtasks: int,
    ) -> None:
        try:
            await record_audit(
                session,
                workspace_id=workspace_id,
                user_ref=str(user_id),
                status="success",
                action="task.submitted",
                task_id=task_id,
                input={"source": source},
                output={"subtasks": subtasks},
            )
        except AuditWriteError:
            logger.error("task_submit_audit_write_failed task=%s", task_id)
