"""Voice Tool Binding to Core Task Infrastructure (Decisions D3 & D5).

Connects `submit_task` and `get_task_status` in `VoiceToolRegistry` to PostgreSQL
Task persistence and the live Redis `VoiceEventBridge`.

`submit_task` creates the task row quickly and returns, then plans and dispatches in a background
task so the Live receive loop is never blocked by an LLM planning call. Errors are returned to the
model honestly: no task id is ever invented.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any
from uuid import UUID

from app.agents import capabilities
from app.agents.capabilities import precheck
from app.agents.custom import called_step, enabled_agents_for
from app.agents.planner import DAGPlanner
from app.agents.state import SubTask
from app.db.models import PlanStatus, Subtask, SubtaskAgent, SubtaskStatus, Task
from app.db.tenant import tenant_session
from app.gateway.model_gateway import ModelGateway
from app.research.intent import requested_sources
from app.research.plan import apply_deep_research
from app.services.projection_service import resolve_subtask_uuid
from app.tasks.events import TaskEventType
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.outbox import write_outbox_event
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.voice.event_bridge import VoiceEventBridge
from app.voice.tools import VoiceExecutionContext, VoiceToolRegistry


class _Planned(Exception):
    """Internal: a custom agent was addressed by name, so the planner is skipped."""


logger = logging.getLogger(__name__)


def task_status_statement(status: object) -> str:
    """A plain statement of a task's real status for the model, so it never has to infer one.

    Only ``completed`` may be described as done; every other status says what it actually is.
    """
    value = str(getattr(status, "value", status) or "unknown").lower()
    if value == "completed":
        return "completed: the task finished."
    if value == "failed":
        return "failed: the task did not complete. Tell the user it failed; never say it succeeded."
    if value in ("cancelled", "canceled"):
        return "cancelled: the task was stopped and did not complete."
    return f"{value}: the task has NOT finished. Do not say it is done, completed or successful."


def voice_idempotency_key(intent: str, context: VoiceExecutionContext) -> str:
    """Stable for one provider call (a retry reuses it); falls back to the intent text."""
    basis = context.call_id or f"intent:{intent}"
    digest = hashlib.sha256(
        f"{context.scope or ''}|{context.session_id}|{basis}".encode()
    ).hexdigest()
    return f"voice-{digest[:32]}"


def wire_voice_tools(
    tool_registry: VoiceToolRegistry,
    event_bridge: VoiceEventBridge | None,
    db_engine: Any,
    *,
    model_gateway: ModelGateway | None = None,
    queue: SubtaskQueue | None = None,
) -> set[asyncio.Task[None]]:
    """Inject task submitter and status getter handlers. Returns the live background planning
    tasks (so the caller can cancel them when the connection ends)."""
    background: set[asyncio.Task[None]] = set()

    async def _mark_failed(task_id: UUID, context: VoiceExecutionContext) -> None:
        try:
            async with tenant_session(db_engine, context.workspace_id, context.user_id) as session:
                task = await session.get(Task, task_id)
                if task is None:
                    return
                task.status = "failed"
                task.plan_status = PlanStatus.FAILED
                await write_outbox_event(
                    session=session,
                    workspace_id=context.workspace_id,
                    task_id=task.id,
                    event_type=TaskEventType.TASK_FAILED,
                    payload={"task_id": str(task.id), "error": "The task could not be planned."},
                )
                await session.commit()
        except Exception as err:
            logger.error("voice_task_mark_failed_error type=%s", type(err).__name__)

    async def _plan_and_dispatch(
        task_id: UUID,
        intent: str,
        context: VoiceExecutionContext,
        deep_research: dict[str, Any] | None = None,
    ) -> None:
        ready_subtasks: list[tuple[UUID, str]] = []
        try:
            async with tenant_session(db_engine, context.workspace_id, context.user_id) as session:
                task = await session.get(Task, task_id)
                if task is None:
                    return
                subtask_nodes: list[Any] = []
                if model_gateway is not None:
                    planner = DAGPlanner(gateway=model_gateway)
                    custom = await enabled_agents_for(context.workspace_id, context.user_id)
                    try:
                        # Addressing a custom agent by name is one step, no planner call.
                        subtask_nodes = called_step(intent, custom) or []
                        if subtask_nodes:
                            raise _Planned
                        user_caps = await capabilities.for_user(
                            context.workspace_id, context.user_id
                        )
                        with capabilities.bound(user_caps):
                            _intent, subtask_nodes = await planner.plan(
                                instruction=intent,
                                workspace_id=context.workspace_id,
                                task_id=task.id,
                                custom_agents=[(a.name, a.description) for a in custom],
                            )
                    except _Planned:
                        pass
                    except Exception as exc:
                        logger.warning("voice_planner_failed type=%s", type(exc).__name__)
                if not subtask_nodes:
                    subtask_nodes = [
                        SubTask(
                            id="step_1",
                            assigned_agent="research",
                            description=intent,
                            dependencies=[],
                            status="pending",
                            input_data={"instruction": intent},
                        )
                    ]
                if deep_research is not None:
                    # ADR-0032: the research step becomes the wide, cited deep-research run.
                    subtask_nodes = apply_deep_research(subtask_nodes, intent, deep_research)
                task.active_plan = [node.model_dump() for node in subtask_nodes]
                task.status = "executing"
                task.plan_status = PlanStatus.EXECUTING
                for node in subtask_nodes:
                    agent_str = str(getattr(node.assigned_agent, "value", node.assigned_agent))
                    agent_enum = (
                        SubtaskAgent(agent_str)
                        if agent_str in SubtaskAgent._value2member_map_
                        else SubtaskAgent.RESEARCH
                    )
                    st_uuid = resolve_subtask_uuid(task.id, str(node.id))
                    dep_uuids = [
                        str(resolve_subtask_uuid(task.id, str(dep))) for dep in node.dependencies
                    ]
                    session.add(
                        Subtask(
                            id=st_uuid,
                            task_id=task.id,
                            workspace_id=context.workspace_id,
                            assigned_agent=agent_enum,
                            description=node.description,
                            dependencies=dep_uuids,
                            status=SubtaskStatus.PENDING,
                            input_data=node.input_data,
                        )
                    )
                    if not node.dependencies:
                        ready_subtasks.append((st_uuid, agent_str))
                task.execution_trace = [
                    {"action": "plan_generated", "subtask_count": len(subtask_nodes)}
                ]
                await write_outbox_event(
                    session=session,
                    workspace_id=context.workspace_id,
                    task_id=task.id,
                    event_type=TaskEventType.TASK_PLAN_CREATED,
                    payload={
                        "task_id": str(task.id),
                        "instruction": intent,
                        "subtasks_count": len(subtask_nodes),
                    },
                )
                await session.commit()
            if queue is not None:
                for ready_id, agent_name in ready_subtasks:
                    await queue.enqueue(
                        SubtaskJob(
                            workspace_id=context.workspace_id,
                            user_id=context.user_id,
                            task_id=task_id,
                            subtask_id=ready_id,
                            assigned_agent=agent_name,
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.error("voice_task_plan_error type=%s", type(err).__name__, exc_info=True)
            await _mark_failed(task_id, context)

    async def _submit_task(
        intent: str,
        params: dict[str, Any],
        context: VoiceExecutionContext,
    ) -> dict[str, Any]:
        intent = intent.strip()
        if not intent:
            return {"status": "error", "error": "empty_intent", "message": "No task was given."}
        # The task service's deterministic refusals (a deliverable with no subject, or private
        # data this deployment cannot reach) come back as ``{ok: false, status, reason, hint}``;
        # the model relays them and no task row is created.
        refusal = precheck(
            intent, await capabilities.for_user(context.workspace_id, context.user_id)
        )
        if refusal is not None:
            return refusal.as_result()
        if db_engine is None:
            return {
                "status": "error",
                "error": "tasks_unavailable",
                "message": "Background tasks are not available right now. Nothing was started.",
            }
        try:
            async with tenant_session(db_engine, context.workspace_id, context.user_id) as session:
                task, is_new = await TaskIdempotencyService.get_or_create_task(
                    session=session,
                    workspace_id=context.workspace_id,
                    user_id=context.user_id,
                    idempotency_key=voice_idempotency_key(intent, context),
                    user_instruction=intent,
                    title=intent[:60],
                    session_id=str(context.session_id),
                )
                task_id = task.id
                status = task.status or "pending"
                await session.commit()
        except Exception as err:
            logger.error("voice_task_submit_error type=%s", type(err).__name__, exc_info=True)
            return {
                "status": "error",
                "error": "task_submission_failed",
                "message": "The task could not be created. Nothing was started.",
            }

        if event_bridge is not None:
            event_bridge.track_task(str(task_id))
        if is_new:
            deep_spec: dict[str, Any] | None = None
            if params.get("deep_research"):
                sites = requested_sources(intent)
                deep_spec = {"sources": sites} if sites else {}
            planning = asyncio.create_task(_plan_and_dispatch(task_id, intent, context, deep_spec))
            background.add(planning)
            planning.add_done_callback(background.discard)
            status = "planning"
        logger.info("voice_task_submitted task_id=%s new=%s", task_id, is_new)
        if params.get("deep_research"):
            return {
                "task_id": str(task_id),
                "status": status,
                "message": (
                    "Deep research started: it reads many different sites and can take several "
                    "minutes. Say that in one sentence and that findings come only when it "
                    "finishes. Do not share any findings now."
                ),
            }
        return {
            "task_id": str(task_id),
            "status": status,
            "message": "Task accepted; planning continues in the background.",
        }

    async def _get_task_status(
        task_id: str,
        context: VoiceExecutionContext,
    ) -> dict[str, Any]:
        """Query live status of a background task."""
        if db_engine is None:
            return {"task_id": task_id, "status": "error", "error": "tasks_unavailable"}

        try:
            tid = UUID(task_id)
        except (ValueError, TypeError):
            return {"task_id": task_id, "status": "not_found", "error": "Invalid task ID"}

        try:
            async with tenant_session(db_engine, context.workspace_id, context.user_id) as session:
                task = await session.get(Task, tid)
                if task is None or task.workspace_id != context.workspace_id:
                    return {"task_id": task_id, "status": "not_found"}

                return {
                    "task_id": task_id,
                    "status": task.status,
                    "statement": task_status_statement(task.status),
                    "title": task.title,
                    "spent_usd": float(task.spent_usd or 0.0),
                }
        except Exception as err:
            logger.error("voice_task_get_status_error type=%s", type(err).__name__)
            return {"task_id": task_id, "status": "error", "error": "status_unavailable"}

    tool_registry.set_task_submitter(_submit_task)
    tool_registry.set_status_getter(_get_task_status)
    return background
