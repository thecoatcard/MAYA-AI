"""Per-user settings (migration 0015) and the classifier driven by the real preference."""

import asyncio
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.tenant import tenant_session
from app.tasks.queue import SubtaskQueue
from tests.conftest import TestDatabase, alembic_config
from tests.postgres import test_chat_api_postgres as base
from tests.postgres.test_chat_api_postgres import Stack, events, model_calls, names, onboard, post
from tests.support.postgres import sqlstate
from tests.support.settings import make_settings

stack = base.stack

pytestmark = [pytest.mark.postgres, pytest.mark.redis]


@pytest.fixture(autouse=True)
def tool_path_without_inline_router(stack: Stack) -> None:
    # Inline routing (MAYA_CHAT_INLINE_FILES, on by default) decides some prompts before the
    # classifier or the model runs. These tests cover the classifier, so inline routing is
    # switched off here. Inline routing is covered in tests/integration/test_chat_inline_files.py.
    stack.app.state.settings = make_settings(redis_url=base.redis_url(), chat_inline_files=False)


SETTINGS = "/api/v1/settings/chat"


async def put(stack: Stack, account: Any, value: bool) -> Any:
    return await stack.client.put(
        SETTINGS, json={"intent_classifier": value}, headers=account.headers
    )


def master(stack: Stack, on: bool) -> None:
    stack.app.state.settings = make_settings(
        redis_url=base.redis_url(), chat_intent_classifier=on, chat_inline_files=False
    )


async def test_the_default_is_off_and_the_round_trip_persists(stack: Stack) -> None:
    alice = await onboard(stack)
    first = await stack.client.get(SETTINGS, headers=alice.headers)
    assert first.json() == {
        "intent_classifier": False,
        "classifier_available": False,
        "memory_in_chat": True,  # chat memory is on unless the user turns it off
    }
    assert (await put(stack, alice, True)).json() == {
        "intent_classifier": True,
        "classifier_available": False,  # the deployment switch is off: stored, no effect
        "memory_in_chat": True,
    }
    stored = await stack.client.get(SETTINGS, headers=alice.headers)
    assert stored.json()["intent_classifier"] is True
    master(stack, True)
    assert (await stack.client.get(SETTINGS, headers=alice.headers)).json() == {
        "intent_classifier": True,
        "classifier_available": True,
        "memory_in_chat": True,
    }


async def test_put_is_an_idempotent_upsert(stack: Stack) -> None:
    alice = await onboard(stack)
    for value in (True, True, False, False):
        assert (await put(stack, alice, value)).status_code == 200
    async with stack.admin.connect() as connection:
        rows = list(
            await connection.execute(
                text("SELECT settings FROM user_settings WHERE workspace_id = :w"),
                {"w": alice.workspace_id},
            )
        )
    assert [r.settings for r in rows] == [{"chat_intent_classifier": False}]


async def test_settings_need_authentication_and_reject_unknown_fields(stack: Stack) -> None:
    alice = await onboard(stack)
    assert (await stack.client.get(SETTINGS)).status_code == 401
    assert (await stack.client.put(SETTINGS, json={"intent_classifier": True})).status_code == 401
    bad = await stack.client.put(
        SETTINGS, json={"intent_classifier": True, "role": "admin"}, headers=alice.headers
    )
    assert bad.status_code == 422


async def test_settings_are_private_to_the_user_and_the_workspace(stack: Stack) -> None:
    alice = await onboard(stack)
    bob = await onboard(stack)
    colleague = await base.colleague(stack, alice)
    await put(stack, alice, True)
    for other in (bob, colleague):
        seen = await stack.client.get(SETTINGS, headers=other.headers)
        assert seen.json()["intent_classifier"] is False
    # row-level security: another workspace sees nothing even without a filter
    engine: AsyncEngine = stack.app.state.resources.engine
    async with tenant_session(engine, bob.workspace_id, bob.user_id) as session:
        assert (await session.execute(text("SELECT count(*) FROM user_settings"))).scalar() == 0
    async with tenant_session(engine, alice.workspace_id, alice.user_id) as session:
        assert (await session.execute(text("SELECT count(*) FROM user_settings"))).scalar() == 1
        with pytest.raises(DBAPIError) as denied:  # WITH CHECK: no writes into another tenant
            await session.execute(
                text("INSERT INTO user_settings (workspace_id, user_id) VALUES (:w, :u)"),
                {"w": bob.workspace_id, "u": bob.user_id},
            )
        assert sqlstate(denied.value) in {"42501", "23503"}
    async with tenant_session(engine, alice.workspace_id, alice.user_id) as session:
        with pytest.raises(DBAPIError) as no_delete:
            await session.execute(text("DELETE FROM user_settings"))
        assert sqlstate(no_delete.value) == "42501"


@pytest.mark.parametrize(("on", "pref"), [(False, False), (False, True), (True, False)])
async def test_classifier_makes_no_extra_call_unless_both_allow(
    stack: Stack, on: bool, pref: bool
) -> None:
    alice = await onboard(stack)
    stack.app.state.resources.subtask_queue = SubtaskQueue()
    master(stack, on)
    await put(stack, alice, pref)
    frames = events(await post(stack, alice, "Research our competitors"))
    assert names(frames) == ["message.accepted", *["delta"] * 4, "done"]
    assert [c for c in stack.fake.aio.models.calls if c["method"] == "generate_content"] == []
    assert model_calls(stack) == 1


async def test_classifier_on_with_the_real_preference_creates_the_task(stack: Stack) -> None:
    alice = await onboard(stack)
    stack.app.state.resources.subtask_queue = SubtaskQueue()
    master(stack, True)
    await put(stack, alice, True)
    stack.models.queued_text_responses = ['{"work_request": true, "title": "Rival report"}']
    stack.models.queued_stream_scripts = [[{"text": "Started the report."}]]
    frames = events(await post(stack, alice, "Research our competitors and write a report"))
    assert names(frames)[:2] == ["message.accepted", "task.created"]
    assert frames[1].data["title"] == "Rival report"
    async with stack.admin.connect() as connection:
        count = (
            await connection.execute(
                text("SELECT count(*) FROM tasks WHERE workspace_id = :w"),
                {"w": alice.workspace_id},
            )
        ).scalar()
    assert count == 1


async def test_0015_downgrade_and_upgrade_round_trip(fresh_database: TestDatabase) -> None:
    config = alembic_config(fresh_database)
    engine = create_async_engine(fresh_database.admin_url, poolclass=NullPool)

    async def exists() -> bool:
        async with engine.connect() as connection:
            found = await connection.execute(text("SELECT to_regclass('public.user_settings')"))
            return found.scalar() is not None

    try:
        assert not await exists()
        await asyncio.to_thread(command.upgrade, config, "head")
        assert await exists()
        await asyncio.to_thread(command.downgrade, config, "0014")
        assert not await exists()
        await asyncio.to_thread(command.upgrade, config, "head")
        assert await exists()
        async with engine.connect() as connection:
            flags = (
                await connection.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = 'user_settings'"
                    )
                )
            ).one()
            grants = {
                row[0]
                for row in await connection.execute(
                    text(
                        "SELECT privilege_type FROM information_schema.role_table_grants "
                        "WHERE table_name = 'user_settings' AND grantee = 'maya_app'"
                    )
                )
            }
        assert tuple(flags) == (True, True)
        assert grants == {"SELECT", "INSERT", "UPDATE"}
    finally:
        await engine.dispose()
