"""Voice persistence against the real test PostgreSQL (row-level security, shared tables)."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.voice.persistence import SessionNotOwned, VoicePersistenceError, VoicePersistenceService
from tests.support.postgres import SeededTenant


@pytest.mark.asyncio
async def test_turns_are_saved_per_turn_in_the_shared_tables_and_seed_in_order(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, other = fresh_tenants
    svc = VoicePersistenceService(app_engine)
    info = await svc.create_session(tenant.workspace_id, tenant.user_id)
    assert info.created is True

    first = await svc.save_turn(
        tenant.workspace_id,
        tenant.user_id,
        info.id,
        role="user",
        content="hello",
        modality="audio",
        metadata={"turn_id": "t1"},
    )
    second = await svc.save_turn(
        tenant.workspace_id,
        tenant.user_id,
        info.id,
        role="assistant",
        content="hi there",
        modality="audio",
        metadata={"turn_id": "t2"},
    )
    assert first is not None and second is not None and first != second

    again = await svc.get_session(tenant.workspace_id, tenant.user_id, info.id)
    assert again.id == info.id
    seed = await svc.load_seed(tenant.workspace_id, tenant.user_id, info.id, 10)
    assert seed == [("user", "hello"), ("assistant", "hi there")]

    # Another user's (or workspace's) session is indistinguishable from a missing one.
    with pytest.raises(SessionNotOwned):
        await svc.get_session(tenant.workspace_id, tenant.other_user_id, info.id)
    with pytest.raises(SessionNotOwned):
        await svc.get_session(other.workspace_id, other.user_id, info.id)


@pytest.mark.asyncio
async def test_failed_save_raises_instead_of_returning_a_random_id(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    svc = VoicePersistenceService(app_engine)
    with pytest.raises(VoicePersistenceError):
        await svc.save_turn(
            tenant.workspace_id,
            tenant.user_id,
            uuid4(),
            role="user",
            content="x",
            modality="audio",
        )
