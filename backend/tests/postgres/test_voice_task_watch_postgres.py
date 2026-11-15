"""Voice task watch against REAL PostgreSQL: the resolver and the voice-off catch-up query."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import FileArtifact, PlanStatus, SubtaskAgent
from app.db.tenant import tenant_session
from app.voice.task_watch import AnnouncedStore, Milestone, find_catch_up, make_db_resolver
from tests.postgres.test_agent_fleet_postgres import _seed
from tests.support.postgres import SeededTenant

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_resolver_returns_own_task_with_file_and_hides_other_users_task(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    task, _sub = await _seed(app_engine, tenant, SubtaskAgent.DOCUMENT, "Solar deck")
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        row = await session.get(type(task), task.id)
        assert row is not None
        row.status = "completed"
        session.add(
            FileArtifact(
                workspace_id=tenant.workspace_id,
                task_id=task.id,
                user_id=tenant.user_id,
                file_name="solar.pptx",
                file_type="pptx",
                storage_uri="/tmp/solar.pptx",
                size_bytes=10,
                sha256="a" * 64,
                verification_status="verified",
            )
        )
        await session.flush()  # tenant_session does not flush on exit
    resolve = make_db_resolver(app_engine)
    facts = await resolve(tenant.workspace_id, tenant.user_id, str(task.id))
    assert facts is not None and facts.files == ("solar.pptx",)
    assert await resolve(tenant.workspace_id, tenant.other_user_id, str(task.id)) is None


@pytest.mark.asyncio
async def test_catch_up_lists_missed_milestones_once_and_moves_the_mark(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    task, _sub = await _seed(app_engine, tenant, SubtaskAgent.DOCUMENT, "Budget report")
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        row = await session.get(type(task), task.id)
        assert row is not None
        row.status = "awaiting_approval"
        row.plan_status = PlanStatus.PAUSED
        await session.flush()  # tenant_session does not flush on exit
    store = AnnouncedStore()
    # First ever voice session: no mark yet, nothing to catch up, the mark is set.
    first = await find_catch_up(
        app_engine,
        store,
        tenant.workspace_id,
        tenant.user_id,
        now=datetime.now(UTC) - timedelta(hours=1),
    )
    assert first == []
    second = await find_catch_up(app_engine, store, tenant.workspace_id, tenant.user_id)
    assert [m for m, _f, _k in second] == [Milestone.APPROVAL]
    third = await find_catch_up(app_engine, store, tenant.workspace_id, tenant.user_id)
    assert third == []  # announced once, cleared
