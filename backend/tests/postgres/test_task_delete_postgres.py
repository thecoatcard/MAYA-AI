"""Deleting a finished task from history, against REAL PostgreSQL (RLS, FKs, audit, outbox)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.db.models import (
    ApprovalRequest,
    AuditLog,
    FileArtifact,
    PlanStatus,
    Subtask,
    SubtaskAgent,
    Task,
    TaskOutboxEvent,
)
from app.db.tenant import tenant_session
from app.main import create_app
from app.services.task_deletion import delete_session_content, delete_tasks
from app.tasks.events import TaskEventType
from app.tasks.outbox import write_outbox_event
from tests.postgres.test_agent_fleet_postgres import _seed
from tests.support.postgres import SeededTenant
from tests.support.settings import make_settings

pytestmark = pytest.mark.postgres


async def _finished_task(
    engine: AsyncEngine, tenant: SeededTenant, root: Path, state: str = "completed"
) -> tuple[UUID, Path, Path]:
    """A task with a step, an outbox event, a pending approval and a stored file."""
    task, sub = await _seed(engine, tenant, SubtaskAgent.DOCUMENT, "Write the Q3 report")
    stored = root / str(tenant.workspace_id) / str(task.id) / "report.pdf"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"%PDF")
    outside = root.parent / f"user-folder-{uuid4().hex}.pdf"  # the user's own working-folder copy
    outside.write_bytes(b"%PDF")
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        row = await session.get(Task, task.id)
        assert row is not None
        row.status = state
        row.plan_status = PlanStatus.COMPLETED
        session.add(
            FileArtifact(
                workspace_id=tenant.workspace_id,
                task_id=task.id,
                user_id=tenant.user_id,
                storage_uri=str(stored),
                file_name="report.pdf",
                file_type="pdf",
                size_bytes=4,
                sha256="a" * 64,
            )
        )
        session.add(
            ApprovalRequest(
                workspace_id=tenant.workspace_id,
                user_id=tenant.user_id,
                task_id=task.id,
                subtask_id=sub.id,
                tool_name="send_email",
                tool_args={},
                payload_diff={},
                payload_hash="b" * 64,
                permission_level="APPROVAL_REQUIRED",
                action_description="Send the report",
                status="pending",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await write_outbox_event(
            session=session,
            workspace_id=tenant.workspace_id,
            task_id=task.id,
            event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
            payload={"ok": True},
        )
    return task.id, stored, outside


async def _count(engine: AsyncEngine, tenant: SeededTenant, table: str, task_id: UUID) -> int:
    columns = {
        "subtasks": Subtask.task_id,
        "files": FileArtifact.task_id,
        "outbox": TaskOutboxEvent.task_id,
    }
    column = columns[table]
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        result = await session.execute(
            select(func.count()).select_from(column.class_).where(column == task_id)
        )
        return int(result.scalar_one())


@pytest.mark.asyncio
async def test_finished_task_is_removed_and_audit_and_outbox_are_kept(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    tenant, _ = fresh_tenants
    root = tmp_path / "artifacts"
    task_id, stored, copy = await _finished_task(app_engine, tenant, root)

    outcome = await delete_tasks(
        app_engine,
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_ids=[task_id],
        storage_root=root,
    )

    assert outcome.deleted == [task_id] and outcome.files_removed == 1
    assert not stored.exists()
    assert copy.exists(), "a copy outside MAYA's storage is never deleted"
    assert await _count(app_engine, tenant, "subtasks", task_id) == 0
    assert await _count(app_engine, tenant, "files", task_id) == 0
    assert await _count(app_engine, tenant, "outbox", task_id) >= 1
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        audit = (
            (await session.execute(select(AuditLog).where(AuditLog.task_id == task_id)))
            .scalars()
            .all()
        )
        assert any(a.tool_name == "task.deleted" for a in audit)
        approvals = (
            (
                await session.execute(
                    select(ApprovalRequest.status).where(ApprovalRequest.task_id == task_id)
                )
            )
            .scalars()
            .all()
        )
        assert "pending" not in approvals
        task = await session.get(Task, task_id)
        assert task is not None and task.user_instruction == ""
        assert "deleted_at" in task.shared_context

    again = await delete_tasks(
        app_engine,
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_ids=[task_id],
        storage_root=root,
    )
    assert again.not_found == [task_id] and again.deleted == []


@pytest.mark.asyncio
async def test_running_task_is_refused_and_untouched(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    tenant, _ = fresh_tenants
    task, _sub = await _seed(app_engine, tenant, SubtaskAgent.DOCUMENT, "Still running")
    outcome = await delete_tasks(
        app_engine,
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_ids=[task.id],
        storage_root=tmp_path,
    )
    assert outcome.not_finished == [task.id] and outcome.deleted == []
    assert await _count(app_engine, tenant, "subtasks", task.id) == 1


@pytest.mark.asyncio
async def test_another_user_cannot_delete_a_task(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    a, b = fresh_tenants
    root = tmp_path / "artifacts"
    task_id, stored, _copy = await _finished_task(app_engine, a, root)
    outcome = await delete_tasks(
        app_engine,
        workspace_id=b.workspace_id,
        user_id=b.user_id,
        task_ids=[task_id],
        storage_root=root,
    )
    assert outcome.not_found == [task_id] and outcome.deleted == []
    assert stored.exists()
    same_ws = await delete_tasks(
        app_engine,
        workspace_id=a.workspace_id,
        user_id=a.other_user_id,
        task_ids=[task_id],
        storage_root=root,
    )
    assert same_ws.deleted == []


@pytest.mark.asyncio
async def test_session_delete_option_removes_its_finished_tasks_and_chat_files(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    tenant, _ = fresh_tenants
    root = tmp_path / "artifacts"
    task_id, _stored, _copy = await _finished_task(app_engine, tenant, root)
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        row = await session.get(Task, task_id)
        assert row is not None
        session_id = row.session_id
    chat_file = root / str(tenant.workspace_id) / "chat" / str(session_id) / "notes.md"
    chat_file.parent.mkdir(parents=True)
    chat_file.write_text("x")

    outcome = await delete_session_content(
        app_engine,
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        session_id=session_id,
        storage_root=root,
    )
    assert outcome.deleted == [task_id]
    assert not chat_file.exists()


def test_http_codes_for_delete_and_bulk_cap(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    principal = Principal(
        user_id=tenant.user_id, workspace_id=tenant.workspace_id, role="member", session_id=uuid4()
    )
    app = create_app(make_settings())
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_tenant_database] = lambda: TenantDatabase(
        engine=app_engine, workspace_id=tenant.workspace_id
    )
    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(return_value=None)
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        resources.redis = None
        resources.model_gateway = None
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer test-token"}
            assert client.delete(f"/api/v1/tasks/{uuid4()}", headers=headers).status_code == 404
            over = client.post(
                "/api/v1/tasks/delete",
                json={"ids": [str(uuid4()) for _ in range(101)]},
                headers=headers,
            )
            assert over.status_code == 422
            ok = client.post("/api/v1/tasks/delete", json={"ids": [str(uuid4())]}, headers=headers)
            assert ok.status_code == 200 and len(ok.json()["not_found"]) == 1
