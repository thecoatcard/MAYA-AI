"""Saga compensation engine and rollback manager for external side effects."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Compensation, PlanStatus, Task

logger = logging.getLogger(__name__)

# Tools that cannot be programmatically rolled back without user action
IRREVERSIBLE_TOOLS = frozenset(
    {
        "gmail_send",
        "email_send",
        "slack_post_message",
        "stripe_charge",
    }
)


class SagaCompensationResult:
    """Summary of saga compensation execution."""

    def __init__(
        self,
        task_id: UUID,
        total_compensations: int,
        executed: int,
        not_applicable: int,
        failed: int,
        notifications: list[str],
    ) -> None:
        self.task_id = task_id
        self.total_compensations = total_compensations
        self.executed = executed
        self.not_applicable = not_applicable
        self.failed = failed
        self.notifications = notifications

    @property
    def is_successful(self) -> bool:
        return self.failed == 0


class SagaManager:
    """Manages forward compensation registration and reverse-order rollbacks."""

    @staticmethod
    async def register_compensation(
        session: AsyncSession,
        workspace_id: UUID,
        task_id: UUID,
        forward_tool: str,
        forward_ref: dict[str, Any],
        compensating_tool: str | None = None,
        compensating_args: dict[str, Any] | None = None,
        permission_level: str = "CONTROLLED_WRITE",
        subtask_id: UUID | None = None,
    ) -> Compensation:
        """Register a compensating action for a forward side effect."""
        is_irreversible = forward_tool in IRREVERSIBLE_TOOLS or compensating_tool is None
        comp_tool = None if is_irreversible else compensating_tool
        comp_args = None if is_irreversible else (compensating_args or {})

        compensation = Compensation(
            workspace_id=workspace_id,
            task_id=task_id,
            subtask_id=subtask_id,
            forward_tool=forward_tool,
            forward_ref=forward_ref,
            compensating_tool=comp_tool,
            compensating_args=comp_args,
            permission_level=permission_level,
            status="registered",
        )
        session.add(compensation)
        await session.flush()
        return compensation

    @staticmethod
    async def list_compensations(
        session: AsyncSession,
        workspace_id: UUID,
        task_id: UUID,
    ) -> list[Compensation]:
        """List all compensations registered for a task in reverse order of creation."""
        stmt = (
            select(Compensation)
            .where(
                Compensation.workspace_id == workspace_id,
                Compensation.task_id == task_id,
            )
            .order_by(Compensation.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def execute_rollback(
        session: AsyncSession,
        workspace_id: UUID,
        task_id: UUID,
        tool_executor: (Callable[[str, dict[str, Any]], Awaitable[bool]] | None) = None,
    ) -> SagaCompensationResult:
        """Execute all registered compensations in reverse order.

        Irreversible actions (e.g. gmail_send) generate human-review notifications
        and transition to 'not_applicable' without halting the remaining rollbacks.
        """
        compensations = await SagaManager.list_compensations(
            session=session,
            workspace_id=workspace_id,
            task_id=task_id,
        )

        executed = 0
        not_applicable = 0
        failed = 0
        notifications: list[str] = []

        for comp in compensations:
            if comp.status in ("executed", "not_applicable"):
                continue

            # Check if irreversible
            if comp.compensating_tool is None or comp.forward_tool in IRREVERSIBLE_TOOLS:
                comp.status = "not_applicable"
                note = (
                    f"Irreversible action '{comp.forward_tool}' detected with ref "
                    f"{comp.forward_ref}. Flagged for human review."
                )
                notifications.append(note)
                not_applicable += 1
                continue

            # Execute reversible compensation
            try:
                success = True
                if tool_executor is not None:
                    success = await tool_executor(
                        comp.compensating_tool,
                        comp.compensating_args or {},
                    )

                if success:
                    comp.status = "executed"
                    executed += 1
                else:
                    comp.status = "failed"
                    failed += 1
            except Exception as exc:
                logger.error(
                    "Compensation %s (%s) failed during rollback: %s",
                    comp.id,
                    comp.compensating_tool,
                    exc,
                    exc_info=True,
                )
                comp.status = "failed"
                failed += 1

        # Update task status to cancelled / compensating
        task_stmt = (
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id).limit(1)
        )
        task_res = await session.execute(task_stmt)
        task = task_res.scalars().first()
        if task:
            task.status = "cancelled"
            task.plan_status = PlanStatus.FAILED

        await session.flush()

        return SagaCompensationResult(
            task_id=task_id,
            total_compensations=len(compensations),
            executed=executed,
            not_applicable=not_applicable,
            failed=failed,
            notifications=notifications,
        )
