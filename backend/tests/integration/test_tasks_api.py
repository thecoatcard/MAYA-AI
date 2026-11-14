"""Integration tests for the Task Lifecycle REST API endpoints (Phase 06)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.db.models import Task
from app.main import create_app
from app.tasks.events import TaskEventPublisher
from tests.support.settings import make_settings


@pytest.fixture
def mock_db_tenant() -> tuple[Principal, dict[UUID, Task], Any]:
    workspace_id = uuid4()
    user_id = uuid4()
    principal = Principal(
        user_id=user_id,
        workspace_id=workspace_id,
        role="member",
        session_id=uuid4(),
    )

    tasks_store: dict[UUID, Task] = {}
    comps_store: dict[UUID, Any] = {}

    @asynccontextmanager
    async def fake_tenant_session(
        engine: Any, ws_id: Any, u_id: Any = None
    ) -> AsyncIterator[AsyncMock]:
        session = AsyncMock()

        async def fake_execute(stmt: Any) -> MagicMock:
            mock_res = MagicMock()
            stmt_str = str(stmt)
            if "FROM compensations" in stmt_str:
                mock_res.scalars.return_value.all.return_value = list(comps_store.values())
                mock_res.scalars.return_value.first.return_value = (
                    list(comps_store.values())[0] if comps_store else None
                )
            elif "FROM tasks" in stmt_str and "WHERE" in stmt_str:
                matched = list(tasks_store.values())
                mock_res.scalars.return_value.first.return_value = matched[0] if matched else None
                mock_res.scalars.return_value.all.return_value = matched
                mock_res.scalar_one.return_value = len(matched)
            elif "count" in stmt_str.lower():
                mock_res.scalar_one.return_value = len(tasks_store)
            else:
                mock_res.scalars.return_value.all.return_value = list(tasks_store.values())
                mock_res.scalars.return_value.first.return_value = (
                    list(tasks_store.values())[0] if tasks_store else None
                )
            return mock_res

        session.execute = AsyncMock(side_effect=fake_execute)

        def fake_add(obj: Any) -> None:
            if isinstance(obj, Task):
                tasks_store[obj.id] = obj

        session.add = MagicMock(side_effect=fake_add)
        session.flush = AsyncMock()
        yield session

    return principal, tasks_store, fake_tenant_session


def test_task_submission_and_idempotency_lifecycle(mock_db_tenant: Any) -> None:
    principal, tasks_store, fake_tenant_session = mock_db_tenant

    with (
        patch("app.main.Resources") as factory,
        patch("app.api.v1.tasks.tenant_session", side_effect=fake_tenant_session),
        # submission moved into the shared service; it opens its own tenant session
        patch("app.services.task_submission.tenant_session", side_effect=fake_tenant_session),
    ):
        resources = factory.return_value
        resources.check = AsyncMock(return_value=None)
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        resources.redis = None
        resources.model_gateway = None

        app = create_app(make_settings())
        app.dependency_overrides[get_principal] = lambda: principal
        app.dependency_overrides[get_tenant_database] = lambda: TenantDatabase(
            engine=MagicMock(), workspace_id=principal.workspace_id
        )
        app.dependency_overrides[TaskEventPublisher] = lambda: TaskEventPublisher(
            redis_client=MagicMock()
        )

        with TestClient(app) as client:
            # 1. Reject submission without idempotency key
            resp_no_key = client.post(
                "/api/v1/tasks",
                json={"instruction": "Perform market research on competitors"},
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp_no_key.status_code == 400
            body = resp_no_key.json()
            assert body["error"] == "http_error"
            assert "idempotency key" in body["message"].lower()

            # 2. First submission with idempotency key -> 201 Created
            resp_created = client.post(
                "/api/v1/tasks",
                json={
                    "instruction": "Perform market research on competitors",
                    "title": "Market Research",
                    "idempotency_key": "task-market-001",
                    "budget_usd": 2.0,
                },
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp_created.status_code == 201
            body = resp_created.json()
            assert body["is_new"] is True
            assert body["idempotency_key"] == "task-market-001"
            task_id = body["task_id"]

            # 3. Second submission with identical idempotency key -> 200 OK
            resp_duplicate = client.post(
                "/api/v1/tasks",
                json={
                    "instruction": "Perform market research on competitors",
                    "idempotency_key": "task-market-001",
                },
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp_duplicate.status_code == 200
            dup_body = resp_duplicate.json()
            assert dup_body["is_new"] is False
            assert dup_body["task_id"] == task_id

            # 4. GET /api/v1/tasks (list)
            resp_list = client.get(
                "/api/v1/tasks",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp_list.status_code == 200
            list_data = resp_list.json()
            assert list_data["total"] >= 1
            assert any(item["id"] == task_id for item in list_data["items"])

            # 5. GET /api/v1/tasks/{id} (details)
            resp_detail = client.get(
                f"/api/v1/tasks/{task_id}",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp_detail.status_code == 200
            detail = resp_detail.json()
            assert detail["id"] == task_id
            assert detail["instruction"] == "Perform market research on competitors"

            # 6. POST /api/v1/tasks/{id}/abort
            resp_abort = client.post(
                f"/api/v1/tasks/{task_id}/abort",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp_abort.status_code == 200
            abort_data = resp_abort.json()
            assert abort_data["task_id"] == task_id
            assert abort_data["status"] == "cancelled"

            # 7. GET /api/v1/tasks/{id}/events (SSE stream)
            resp_events = client.get(
                f"/api/v1/tasks/{task_id}/events",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp_events.status_code == 200
            assert "text/event-stream" in resp_events.headers["content-type"]
            assert "connected" in resp_events.text
