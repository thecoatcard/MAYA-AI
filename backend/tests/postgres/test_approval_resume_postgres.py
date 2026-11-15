"""A Notion write approved over the real HTTP route resumes the paused task and completes it.

Real PostgreSQL as ``maya_app`` (FORCE RLS), the real approvals route, the real queue and the
real worker. Only the tool server is a recorder, so the test can count the writes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import PlanStatus, Subtask, SubtaskAgent, SubtaskStatus, Task
from app.db.tenant import tenant_session
from app.mcp.permissions import ToolPermissionRegistry
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.runtime import TaskRuntime
from app.tasks.worker import SubtaskWorker, resolve_subtask_uuid
from tests.support.postgres import SeededTenant
from tests.support.settings import JWT_KEY, make_settings

pytestmark = pytest.mark.postgres

WRITE_ARGS: dict[str, Any] = {
    "requests": [
        {
            "method": "POST",
            "path": "/pages",
            "body": {
                "parent": {"page_id": "40e5895561ca4a9f8a0c0000000000aa"},
                "properties": {
                    "title": {"title": [{"type": "text", "text": {"content": "Notes"}}]}
                },
            },
        }
    ]
}


class _Publisher:
    async def publish(self, event: Any) -> bool:
        return True


class _Tools:
    """The MCP gateway as the worker uses it: records every invocation."""

    def __init__(self) -> None:
        self.calls: list[bool] = []

    def has_tool(self, name: str) -> bool:
        return True

    async def invoke(self, name: str, args: dict[str, Any], **kwargs: Any) -> Any:
        self.calls.append(bool(kwargs.get("has_valid_approval_token")))
        return SimpleNamespace(
            clean_text="Created page https://notion.so/Notes. VERIFIED by reading it back.",
            has_injection_suspicions=False,
        )


async def _no_agent(job: SubtaskJob, input_data: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError("a tool step must not reach the agent handler")


async def _approve_over_http(
    app_engine: AsyncEngine, tenant: SeededTenant, queue: SubtaskQueue, approval_id: Any
) -> int:
    """POST the approve decision to the real route, with the real queue behind it."""
    from fastapi.testclient import TestClient

    from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
    from app.auth.models import Principal
    from app.main import create_app

    principal = Principal(
        user_id=tenant.user_id, workspace_id=tenant.workspace_id, role="member", session_id=uuid4()
    )
    app = create_app(make_settings())
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_tenant_database] = lambda: TenantDatabase(
        engine=app_engine, workspace_id=tenant.workspace_id
    )

    def approve() -> int:
        with patch("app.main.Resources") as factory:
            resources = factory.return_value
            resources.check = AsyncMock(return_value=None)
            resources.close = AsyncMock()
            resources.verify_runtime_role = AsyncMock(return_value=None)
            resources.subtask_queue = queue
            with TestClient(app) as client:
                reply = client.post(
                    f"/api/v1/approvals/{approval_id}/decide",
                    json={"action": "approve"},
                    headers={"Authorization": "Bearer test-token"},
                )
                return reply.status_code

    return await asyncio.to_thread(approve)


@pytest.mark.asyncio
async def test_approving_over_http_resumes_and_completes_the_notion_write(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        task, _ = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            idempotency_key=f"resume-{uuid4().hex}",
            user_instruction="Notion: create the page 'Notes'",
        )
        task.status = "executing"
        task.plan_status = PlanStatus.EXECUTING
        subtask = Subtask(
            id=resolve_subtask_uuid(task.id, "step_0"),
            task_id=task.id,
            workspace_id=tenant.workspace_id,
            assigned_agent=SubtaskAgent.PM,
            description="Notion: create the page 'Notes'",
            dependencies=[],
            status=SubtaskStatus.PENDING,
            input_data={
                "tool": "notion_create_page",
                "tool_args": WRITE_ARGS,
                "requires_approval": True,
            },
        )
        session.add(subtask)
        await session.flush()
    task_id, subtask_id = task.id, subtask.id

    queue = SubtaskQueue()
    tools = _Tools()
    runtime = TaskRuntime(
        engine=app_engine,
        queue=queue,
        publisher=_Publisher(),  # type: ignore[arg-type]
        worker=SubtaskWorker(
            queue=queue,
            permission_registry=ToolPermissionRegistry(),
            jwt_signing_key=JWT_KEY,
            tool_invoker=tools,
        ),
        agent_handler=_no_agent,
    )
    job = SubtaskJob(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_id=task_id,
        subtask_id=subtask_id,
        assigned_agent="pm",
    )

    # 1. the worker pauses the step and records the approval request; nothing is written
    await runtime.process_job(job)
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        row = (
            await session.execute(
                text("SELECT id, status FROM approval_requests WHERE task_id = :t"), {"t": task_id}
            )
        ).one()
        assert row.status == "pending"
        assert (await session.get(Task, task_id)).status == "awaiting_approval"  # type: ignore[union-attr]
    assert tools.calls == []

    # 2. the user approves through the real route (the same one the inline card calls)
    assert await _approve_over_http(app_engine, tenant, queue, row.id) == 200

    # 3. the approval queued the step again, and the worker runs it
    assert queue.qsize() == 1, "approving must queue the paused step again"
    resumed = await queue.dequeue(timeout_seconds=1.0)
    assert resumed is not None
    await runtime.process_job(resumed)

    # 4. exactly one approved write; the step and the task are completed
    assert tools.calls == [True]
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        done_task = await session.get(Task, task_id)
        done_step = (
            await session.execute(select(Subtask).where(Subtask.id == subtask_id))
        ).scalar_one()
        status = (
            await session.execute(
                text("SELECT status FROM approval_requests WHERE id = :i"), {"i": row.id}
            )
        ).scalar_one()
    assert done_step.status == SubtaskStatus.COMPLETED
    assert "VERIFIED by reading it back" in str(done_step.output_result)
    assert done_task is not None and done_task.status == "completed"
    assert status in ("executing", "completed")


@pytest.mark.asyncio
async def test_an_approval_whose_token_ran_out_before_the_step_ran_asks_again(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    """Regression: "Approved" in the list but the card waits forever.

    The approval token lives 5 minutes. If the step only runs later (a restart, a backlog) the
    token is dead, the request stays "approved" and nothing ever executed or asked again. Now the
    stale request is closed as expired and a fresh pending one appears; nothing is written on the
    stale one.
    """
    from datetime import UTC, datetime, timedelta

    from app.mcp.tokens import mint_approval_token

    tenant, _ = fresh_tenants
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        task, _ = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            idempotency_key=f"stale-{uuid4().hex}",
            user_instruction="Notion: create the page 'Notes'",
        )
        task.status = "executing"
        task.plan_status = PlanStatus.EXECUTING
        subtask = Subtask(
            id=resolve_subtask_uuid(task.id, "step_0"),
            task_id=task.id,
            workspace_id=tenant.workspace_id,
            assigned_agent=SubtaskAgent.PM,
            description="Notion: create the page 'Notes'",
            dependencies=[],
            status=SubtaskStatus.PENDING,
            input_data={
                "tool": "notion_create_page",
                "tool_args": WRITE_ARGS,
                "requires_approval": True,
            },
        )
        session.add(subtask)
        await session.flush()
    task_id, subtask_id = task.id, subtask.id
    queue = SubtaskQueue()
    tools = _Tools()
    runtime = TaskRuntime(
        engine=app_engine,
        queue=queue,
        publisher=_Publisher(),  # type: ignore[arg-type]
        worker=SubtaskWorker(
            queue=queue,
            permission_registry=ToolPermissionRegistry(),
            jwt_signing_key=JWT_KEY,
            tool_invoker=tools,
        ),
        agent_handler=_no_agent,
    )
    job = SubtaskJob(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_id=task_id,
        subtask_id=subtask_id,
        assigned_agent="pm",
    )
    await runtime.process_job(job)

    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, payload_hash, tool_name FROM approval_requests WHERE task_id = :t"
                ),
                {"t": task_id},
            )
        ).one()
        old_token, jti = mint_approval_token(
            JWT_KEY,
            user_id=tenant.user_id,
            workspace_id=tenant.workspace_id,
            request_id=row.id,
            tool_name=row.tool_name,
            payload_hash=row.payload_hash,
            ttl_seconds=300,
            now=datetime.now(UTC) - timedelta(minutes=20),
        )
        # what the approve route committed, 20 minutes ago
        await session.execute(
            text(
                "UPDATE approval_requests SET status = 'approved', approval_token = :tok, "
                "token_jti = :jti, decided_at = now() WHERE id = :i"
            ),
            {"tok": old_token, "jti": jti, "i": row.id},
        )
        await session.execute(
            text(
                "UPDATE subtasks SET status = 'pending', input_data = "
                "jsonb_set(input_data, '{approval_token}', to_jsonb(CAST(:tok AS text))) "
                "WHERE id = :s"
            ),
            {"tok": old_token, "s": subtask_id},
        )

    await runtime.process_job(job)  # the late run

    assert tools.calls == [], "nothing may execute on an expired token"
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        rows = (
            await session.execute(
                text("SELECT id, status FROM approval_requests WHERE task_id = :t"), {"t": task_id}
            )
        ).all()
        waiting_task = await session.get(Task, task_id)
    by_status = sorted(r.status for r in rows)
    assert by_status == ["expired", "pending"], by_status
    assert waiting_task is not None and waiting_task.status == "awaiting_approval"

    # the user approves the fresh request on the card: exactly one write, task completed
    fresh = next(r.id for r in rows if r.status == "pending")
    assert await _approve_over_http(app_engine, tenant, queue, fresh) == 200
    resumed = await queue.dequeue(timeout_seconds=1.0)
    assert resumed is not None
    await runtime.process_job(resumed)
    assert tools.calls == [True]
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        final = await session.get(Task, task_id)
    assert final is not None and final.status == "completed"
