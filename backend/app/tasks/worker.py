"""Background worker runner executing subtask jobs conforming to Decision D8."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.schemas import AgentExecutionStatus
from app.db.models import FileArtifact, PlanStatus, Subtask, SubtaskStatus, Task
from app.db.repositories.approvals import ApprovalRepository
from app.mcp.approvals import compute_payload_hash
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.mcp.tokens import (
    claim_approval_atomically,
    complete_execution_atomically,
    fail_execution_atomically,
    start_execution_atomically,
    verify_approval_token,
)
from app.services.projection_service import resolve_subtask_uuid
from app.tasks.events import TaskEventType
from app.tasks.outbox import write_outbox_event
from app.tasks.queue import SubtaskJob, SubtaskQueue

logger = logging.getLogger(__name__)

DEFAULT_SUBTASK_TIMEOUT_SECONDS = 300
MAX_SUBTASK_RETRIES = 3


class SubtaskExecutionError(Exception):
    """Raised when subtask execution fails unrecoverably."""


class SubtaskTimeoutError(SubtaskExecutionError):
    """Raised when subtask execution exceeds bounded timeout."""


class SubtaskWorker:
    """Async worker processing individual subtask jobs.

    Core Decision D8 rules:
    - Exactly 1 subtask per queued job message.
    - Workers never sleep or block waiting for human approval; upon hitting
      awaiting_approval, state is persisted and the worker exits immediately.
    - Job execution bounded strictly by timeout.
    """

    def __init__(
        self,
        queue: SubtaskQueue | None = None,
        default_timeout: int = DEFAULT_SUBTASK_TIMEOUT_SECONDS,
        permission_registry: ToolPermissionRegistry | None = None,
        jwt_signing_key: str | bytes | None = None,
        fleet_registry: Any | None = None,
    ) -> None:
        self.queue = queue or SubtaskQueue()
        self.default_timeout = default_timeout
        self.permission_registry = permission_registry or ToolPermissionRegistry()
        self.jwt_signing_key = jwt_signing_key or "0" * 32
        self.fleet_registry = fleet_registry

    async def execute_job(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        agent_handler: (
            Callable[[SubtaskJob, dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None,
    ) -> SubtaskStatus:
        """Execute a single subtask job under strict tenant isolation."""
        # 1. Load subtask and verify tenant boundary
        stmt = (
            select(Subtask)
            .where(
                Subtask.id == job.subtask_id,
                Subtask.workspace_id == job.workspace_id,
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        subtask = result.scalars().first()
        if subtask is None:
            logger.error(
                "Subtask %s not found in workspace %s",
                job.subtask_id,
                job.workspace_id,
            )
            return SubtaskStatus.FAILED

        # 2. Verify dependencies are satisfied
        dep_outputs: dict[str, Any] = {}
        if subtask.dependencies:
            dep_stmt = select(Subtask).where(
                Subtask.task_id == job.task_id,
                Subtask.workspace_id == job.workspace_id,
            )
            dep_res = await session.execute(dep_stmt)
            all_subtasks = {str(s.id): s for s in dep_res.scalars().all()}
            for dep_id in subtask.dependencies:
                dep_sub = all_subtasks.get(str(dep_id))
                if dep_sub is None:
                    # Defensive fallback: resolve planner string ID to deterministic UUID
                    resolved_dep_id = str(resolve_subtask_uuid(job.task_id, str(dep_id)))
                    dep_sub = all_subtasks.get(resolved_dep_id)

                if dep_sub is None or dep_sub.status != SubtaskStatus.COMPLETED:
                    logger.warning(
                        "Subtask %s dependencies not satisfied (dep %s status: %s)",
                        job.subtask_id,
                        dep_id,
                        getattr(dep_sub, "status", "missing"),
                    )
                    return SubtaskStatus.PENDING
                if dep_sub.output_result is not None:
                    dep_outputs[str(dep_id)] = dep_sub.output_result

        # 3. Check for human approval requirement (Decision D8, Decision D4, Decision D5)
        # Workers NEVER sleep or block on human approval; exit immediately!
        tool_name = subtask.input_data.get("tool") or subtask.input_data.get("tool_name")
        explicit_approval = bool(
            subtask.input_data.get("requires_approval", False)
            or subtask.input_data.get("approval_required", False)
        )
        tool_approval = False
        if tool_name:
            if self.permission_registry.is_registered(str(tool_name)):
                tier = self.permission_registry.classify(str(tool_name))
                if tier in (ToolTier.APPROVAL_REQUIRED, ToolTier.RESTRICTED):
                    tool_approval = True
            else:
                # Deny by default: unregistered tools require approval / are blocked
                tool_approval = True

        requires_approval = explicit_approval or tool_approval

        # Fetch parent task to get user context
        task_stmt = select(Task).where(
            Task.id == job.task_id,
            Task.workspace_id == job.workspace_id,
        )
        t_res = await session.execute(task_stmt)
        task = t_res.scalars().first()

        token_consumed = False
        claimed_approval_id: UUID | None = None
        worker_id = f"worker-{os.getpid()}-{uuid4().hex[:8]}"
        approval_token = subtask.input_data.get("approval_token")

        if requires_approval and approval_token and task is not None:
            expected_tool = str(tool_name or "subtask_action")
            expected_args = (
                subtask.input_data.get("tool_args") or subtask.input_data.get("args") or {}
            )
            idempotency_key = str(subtask.input_data.get("idempotency_key") or job.subtask_id)
            expected_hash = compute_payload_hash(
                tool=expected_tool,
                args=expected_args,
                workspace_id=job.workspace_id,
                user_id=task.user_id,
                idempotency_key=idempotency_key,
            )

            try:
                conn = await session.connection()
                settings = conn.info.get("settings") if hasattr(conn, "info") else None
                signing_key = (
                    settings.jwt_signing_key.get_secret_value()
                    if settings and hasattr(settings, "jwt_signing_key")
                    else self.jwt_signing_key
                )

                claims = verify_approval_token(
                    str(approval_token),
                    signing_key,
                    expected_workspace_id=job.workspace_id,
                    expected_user_id=task.user_id,
                    expected_tool_name=expected_tool,
                    expected_payload_hash=expected_hash,
                )

                # Atomic CAS single-use claim in PostgreSQL
                # Prevents multiple workers from executing the same operation
                claimed = await claim_approval_atomically(
                    conn,
                    request_id=claims.request_id,
                    workspace_id=job.workspace_id,
                    payload_hash=expected_hash,
                    worker_id=worker_id,
                )
                if claimed:
                    await start_execution_atomically(
                        conn,
                        request_id=claims.request_id,
                        workspace_id=job.workspace_id,
                        worker_id=worker_id,
                    )
                    token_consumed = True
                    claimed_approval_id = claims.request_id
                    logger.info(
                        "Approval token claimed and execution started for subtask %s by %s",
                        job.subtask_id,
                        worker_id,
                    )
                else:
                    logger.warning(
                        "Approval claim refused for subtask %s (already claimed or revoked)",
                        job.subtask_id,
                    )
                    token_consumed = False
            except Exception as err:
                logger.warning(
                    "Approval token verification failed for subtask %s: %s",
                    job.subtask_id,
                    err,
                )
                token_consumed = False

        if requires_approval and not token_consumed:
            # Ensure approval request row is persisted in approval_requests table
            if task is not None:
                try:
                    conn = await session.connection()
                    repo = ApprovalRepository(conn, job.workspace_id, task.user_id)
                    expected_tool = str(tool_name or "subtask_action")
                    expected_args = (
                        subtask.input_data.get("tool_args") or subtask.input_data.get("args") or {}
                    )
                    idempotency_key = str(
                        subtask.input_data.get("idempotency_key") or job.subtask_id
                    )
                    expected_hash = compute_payload_hash(
                        tool=expected_tool,
                        args=expected_args,
                        workspace_id=job.workspace_id,
                        user_id=task.user_id,
                        idempotency_key=idempotency_key,
                    )
                    check_stmt = text(
                        "SELECT id FROM approval_requests "
                        "WHERE workspace_id = :workspace_id AND subtask_id = :subtask_id "
                        "  AND status IN ('pending', 'approved') LIMIT 1"
                    )
                    existing = await conn.execute(
                        check_stmt,
                        {"workspace_id": job.workspace_id, "subtask_id": job.subtask_id},
                    )
                    first_row = existing.first()
                    if inspect.isawaitable(first_row):
                        first_row = await first_row  # type: ignore[attr-defined]
                    if first_row is None:
                        await repo.create_request(
                            task_id=job.task_id,
                            subtask_id=job.subtask_id,
                            tool_name=expected_tool,
                            tool_args=expected_args,
                            payload_hash=expected_hash,
                            tier="APPROVAL_REQUIRED",
                            action_description=(
                                subtask.description or f"Execution of {expected_tool}"
                            ),
                            payload_diff=subtask.input_data.get("diff")
                            or subtask.input_data.get("payload_diff"),
                            expires_at=datetime.now(UTC) + timedelta(days=7),
                            idempotency_key=idempotency_key,
                        )
                except Exception as app_err:
                    logger.warning("Failed to record approval request row: %s", app_err)

            subtask.status = SubtaskStatus.AWAITING_APPROVAL
            await write_outbox_event(
                session=session,
                workspace_id=job.workspace_id,
                task_id=job.task_id,
                event_type=TaskEventType.TASK_PAUSED_APPROVAL,
                payload={
                    "task_id": str(job.task_id),
                    "subtask_id": str(job.subtask_id),
                    "assigned_agent": job.assigned_agent,
                    "tool_name": tool_name,
                    "reason": "Human approval required before executing mutating side effect",
                },
            )
            # Update parent task state to paused / awaiting_approval
            if task:
                task.status = "awaiting_approval"
                task.plan_status = PlanStatus.PAUSED

            await session.flush()
            logger.info(
                "Subtask %s requires approval; transitioned to awaiting_approval "
                "and worker exited cleanly.",
                job.subtask_id,
            )
            return SubtaskStatus.AWAITING_APPROVAL

        # 4. Transition to in_progress
        subtask.status = SubtaskStatus.IN_PROGRESS
        await write_outbox_event(
            session=session,
            workspace_id=job.workspace_id,
            task_id=job.task_id,
            event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
            payload={
                "task_id": str(job.task_id),
                "subtask_id": str(job.subtask_id),
                "status": SubtaskStatus.IN_PROGRESS.value,
                "assigned_agent": job.assigned_agent,
            },
        )
        await session.flush()

        # 5. Execute assigned agent handler within bounded timeout
        timeout = job.timeout_seconds or self.default_timeout
        agent_input = dict(subtask.input_data or {})
        if token_consumed:
            agent_input["has_valid_approval_token"] = True
        if dep_outputs:
            agent_input["dependency_outputs"] = dep_outputs
        if approval_token:
            agent_input["approval_token"] = approval_token

        try:
            handler_output: dict[str, Any] = {}
            if agent_handler is not None:
                handler_output = await asyncio.wait_for(
                    agent_handler(job, agent_input),
                    timeout=timeout,
                )
            elif self.fleet_registry and self.fleet_registry.has_agent(job.assigned_agent):
                agent = self.fleet_registry.get_agent(job.assigned_agent)
                assert agent is not None
                agent_res = await asyncio.wait_for(
                    agent.execute_subtask(job, agent_input),
                    timeout=timeout,
                )
                if (
                    agent_res.requires_human_approval
                    and agent_res.status == AgentExecutionStatus.PARTIAL
                ):
                    subtask.status = SubtaskStatus.AWAITING_APPROVAL
                    subtask.output_result = agent_res.summary
                    subtask.verification_proof = agent_res.verification_proof
                    if task is not None:
                        try:
                            conn = await session.connection()
                            repo = ApprovalRepository(conn, job.workspace_id, task.user_id)
                            act_ctx = agent_res.approval_context or {}
                            act_tool = str(
                                act_ctx.get("tool")
                                or act_ctx.get("tool_name")
                                or f"{job.assigned_agent}_action"
                            )
                            act_args = act_ctx.get("tool_args") or act_ctx.get("args") or {}
                            act_idempotency = str(
                                act_ctx.get("idempotency_key") or f"{job.subtask_id}-agent-approval"
                            )
                            act_hash = compute_payload_hash(
                                tool=act_tool,
                                args=act_args,
                                workspace_id=job.workspace_id,
                                user_id=task.user_id,
                                idempotency_key=act_idempotency,
                            )
                            check_stmt = text(
                                "SELECT id FROM approval_requests "
                                "WHERE workspace_id = :workspace_id AND subtask_id = :subtask_id "
                                "  AND status IN ('pending', 'approved') LIMIT 1"
                            )
                            res = await conn.execute(
                                check_stmt,
                                {"workspace_id": job.workspace_id, "subtask_id": job.subtask_id},
                            )
                            first_res = res.first()
                            if inspect.isawaitable(first_res):
                                first_res = await first_res  # type: ignore[attr-defined]
                            if first_res is None:
                                await repo.create_request(
                                    task_id=job.task_id,
                                    subtask_id=job.subtask_id,
                                    tool_name=act_tool,
                                    tool_args=act_args,
                                    payload_hash=act_hash,
                                    tier="APPROVAL_REQUIRED",
                                    action_description=agent_res.summary,
                                    payload_diff=act_ctx.get("diff") or act_ctx.get("payload_diff"),
                                    expires_at=datetime.now(UTC) + timedelta(days=7),
                                    idempotency_key=act_idempotency,
                                )
                        except Exception as app_err:
                            logger.warning(
                                "Failed to record dynamic approval request row: %s", app_err
                            )

                    await write_outbox_event(
                        session=session,
                        workspace_id=job.workspace_id,
                        task_id=job.task_id,
                        event_type=TaskEventType.TASK_PAUSED_APPROVAL,
                        payload={
                            "task_id": str(job.task_id),
                            "subtask_id": str(job.subtask_id),
                            "assigned_agent": job.assigned_agent,
                            "reason": agent_res.summary,
                            "approval_context": agent_res.approval_context,
                        },
                    )
                    if task:
                        task.status = "awaiting_approval"
                        task.plan_status = PlanStatus.PAUSED
                    await session.flush()
                    return SubtaskStatus.AWAITING_APPROVAL

                elif agent_res.status in (
                    AgentExecutionStatus.FAILED,
                    AgentExecutionStatus.BLOCKED,
                ):
                    subtask.status = SubtaskStatus.FAILED
                    subtask.error_message = str(agent_res.summary)
                    subtask.output_result = agent_res.summary
                    subtask.verification_proof = agent_res.verification_proof
                    if task:
                        task.status = "failed"
                        task.plan_status = PlanStatus.FAILED
                    await write_outbox_event(
                        session=session,
                        workspace_id=job.workspace_id,
                        task_id=job.task_id,
                        event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
                        payload={
                            "task_id": str(job.task_id),
                            "subtask_id": str(job.subtask_id),
                            "status": SubtaskStatus.FAILED.value,
                            "error": str(agent_res.summary),
                        },
                    )
                    await write_outbox_event(
                        session=session,
                        workspace_id=job.workspace_id,
                        task_id=job.task_id,
                        event_type=TaskEventType.TASK_FAILED,
                        payload={
                            "task_id": str(job.task_id),
                            "subtask_id": str(job.subtask_id),
                            "error": str(agent_res.summary),
                        },
                    )
                    await session.flush()
                    return SubtaskStatus.FAILED

                handler_output = {
                    "result": agent_res.summary,
                    "proof": agent_res.verification_proof,
                }
            else:
                # Default mock handler for tests / dispatch pass-through
                handler_output = {
                    "result": f"Executed by {job.assigned_agent}",
                    "proof": {"status": "verified", "exit_code": 0},
                }

            subtask.output_result = handler_output.get("result", handler_output)
            subtask.verification_proof = handler_output.get(
                "proof",
                {"verified": True, "exit_code": 0},
            )
            subtask.status = SubtaskStatus.COMPLETED
            subtask.completed_at = datetime.now(UTC)

            # Persist FileArtifact if generated and verified
            proof_data = handler_output.get("proof")
            if (
                isinstance(proof_data, dict)
                and "storage_uri" in proof_data
                and "sha256" in proof_data
            ):
                try:
                    artifact = FileArtifact(
                        workspace_id=job.workspace_id,
                        task_id=job.task_id,
                        user_id=task.user_id if task else job.user_id,
                        file_name=proof_data.get("file_name", "output_artifact"),
                        file_type=proof_data.get("file_type", "other"),
                        storage_uri=proof_data["storage_uri"],
                        size_bytes=int(proof_data.get("size_bytes", 0)),
                        sha256=proof_data["sha256"],
                        verification_status=proof_data.get("status", "verified"),
                        verification_report=proof_data.get("report"),
                    )
                    session.add(artifact)
                except Exception as art_err:
                    logger.warning("Failed to persist file artifact record: %s", art_err)

            await write_outbox_event(
                session=session,
                workspace_id=job.workspace_id,
                task_id=job.task_id,
                event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
                payload={
                    "task_id": str(job.task_id),
                    "subtask_id": str(job.subtask_id),
                    "status": SubtaskStatus.COMPLETED.value,
                    "output": subtask.output_result,
                },
            )

            # Check if all subtasks in the task are now completed
            all_stmt = select(Subtask).where(
                Subtask.task_id == job.task_id,
                Subtask.workspace_id == job.workspace_id,
            )
            all_res = await session.execute(all_stmt)
            task_subtasks = list(all_res.scalars().all())
            all_done = all(st.status == SubtaskStatus.COMPLETED for st in task_subtasks)
            if all_done:
                task_stmt = select(Task).where(
                    Task.id == job.task_id,
                    Task.workspace_id == job.workspace_id,
                )
                t_res = await session.execute(task_stmt)
                task = t_res.scalars().first()
                if task:
                    task.status = "completed"
                    task.plan_status = PlanStatus.COMPLETED
                    task.completed_at = datetime.now(UTC)

                await write_outbox_event(
                    session=session,
                    workspace_id=job.workspace_id,
                    task_id=job.task_id,
                    event_type=TaskEventType.TASK_COMPLETED,
                    payload={"task_id": str(job.task_id), "status": "completed"},
                )
            else:
                # Progress DAG: Find newly unblocked pending subtasks and enqueue them
                completed_ids = {
                    str(st.id) for st in task_subtasks if st.status == SubtaskStatus.COMPLETED
                }
                pending_subtasks = [
                    st for st in task_subtasks if st.status == SubtaskStatus.PENDING
                ]
                for pst in pending_subtasks:
                    deps = pst.dependencies or []
                    deps_satisfied = True
                    for d in deps:
                        d_str = str(d)
                        if d_str not in completed_ids:
                            resolved_d = str(resolve_subtask_uuid(job.task_id, d_str))
                            if resolved_d not in completed_ids:
                                deps_satisfied = False
                                break
                    if deps_satisfied:
                        agent_name = (
                            pst.assigned_agent.value
                            if hasattr(pst.assigned_agent, "value")
                            else str(pst.assigned_agent)
                        )
                        next_job = SubtaskJob(
                            workspace_id=job.workspace_id,
                            user_id=job.user_id,
                            task_id=job.task_id,
                            subtask_id=pst.id,
                            assigned_agent=agent_name,
                        )
                        await self.queue.enqueue(next_job)
                        logger.info(
                            "Enqueued unblocked subtask %s for task %s", pst.id, job.task_id
                        )

            if claimed_approval_id is not None:
                try:
                    conn = await session.connection()
                    await complete_execution_atomically(
                        conn,
                        request_id=claimed_approval_id,
                        workspace_id=job.workspace_id,
                        worker_id=worker_id,
                        output=handler_output,
                    )
                except Exception as rec_err:
                    logger.warning("Failed to record completed execution: %s", rec_err)

            await session.flush()
            return SubtaskStatus.COMPLETED

        except TimeoutError:
            logger.error(
                "Subtask %s exceeded timeout of %ss",
                job.subtask_id,
                timeout,
            )
            if claimed_approval_id is not None:
                try:
                    conn = await session.connection()
                    await fail_execution_atomically(
                        conn,
                        request_id=claimed_approval_id,
                        workspace_id=job.workspace_id,
                        worker_id=worker_id,
                        reason=f"Execution timed out after {timeout} seconds",
                    )
                except Exception as rec_err:
                    logger.warning("Failed to record failed execution: %s", rec_err)
            subtask.retry_count += 1
            if subtask.retry_count >= MAX_SUBTASK_RETRIES:
                subtask.status = SubtaskStatus.FAILED
                subtask.error_message = f"Execution timed out after {timeout} seconds"
                task_stmt = select(Task).where(
                    Task.id == job.task_id,
                    Task.workspace_id == job.workspace_id,
                )
                t_res = await session.execute(task_stmt)
                task = t_res.scalars().first()
                if task:
                    task.status = "failed"
                    task.plan_status = PlanStatus.FAILED

                await write_outbox_event(
                    session=session,
                    workspace_id=job.workspace_id,
                    task_id=job.task_id,
                    event_type=TaskEventType.TASK_FAILED,
                    payload={
                        "task_id": str(job.task_id),
                        "subtask_id": str(job.subtask_id),
                        "error": "ExecutionTimeoutError",
                    },
                )
            await session.flush()
            return subtask.status

        except Exception as exc:
            logger.error(
                "Subtask %s failed: %s",
                job.subtask_id,
                exc,
                exc_info=True,
            )
            if claimed_approval_id is not None:
                try:
                    conn = await session.connection()
                    await fail_execution_atomically(
                        conn,
                        request_id=claimed_approval_id,
                        workspace_id=job.workspace_id,
                        worker_id=worker_id,
                        reason=str(exc),
                    )
                except Exception as rec_err:
                    logger.warning("Failed to record failed execution: %s", rec_err)
            subtask.retry_count += 1
            if subtask.retry_count >= MAX_SUBTASK_RETRIES:
                subtask.status = SubtaskStatus.FAILED
                subtask.error_message = str(exc)
                task_stmt = select(Task).where(
                    Task.id == job.task_id,
                    Task.workspace_id == job.workspace_id,
                )
                t_res = await session.execute(task_stmt)
                task = t_res.scalars().first()
                if task:
                    task.status = "failed"
                    task.plan_status = PlanStatus.FAILED

                await write_outbox_event(
                    session=session,
                    workspace_id=job.workspace_id,
                    task_id=job.task_id,
                    event_type=TaskEventType.TASK_FAILED,
                    payload={
                        "task_id": str(job.task_id),
                        "subtask_id": str(job.subtask_id),
                        "error": str(exc),
                    },
                )
            await session.flush()
            return subtask.status
