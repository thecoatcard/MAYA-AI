"""Per-tenant idempotency enforcement for task submission."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlanStatus, Task

logger = logging.getLogger(__name__)


class TaskIdempotencyService:
    """Manages idempotent task creation scoped strictly to workspace tenants."""

    @staticmethod
    async def get_or_create_task(
        session: AsyncSession,
        workspace_id: UUID,
        user_id: UUID,
        idempotency_key: str,
        user_instruction: str,
        title: str | None = None,
        budget_usd: Decimal | None = None,
        session_id: str | None = None,
    ) -> tuple[Task, bool]:
        """Claim or retrieve a task using its workspace-scoped idempotency key.

        Returns:
            tuple[Task, bool]: (task, is_newly_created)
        """
        # 1. Fast path: check if existing task exists
        stmt = (
            select(Task)
            .where(
                Task.workspace_id == workspace_id,
                Task.idempotency_key == idempotency_key,
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        existing = result.scalars().first()
        if existing is not None:
            return existing, False

        # 2. Not found; attempt insertion
        task_id = uuid4()
        effective_session_id = session_id or str(task_id)
        effective_title = title or (
            user_instruction[:50] + "..." if len(user_instruction) > 50 else user_instruction
        )
        effective_budget = budget_usd if budget_usd is not None else Decimal("1.50")

        now = datetime.now(UTC)
        new_task = Task(
            id=task_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=effective_session_id,
            user_instruction=user_instruction,
            instruction=user_instruction,
            title=effective_title,
            idempotency_key=idempotency_key,
            plan_status=PlanStatus.PLANNING,
            status="pending",
            budget_usd=effective_budget,
            spent_usd=Decimal("0.0"),
            tool_turns=0,
            active_plan=[],
            shared_context={},
            execution_trace=[],
            created_at=now,
            updated_at=now,
        )
        session.add(new_task)

        try:
            await session.flush()
            return new_task, True
        except IntegrityError:
            # Concurrent submission inserted the same key between check and insert
            await session.rollback()
            retry_stmt = (
                select(Task)
                .where(
                    Task.workspace_id == workspace_id,
                    Task.idempotency_key == idempotency_key,
                )
                .limit(1)
            )
            retry_result = await session.execute(retry_stmt)
            concurrent_task = retry_result.scalars().first()
            if concurrent_task is not None:
                return concurrent_task, False
            raise
