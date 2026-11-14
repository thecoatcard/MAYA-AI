"""The chat flags and feedback documents against real PostgreSQL: compare-and-set under
concurrency, no lost updates, and one user's document is invisible to another."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.repositories.user_settings import UserSettingsStore
from app.services import chat_library
from tests.support.postgres import SeededTenant

pytestmark = pytest.mark.postgres


async def test_concurrent_pins_all_land_and_other_users_see_nothing(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, b = fresh_tenants
    store = chat_library.JsonDocStore(UserSettingsStore(app_engine), chat_library.SESSION_FLAGS_KEY)
    ids = [f"session-{i}" for i in range(6)]

    async def pin(session_id: str) -> None:
        await store.update(
            a.workspace_id,
            a.user_id,
            lambda doc: chat_library.apply_session_patch(
                doc, session_id, pinned=True, archived=None, lock_title=False, now=session_id
            ),
            trim=chat_library.trim_flags,
        )

    await asyncio.gather(*(pin(i) for i in ids))
    mine = await store.load(a.workspace_id, a.user_id)
    assert set(mine) == set(ids)  # no update was lost to a race
    assert all(chat_library.flags_of(mine, i) == (True, False) for i in ids)
    assert await store.load(b.workspace_id, b.user_id) == {}
    assert (
        await store.load(b.workspace_id, a.user_id) == {}
    )  # another workspace: row-level security

    await store.update(
        a.workspace_id, a.user_id, lambda doc: chat_library.prune_flags(doc, {"session-3"})
    )
    assert set(await store.load(a.workspace_id, a.user_id)) == {"session-3"}


async def test_feedback_round_trip_keeps_the_newest_entries(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    a, _ = fresh_tenants
    store = chat_library.JsonDocStore(UserSettingsStore(app_engine), chat_library.FEEDBACK_KEY)

    def rate(doc: dict[str, object], mid: str, value: str | None, ts: str) -> None:
        chat_library.set_feedback(
            doc, mid, value=value, note=None, session_id="s", model_id="m", ts=ts
        )

    await store.update(a.workspace_id, a.user_id, lambda d: rate(d, "m1", "up", "2026-11-13T01"))
    await store.update(a.workspace_id, a.user_id, lambda d: rate(d, "m2", "down", "2026-11-13T02"))
    doc = await store.load(a.workspace_id, a.user_id)
    assert (
        chat_library.feedback_of(doc, "m1") == "up"
        and chat_library.feedback_of(doc, "m2") == "down"
    )
    await store.update(a.workspace_id, a.user_id, lambda d: rate(d, "m1", None, "2026-11-13T03"))
    assert set(await store.load(a.workspace_id, a.user_id)) == {"m2"}
