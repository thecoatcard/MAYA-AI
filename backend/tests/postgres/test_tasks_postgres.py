"""Tests for Task Lifecycle and Idempotency against real PostgreSQL service."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.db.models import ChatSession
from app.db.tenant import tenant_session
from app.main import create_app
from app.tasks.events import TaskEventPublisher
from app.tasks.idempotency import TaskIdempotencyService
from tests.support.postgres import SeededTenant
from tests.support.settings import make_settings

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_task_idempotency_service_auto_creates_session_in_postgres(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
) -> None:
    """TaskIdempotencyService must auto-create a parent session when none is passed,

    avoiding foreign key constraint violations against PostgreSQL sessions table.
    """
    tenant, _ = fresh_tenants
    key = f"key-auto-sess-{uuid4().hex[:8]}"

    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        task, is_new = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            idempotency_key=key,
            user_instruction="Analyze quarterly operating margin",
            title="Margin Analysis",
            budget_usd=Decimal("2.50"),
            session_id=None,
        )
        assert is_new is True
        assert task.session_id is not None
        assert task.workspace_id == tenant.workspace_id

        # Verify parent session was created in database
        sess_stmt = select(ChatSession).where(
            ChatSession.workspace_id == tenant.workspace_id,
            ChatSession.id == task.session_id,
        )
        sess_res = await session.execute(sess_stmt)
        parent_session = sess_res.scalars().first()
        assert parent_session is not None
        assert parent_session.title == "Margin Analysis"

        # Verify idempotency deduplication on second call
        task2, is_new2 = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            idempotency_key=key,
            user_instruction="Analyze quarterly operating margin",
        )
        assert is_new2 is False
        assert task2.id == task.id
        assert task2.session_id == task.session_id


@pytest.mark.asyncio
async def test_task_idempotency_service_links_existing_session_in_postgres(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
) -> None:
    """When a valid session_id is provided, TaskIdempotencyService links to it."""
    tenant, _ = fresh_tenants
    key = f"key-linked-sess-{uuid4().hex[:8]}"

    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        task, is_new = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            idempotency_key=key,
            user_instruction="Research competitor pricing",
            session_id=str(tenant.session_id),
        )
        assert is_new is True
        assert task.session_id == tenant.session_id


def test_post_api_v1_tasks_against_real_postgres(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
) -> None:
    """POST /api/v1/tasks succeeds with 201 Created against real PostgreSQL."""
    tenant, _ = fresh_tenants
    principal = Principal(
        user_id=tenant.user_id,
        workspace_id=tenant.workspace_id,
        role="member",
        session_id=uuid4(),
    )

    app = create_app(make_settings())
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_tenant_database] = lambda: TenantDatabase(
        engine=app_engine,
        workspace_id=tenant.workspace_id,
    )
    app.dependency_overrides[TaskEventPublisher] = lambda: TaskEventPublisher(
        redis_client=MagicMock()
    )

    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(return_value=None)
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        resources.redis = None
        resources.model_gateway = None

        with TestClient(app) as client:
            # Submit new task without session_id (like NewTaskModal does)
            resp = client.post(
                "/api/v1/tasks",
                json={
                    "instruction": "Benchmark PostgreSQL connection pooling and latency",
                    "title": "Latency Benchmark",
                    "idempotency_key": f"task-pg-bench-{uuid4().hex[:8]}",
                    "budget_usd": 1.5,
                },
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()
            assert data["is_new"] is True
            assert data["status"] == "executing"
            task_id = data["task_id"]

            # Fetch task details via GET /api/v1/tasks/{id}
            resp_detail = client.get(
                f"/api/v1/tasks/{task_id}",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp_detail.status_code == 200, resp_detail.text
            detail = resp_detail.json()
            assert detail["id"] == task_id
            assert detail["title"] == "Latency Benchmark"
            assert len(detail["subtasks"]) >= 1
