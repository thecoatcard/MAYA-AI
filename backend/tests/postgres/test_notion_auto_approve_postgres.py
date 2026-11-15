"""Notion auto-approve against real PostgreSQL (FORCE RLS), the real worker and queue.

The approval is recorded by the same repository and token as a click; the worker still verifies the
token against the payload hash, claims it and runs the tool. Only the tool server is a recorder.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import PlanStatus, Subtask, SubtaskAgent, SubtaskStatus, Task
from app.db.repositories.user_settings import UserSettingsStore
from app.db.tenant import tenant_session
from app.mcp import notion_auto_approve as auto
from app.mcp.permissions import ToolPermissionRegistry
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.runtime import TaskRuntime
from app.tasks.worker import SubtaskWorker, resolve_subtask_uuid
from tests.postgres.test_approval_resume_postgres import WRITE_ARGS
from tests.support.postgres import SeededTenant
from tests.support.settings import JWT_KEY

pytestmark = pytest.mark.postgres


class _Publisher:
    async def publish(self, event: Any) -> bool:
        return True


class _Tools:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def has_tool(self, name: str) -> bool:
        return True

    async def invoke(self, name: str, args: dict[str, Any], **kwargs: Any) -> Any:
        self.calls.append(bool(kwargs.get("has_valid_approval_token")))
        return SimpleNamespace(
            clean_text="Created page. VERIFIED by reading it back.",
            has_injection_suspicions=False,
        )


async def _no_agent(job: SubtaskJob, input_data: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError("a tool step must not reach the agent handler")


async def _seed(
    engine: AsyncEngine, tenant: SeededTenant, tool: str = "notion_create_page"
) -> tuple[Any, Any]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task, _ = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            idempotency_key=f"auto-{uuid4().hex}",
            user_instruction="Notion write",
        )
        task.status = "executing"
        task.plan_status = PlanStatus.EXECUTING
        subtask = Subtask(
            id=resolve_subtask_uuid(task.id, "step_0"),
            task_id=task.id,
            workspace_id=tenant.workspace_id,
            assigned_agent=SubtaskAgent.PM,
            description="Notion write",
            dependencies=[],
            status=SubtaskStatus.PENDING,
            input_data={"tool": tool, "tool_args": WRITE_ARGS, "requires_approval": True},
        )
        session.add(subtask)
        await session.flush()
    return task.id, subtask.id


def _runtime(engine: AsyncEngine, queue: SubtaskQueue, tools: _Tools) -> TaskRuntime:
    return TaskRuntime(
        engine=engine,
        queue=queue,
        publisher=_Publisher(),  # type: ignore[arg-type]
        worker=SubtaskWorker(
            queue=queue,
            permission_registry=ToolPermissionRegistry(),
            jwt_signing_key=JWT_KEY,
            tool_invoker=tools,
            auto_approver=auto.NotionAutoApprover(UserSettingsStore(engine), JWT_KEY),
        ),
        agent_handler=_no_agent,
    )


async def _enable(
    engine: AsyncEngine, tenant: SeededTenant, scopes: tuple[str, ...] = auto.SCOPES
) -> None:
    setting = auto.NotionAutoApproveSetting(True, frozenset(scopes), 4)
    await auto.save_setting(UserSettingsStore(engine), tenant.workspace_id, tenant.user_id, setting)


def _job(tenant: SeededTenant, task_id: Any, subtask_id: Any) -> SubtaskJob:
    return SubtaskJob(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_id=task_id,
        subtask_id=subtask_id,
        assigned_agent="pm",
    )


async def _approval(engine: AsyncEngine, tenant: SeededTenant, task_id: Any) -> Any:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        return (
            await session.execute(
                text(
                    "SELECT id, status, decision_channel, payload_hash, action_description "
                    "FROM approval_requests WHERE task_id = :t"
                ),
                {"t": task_id},
            )
        ).one()


async def test_default_off_keeps_the_human_gate(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    task_id, subtask_id = await _seed(app_engine, tenant)
    queue, tools = SubtaskQueue(), _Tools()
    await _runtime(app_engine, queue, tools).process_job(_job(tenant, task_id, subtask_id))
    row = await _approval(app_engine, tenant, task_id)
    assert row.status == "pending" and row.decision_channel is None
    assert tools.calls == [] and queue.qsize() == 0


async def test_enabled_scope_auto_approves_through_the_same_gate_and_runs_once(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    await _enable(app_engine, tenant, ("create_page",))
    task_id, subtask_id = await _seed(app_engine, tenant)
    queue, tools = SubtaskQueue(), _Tools()
    runtime = _runtime(app_engine, queue, tools)
    await runtime.process_job(_job(tenant, task_id, subtask_id))
    row = await _approval(app_engine, tenant, task_id)
    assert row.status == "approved" and row.decision_channel == "api"
    assert tools.calls == [], "approval is recorded first; the write is a separate, verified run"
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        audit = (
            await session.execute(
                text(
                    "SELECT user_ref, approval_id, status, input_redacted FROM audit_log "
                    "WHERE tool_name = 'approval.auto_approved' AND task_id = :t"
                ),
                {"t": task_id},
            )
        ).one()
    assert audit.user_ref == str(tenant.user_id) and audit.approval_id == row.id
    assert audit.input_redacted["payload_hash"] == row.payload_hash
    assert audit.input_redacted["setting_version"] == 4
    assert audit.input_redacted["decision_channel"] == "auto_setting"
    resumed = await queue.dequeue(timeout_seconds=1.0)
    assert resumed is not None
    await runtime.process_job(resumed)
    assert tools.calls == [True]
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        step = (await session.execute(select(Subtask).where(Subtask.id == subtask_id))).scalar_one()
        done = await session.get(Task, task_id)
        usage = await auto.usage_in_session(session, tenant.workspace_id, tenant.user_id)
    assert step.status == SubtaskStatus.COMPLETED
    assert "VERIFIED by reading it back" in str(step.output_result)
    assert done is not None and done.status == "completed"
    assert usage.last_hour == 1 and usage.last_tool == "notion_create_page"


async def test_tampering_after_auto_approval_fails_the_gate(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    await _enable(app_engine, tenant, ("create_page",))
    task_id, subtask_id = await _seed(app_engine, tenant)
    queue, tools = SubtaskQueue(), _Tools()
    runtime = _runtime(app_engine, queue, tools)
    await runtime.process_job(_job(tenant, task_id, subtask_id))
    changed = {"requests": [{**WRITE_ARGS["requests"][0], "path": "/pages"}], "extra": "x"}
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        step = (await session.execute(select(Subtask).where(Subtask.id == subtask_id))).scalar_one()
        step.input_data = {**step.input_data, "tool_args": changed}
        await session.commit()
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        stored = (
            await session.execute(select(Subtask).where(Subtask.id == subtask_id))
        ).scalar_one()
        assert stored.input_data["tool_args"] == changed, "the tampering must really be stored"
    resumed = await queue.dequeue(timeout_seconds=1.0)
    assert resumed is not None
    await runtime.process_job(resumed)
    assert tools.calls == [], "a changed payload must not run on the old approval"


async def test_scope_off_destructive_and_cap_stay_manual(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    await _enable(app_engine, tenant, ("add_row",))  # create_page is not switched on
    task_id, subtask_id = await _seed(app_engine, tenant)
    queue, tools = SubtaskQueue(), _Tools()
    await _runtime(app_engine, queue, tools).process_job(_job(tenant, task_id, subtask_id))
    assert (await _approval(app_engine, tenant, task_id)).status == "pending"

    await _enable(app_engine, tenant)  # every scope on, destructive tool still manual
    task2, sub2 = await _seed(app_engine, tenant, tool="notion_archive_page")
    await _runtime(app_engine, queue, tools).process_job(_job(tenant, task2, sub2))
    assert (await _approval(app_engine, tenant, task2)).status == "pending"
    assert tools.calls == []


async def test_cap_reached_falls_back_to_manual_with_a_note(
    app_engine: AsyncEngine,
    admin_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, _ = fresh_tenants
    await _enable(app_engine, tenant)
    monkeypatch.setattr(auto, "HOURLY_CAP", 1)
    queue, tools = SubtaskQueue(), _Tools()
    runtime = _runtime(app_engine, queue, tools)
    first_task, first_sub = await _seed(app_engine, tenant)
    await runtime.process_job(_job(tenant, first_task, first_sub))
    assert (await _approval(app_engine, tenant, first_task)).status == "approved"
    task2, sub2 = await _seed(app_engine, tenant)
    await runtime.process_job(_job(tenant, task2, sub2))
    row = await _approval(app_engine, tenant, task2)
    assert row.status == "pending" and "approve this one yourself" in row.action_description


async def test_one_user_setting_is_not_visible_to_another(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = fresh_tenants
    store = UserSettingsStore(app_engine)
    await _enable(app_engine, a)
    assert (await auto.load_setting(store, a.workspace_id, a.user_id)).enabled is True
    assert (await auto.load_setting(store, b.workspace_id, b.user_id)).enabled is False
    # Another workspace's rows are invisible under row-level security, whoever asks.
    assert (await auto.load_setting(store, b.workspace_id, a.user_id)).enabled is False
