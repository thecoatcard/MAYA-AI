"""ChatRepository against a real PostgreSQL database.

Tenant isolation (workspace and user), ordering, keyset pagination, cursor validation and the
behaviour without a tenant context. Rows that must share a timestamp are written as the
administrator, which bypasses row-level security; everything else goes through the repository as
the restricted runtime role, exactly as the API does.
"""

import ast
import base64
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.repositories.chat import (
    ChatMessageRecord,
    ChatRepository,
    InvalidCursor,
    SessionNotFound,
)
from app.db.tenant import tenant_transaction
from app.schemas.chat import MessageOut, SessionOut
from tests.support.postgres import SeededTenant, sqlstate

pytestmark = pytest.mark.postgres

Tenants = tuple[SeededTenant, SeededTenant]
APP = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def tenants(fresh_tenants: Tenants) -> Tenants:
    """This module commits rows, so it works on its own workspaces."""
    return fresh_tenants


@asynccontextmanager
async def repository(
    engine: AsyncEngine, tenant: SeededTenant, user_id: UUID | None = None
) -> AsyncIterator[ChatRepository]:
    """One committed transaction in the tenant's context, as the API opens it."""
    user = tenant.user_id if user_id is None else user_id
    async with tenant_transaction(engine, tenant.workspace_id) as connection:
        yield ChatRepository(connection, tenant.workspace_id, user)


async def new_session(
    engine: AsyncEngine, tenant: SeededTenant, user_id: UUID | None = None, title: str = "chat"
) -> UUID:
    async with repository(engine, tenant, user_id) as repo:
        return (await repo.create_session(title)).id


async def new_messages(
    engine: AsyncEngine,
    tenant: SeededTenant,
    session_id: UUID,
    count: int,
    user_id: UUID | None = None,
) -> list[ChatMessageRecord]:
    """``count`` alternating user and assistant messages, added in one transaction."""
    async with repository(engine, tenant, user_id) as repo:
        return [
            await repo.add_message(
                session_id, "user" if index % 2 == 0 else "orchestrator", f"message {index}"
            )
            for index in range(count)
        ]


async def table_count(admin_engine: AsyncEngine, table: Literal["sessions", "messages"]) -> int:
    statements = {
        "sessions": text("SELECT count(*) FROM sessions"),
        "messages": text("SELECT count(*) FROM messages"),
    }
    async with admin_engine.connect() as connection:
        return int((await connection.execute(statements[table])).scalar_one())


async def insert_messages_at(
    admin_engine: AsyncEngine,
    tenant: SeededTenant,
    session_id: UUID,
    created_at: datetime,
    labels: list[str],
) -> list[UUID]:
    ids = [uuid4() for _ in labels]
    async with admin_engine.begin() as connection:
        for message_id, label in zip(ids, labels, strict=True):
            await connection.execute(
                text(
                    "INSERT INTO messages (id, workspace_id, session_id, sender, content, "
                    "created_at) VALUES (:id, :ws, :session, 'user', :content, :at)"
                ),
                {
                    "id": message_id,
                    "ws": tenant.workspace_id,
                    "session": session_id,
                    "content": label,
                    "at": created_at,
                },
            )
    return ids


async def insert_sessions_at(
    admin_engine: AsyncEngine,
    tenant: SeededTenant,
    user_id: UUID,
    created_at: datetime,
    count: int,
) -> list[tuple[datetime, UUID]]:
    ids = [uuid4() for _ in range(count)]
    async with admin_engine.begin() as connection:
        for session_id in ids:
            await connection.execute(
                text(
                    "INSERT INTO sessions (id, workspace_id, user_id, title, created_at) "
                    "VALUES (:id, :ws, :user, 'tied', :at)"
                ),
                {"id": session_id, "ws": tenant.workspace_id, "user": user_id, "at": created_at},
            )
    return [(created_at, session_id) for session_id in ids]


# ---------------------------------------------------------------------------------------------
# Sessions


async def test_a_created_session_belongs_to_the_caller_and_can_be_read_back(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    before = datetime.now(UTC) - timedelta(minutes=1)
    async with repository(app_engine, tenant, tenant.other_user_id) as repo:
        created = await repo.create_session("Summarize the open risks.", "desktop")
    assert created.workspace_id == tenant.workspace_id
    assert created.user_id == tenant.other_user_id
    assert (created.title, created.channel, created.status) == (
        "Summarize the open risks.",
        "desktop",
        "active",
    )
    assert created.created_at.tzinfo is not None and created.created_at > before
    async with repository(app_engine, tenant, tenant.other_user_id) as repo:
        assert await repo.get_session(created.id) == created
    async with admin_engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT workspace_id, user_id, title FROM sessions WHERE id = :id"),
                {"id": created.id},
            )
        ).one()
    assert (row.workspace_id, row.user_id, row.title) == (
        tenant.workspace_id,
        tenant.other_user_id,
        "Summarize the open risks.",
    )


async def test_a_session_without_a_title_defaults_to_the_web_channel(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    async with repository(app_engine, tenants[0]) as repo:
        created = await repo.create_session()
    assert created.title is None and created.channel == "web"


async def test_a_title_is_cleaned_and_cut_to_the_column_width(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    async with repository(app_engine, tenants[0]) as repo:
        long = await repo.create_session("t" * 400)
        dirty = await repo.create_session("a\x00b\ud800c")
    assert long.title == "t" * 255
    assert dirty.title == "a\N{REPLACEMENT CHARACTER}b\N{REPLACEMENT CHARACTER}c"


async def test_records_map_to_the_api_models(app_engine: AsyncEngine, tenants: Tenants) -> None:
    async with repository(app_engine, tenants[0]) as repo:
        session = await repo.create_session("hello")
        user = await repo.add_message(session.id, "user", "hi")
        reply = await repo.add_message(
            session.id,
            "orchestrator",
            "hello",
            metadata={"model_id": "gemini-4-hydrogen", "finish_reason": "stop"},
        )
    assert SessionOut.model_validate(session).title == "hello"
    as_user = MessageOut.model_validate(user)
    assert (as_user.role, as_user.finish_reason, as_user.model_id) == ("user", None, None)
    as_reply = MessageOut.model_validate(reply)
    assert (as_reply.role, as_reply.finish_reason, as_reply.model_id) == (
        "assistant",
        "stop",
        "gemini-4-hydrogen",
    )


async def test_a_voice_turn_maps_modality_and_safe_metadata(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    async with repository(app_engine, tenants[0]) as repo:
        session = await repo.create_session("voice")
        spoken = await repo.add_message(
            session.id,
            "user",
            "hello",
            modality="audio",
            metadata={"turn_id": "t-1", "source": "live", "interrupted": True},
        )
        typed = await repo.add_message(session.id, "user", "typed")
    out = MessageOut.model_validate(spoken)
    assert (out.modality, out.turn_id, out.source) == ("audio", "t-1", "live")
    plain = MessageOut.model_validate(typed)
    assert (plain.modality, plain.turn_id, plain.source) == ("text", None, None)


async def test_the_seeded_session_and_message_are_readable_by_their_owner(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    async with repository(app_engine, tenant) as repo:
        page = await repo.list_messages(tenant.session_id)
        listed = await repo.list_sessions()
    assert [message.content for message in page.items] == ["seed"]
    assert page.items[0].sender == "user" and page.items[0].metadata == {}
    assert [session.id for session in listed.items] == [tenant.session_id]


# ---------------------------------------------------------------------------------------------
# Isolation


async def test_another_workspace_cannot_read_or_write_with_forged_ids(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    first, second = tenants
    session_id = await new_session(app_engine, first)
    (message,) = await new_messages(app_engine, first, session_id, 1)
    sessions_before = await table_count(admin_engine, "sessions")
    messages_before = await table_count(admin_engine, "messages")
    async with repository(app_engine, second) as repo:
        assert await repo.get_session(session_id) is None
        assert await repo.get_session(first.session_id) is None
        assert await repo.get_message(message.id) is None
        assert {s.id for s in (await repo.list_sessions()).items} == {second.session_id}
        with pytest.raises(SessionNotFound):
            await repo.list_messages(session_id)
        with pytest.raises(SessionNotFound):
            await repo.recent_messages(session_id, 10)
        with pytest.raises(SessionNotFound):
            await repo.add_message(session_id, "user", "planted in someone else's chat")
        with pytest.raises(SessionNotFound):
            await repo.add_message(first.session_id, "user", "planted in the seeded chat")
    assert await table_count(admin_engine, "sessions") == sessions_before
    assert await table_count(admin_engine, "messages") == messages_before
    async with repository(app_engine, first) as repo:
        contents = [m.content for m in (await repo.list_messages(session_id)).items]
    assert contents == ["message 0"]


async def test_a_repository_given_a_forged_workspace_or_user_sees_nothing(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    """Row-level security and the explicit filters each hold on their own."""
    first, second = tenants
    session_id = await new_session(app_engine, first)
    (message,) = await new_messages(app_engine, first, session_id, 1)
    messages_before = await table_count(admin_engine, "messages")
    forged = (
        # workspace B's transaction, claiming workspace A and its user
        (second.workspace_id, first.workspace_id, first.user_id),
        # workspace B's transaction, claiming workspace B but the user of workspace A
        (second.workspace_id, second.workspace_id, first.user_id),
        # workspace A's transaction, claiming workspace B
        (first.workspace_id, second.workspace_id, first.user_id),
    )
    for context, claimed_workspace, claimed_user in forged:
        async with tenant_transaction(app_engine, context) as connection:
            repo = ChatRepository(connection, claimed_workspace, claimed_user)
            assert await repo.get_session(session_id) is None
            assert await repo.get_message(message.id) is None
            assert session_id not in {s.id for s in (await repo.list_sessions()).items}
            with pytest.raises(SessionNotFound):
                await repo.list_messages(session_id)
            with pytest.raises(SessionNotFound):
                await repo.add_message(session_id, "user", "forged")
    assert await table_count(admin_engine, "messages") == messages_before


async def test_a_session_cannot_be_created_in_a_workspace_other_than_the_context(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    first, second = tenants
    before = await table_count(admin_engine, "sessions")
    with pytest.raises(DBAPIError) as caught:
        async with tenant_transaction(app_engine, second.workspace_id) as connection:
            await ChatRepository(connection, first.workspace_id, first.user_id).create_session("x")
    assert sqlstate(caught.value) == "42501"
    assert await table_count(admin_engine, "sessions") == before


async def test_a_colleague_in_the_same_workspace_cannot_see_or_write_to_my_session(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    mine, colleague = tenant.user_id, tenant.other_user_id
    session_id = await new_session(app_engine, tenant, mine, "private")
    (message,) = await new_messages(app_engine, tenant, session_id, 1, mine)
    messages_before = await table_count(admin_engine, "messages")
    async with repository(app_engine, tenant, colleague) as repo:
        assert await repo.get_session(session_id) is None
        assert await repo.get_session(tenant.session_id) is None  # the seeded one is mine too
        assert await repo.get_message(message.id) is None
        assert (await repo.list_sessions()).items == ()
        with pytest.raises(SessionNotFound):
            await repo.list_messages(session_id)
        with pytest.raises(SessionNotFound):
            await repo.list_messages(tenant.session_id)
        with pytest.raises(SessionNotFound):
            await repo.recent_messages(session_id, 10)
        with pytest.raises(SessionNotFound):
            await repo.add_message(session_id, "user", "planted by a colleague")
        with pytest.raises(SessionNotFound):
            await repo.add_message(
                session_id, "orchestrator", "forged answer", message_id=uuid4(), metadata={"a": 1}
            )
    assert await table_count(admin_engine, "messages") == messages_before
    async with repository(app_engine, tenant, mine) as repo:
        assert (await repo.get_session(session_id)) is not None
        assert [m.content for m in (await repo.list_messages(session_id)).items] == ["message 0"]
        assert await repo.get_message(message.id) == message


async def test_two_users_of_one_workspace_each_see_only_their_own_conversations(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    mine = await new_session(app_engine, tenant, tenant.user_id, "mine")
    theirs = await new_session(app_engine, tenant, tenant.other_user_id, "theirs")
    await new_messages(app_engine, tenant, mine, 2, tenant.user_id)
    await new_messages(app_engine, tenant, theirs, 3, tenant.other_user_id)
    async with repository(app_engine, tenant, tenant.user_id) as repo:
        assert {s.title for s in (await repo.list_sessions()).items} == {"mine", None}
        assert len((await repo.list_messages(mine)).items) == 2
        with pytest.raises(SessionNotFound):
            await repo.list_messages(theirs)
    async with repository(app_engine, tenant, tenant.other_user_id) as repo:
        assert [s.title for s in (await repo.list_sessions()).items] == ["theirs"]
        assert len((await repo.list_messages(theirs)).items) == 3
        with pytest.raises(SessionNotFound):
            await repo.list_messages(mine)


async def test_without_a_tenant_context_the_repository_returns_nothing_and_writes_nothing(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    (message,) = await new_messages(app_engine, tenant, session_id, 1)
    messages_before = await table_count(admin_engine, "messages")
    async with tenant_transaction(app_engine, None) as connection:
        repo = ChatRepository(connection, tenant.workspace_id, tenant.user_id)
        assert await repo.get_session(session_id) is None
        assert await repo.get_message(message.id) is None
        assert (await repo.list_sessions()).items == ()
        with pytest.raises(SessionNotFound):
            await repo.list_messages(session_id)
        with pytest.raises(SessionNotFound):
            await repo.recent_messages(session_id, 5)
        with pytest.raises(SessionNotFound):
            await repo.add_message(session_id, "user", "no context")
    with pytest.raises(DBAPIError) as caught:
        async with tenant_transaction(app_engine, None) as connection:
            await ChatRepository(connection, tenant.workspace_id, tenant.user_id).create_session()
    assert sqlstate(caught.value) == "42501"
    assert await table_count(admin_engine, "messages") == messages_before


# ---------------------------------------------------------------------------------------------
# add_message


async def test_a_message_is_stored_and_returned_with_its_fields(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    async with repository(app_engine, tenant) as repo:
        stored = await repo.add_message(session_id, "user", 'Hello\nworld \U0001f600 "quoted"')
    assert (stored.workspace_id, stored.session_id) == (tenant.workspace_id, session_id)
    assert (stored.sender, stored.modality, stored.metadata) == ("user", "text", {})
    assert stored.content == 'Hello\nworld \U0001f600 "quoted"'
    assert stored.created_at.tzinfo is not None
    async with admin_engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT content, sender, modality FROM messages WHERE id = :id"),
                {"id": stored.id},
            )
        ).one()
    assert tuple(row) == (stored.content, "user", "text")


async def test_a_caller_supplied_message_id_is_used(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    announced = uuid4()
    async with repository(app_engine, tenant) as repo:
        stored = await repo.add_message(session_id, "orchestrator", "reply", message_id=announced)
    assert stored.id == announced
    async with repository(app_engine, tenant) as repo:
        assert await repo.get_message(announced) == stored
    with pytest.raises(IntegrityError):  # an id is unique; reusing it is a bug, not an upsert
        async with repository(app_engine, tenant) as repo:
            await repo.add_message(session_id, "orchestrator", "again", message_id=announced)


async def test_metadata_round_trips_as_a_json_object(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    metadata = {
        "model_id": "gemini-4-hydrogen",
        "alias": "orchestrator",
        "finish_reason": "stop",
        "status": "completed",
        "latency_ms": 1234,
        "usage": {
            "prompt_tokens": 10,
            "candidates_tokens": 20,
            "cost_usd": None,
            "estimate": False,
        },
        "nested": {"list": [1, 2.5, "x", None, True], "text": 'café \U0001f600 "q" \\ \n'},
    }
    async with repository(app_engine, tenant) as repo:
        stored = await repo.add_message(session_id, "orchestrator", "reply", metadata=metadata)
    assert stored.metadata == metadata
    async with repository(app_engine, tenant) as repo:
        again = await repo.get_message(stored.id)
        listed = (await repo.list_messages(session_id)).items
    assert again is not None and again.metadata == metadata
    assert listed[0].metadata == metadata
    assert (again.finish_reason, again.model_id) == ("stop", "gemini-4-hydrogen")


async def test_metadata_defaults_to_an_empty_object_and_converts_decimal_uuid_and_time(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    moment = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    reference = uuid4()
    async with repository(app_engine, tenant) as repo:
        empty = await repo.add_message(session_id, "user", "x", metadata=None)
        converted = await repo.add_message(
            session_id,
            "orchestrator",
            "y",
            metadata={"cost": Decimal("1E-7"), "ref": reference, "at": moment},
        )
    assert empty.metadata == {}
    assert converted.metadata == {
        "cost": "0.0000001",
        "ref": str(reference),
        "at": moment.isoformat(),
    }


async def test_metadata_that_is_not_a_json_object_is_refused_before_anything_is_written(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    before = await table_count(admin_engine, "messages")
    async with repository(app_engine, tenant) as repo:
        with pytest.raises(TypeError):
            await repo.add_message(session_id, "user", "x", metadata=[1, 2])  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            await repo.add_message(session_id, "user", "x", metadata={"o": object()})
        stored = await repo.add_message(session_id, "user", "still works")  # transaction is fine
    assert stored.content == "still works"
    assert await table_count(admin_engine, "messages") == before + 1


async def test_characters_the_database_cannot_store_are_replaced_not_fatal(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    async with repository(app_engine, tenant) as repo:
        stored = await repo.add_message(session_id, "orchestrator", "a\x00b\ud83dc \U0001f600")
    assert stored.content == "a\N{REPLACEMENT CHARACTER}b\N{REPLACEMENT CHARACTER}c \U0001f600"


async def test_adding_to_a_session_that_does_not_exist_writes_nothing_and_keeps_the_transaction(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    before = await table_count(admin_engine, "messages")
    async with repository(app_engine, tenant) as repo:
        with pytest.raises(SessionNotFound):
            await repo.add_message(uuid4(), "user", "nobody home")
        recovered = await repo.create_session("created after the failure")
    assert recovered.title == "created after the failure"
    assert await table_count(admin_engine, "messages") == before


async def test_the_transaction_owner_decides_whether_messages_are_kept(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    with pytest.raises(RuntimeError):
        async with repository(app_engine, tenant) as repo:
            await repo.add_message(session_id, "user", "rolled back")
            raise RuntimeError("boom")
    async with repository(app_engine, tenant) as repo:
        assert (await repo.list_messages(session_id)).items == ()


# ---------------------------------------------------------------------------------------------
# Ordering


async def test_messages_added_in_one_transaction_keep_their_insertion_order(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    created = await new_messages(app_engine, tenant, session_id, 8)
    stamps = [message.created_at for message in created]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)  # strictly increasing
    async with repository(app_engine, tenant) as repo:
        page = await repo.list_messages(session_id)
    assert [m.id for m in page.items] == [m.id for m in created]
    assert [m.content for m in page.items] == [f"message {i}" for i in range(8)]
    assert page.next_cursor is None


async def test_sessions_are_listed_newest_first(app_engine: AsyncEngine, tenants: Tenants) -> None:
    tenant = tenants[0]
    user = tenant.other_user_id
    ids: list[UUID] = []
    async with repository(app_engine, tenant, user) as repo:
        for index in range(5):
            ids.append((await repo.create_session(f"s{index}")).id)
    async with repository(app_engine, tenant, user) as repo:
        page = await repo.list_sessions()
    assert [s.id for s in page.items] == list(reversed(ids))
    assert [s.title for s in page.items] == ["s4", "s3", "s2", "s1", "s0"]


async def test_recent_messages_are_the_newest_n_in_oldest_first_order(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    created = await new_messages(app_engine, tenant, session_id, 6)
    async with repository(app_engine, tenant) as repo:
        assert [m.id for m in await repo.recent_messages(session_id, 3)] == [
            m.id for m in created[-3:]
        ]
        assert [m.id for m in await repo.recent_messages(session_id, 1)] == [created[-1].id]
        assert [m.id for m in await repo.recent_messages(session_id, 6)] == [m.id for m in created]
        assert [m.id for m in await repo.recent_messages(session_id, 500)] == [
            m.id for m in created
        ]


async def test_recent_messages_on_an_empty_session_is_an_empty_list(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    async with repository(app_engine, tenant) as repo:
        assert await repo.recent_messages(session_id, 10) == []


async def test_only_the_conversation_turns_are_listed_but_any_message_can_be_fetched(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    async with repository(app_engine, tenant) as repo:
        user = await repo.add_message(session_id, "user", "question")
        agent = await repo.add_message(session_id, "agent", "internal step")
        system = await repo.add_message(session_id, "system", "internal note")
        reply = await repo.add_message(session_id, "orchestrator", "answer")
    async with repository(app_engine, tenant) as repo:
        assert [m.id for m in (await repo.list_messages(session_id)).items] == [user.id, reply.id]
        assert [m.id for m in await repo.recent_messages(session_id, 10)] == [user.id, reply.id]
        assert await repo.get_message(agent.id) == agent
        assert await repo.get_message(system.id) == system
    assert (user.role, reply.role) == ("user", "assistant")
    with pytest.raises(ValueError, match="no chat role"):
        _ = agent.role


@pytest.mark.parametrize("limit", [0, -1, 501, True])
async def test_recent_messages_refuses_a_limit_outside_one_to_five_hundred(
    app_engine: AsyncEngine, tenants: Tenants, limit: int
) -> None:
    session_id = await new_session(app_engine, tenants[0])
    async with repository(app_engine, tenants[0]) as repo:
        with pytest.raises(ValueError, match="limit"):
            await repo.recent_messages(session_id, limit)


@pytest.mark.parametrize("limit", [0, -5, 101, True])
async def test_list_methods_refuse_a_limit_outside_one_to_one_hundred(
    app_engine: AsyncEngine, tenants: Tenants, limit: int
) -> None:
    session_id = await new_session(app_engine, tenants[0])
    async with repository(app_engine, tenants[0]) as repo:
        with pytest.raises(ValueError, match="limit"):
            await repo.list_sessions(limit)
        with pytest.raises(ValueError, match="limit"):
            await repo.list_messages(session_id, limit)


# ---------------------------------------------------------------------------------------------
# Pagination


async def walk_messages(
    engine: AsyncEngine, tenant: SeededTenant, session_id: UUID, limit: int
) -> list[list[UUID]]:
    """Every page, each read in its own transaction as separate requests would."""
    pages: list[list[UUID]] = []
    cursor: str | None = None
    for _ in range(100):
        async with repository(engine, tenant) as repo:
            page = await repo.list_messages(session_id, limit, cursor)
        pages.append([m.id for m in page.items])
        cursor = page.next_cursor
        if cursor is None:
            return pages
    raise AssertionError("pagination did not terminate")


async def walk_sessions(
    engine: AsyncEngine, tenant: SeededTenant, user_id: UUID, limit: int
) -> list[list[UUID]]:
    pages: list[list[UUID]] = []
    cursor: str | None = None
    for _ in range(100):
        async with repository(engine, tenant, user_id) as repo:
            page = await repo.list_sessions(limit, cursor)
        pages.append([s.id for s in page.items])
        cursor = page.next_cursor
        if cursor is None:
            return pages
    raise AssertionError("pagination did not terminate")


async def test_messages_paginate_without_gaps_or_duplicates(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    created = await new_messages(app_engine, tenant, session_id, 11)
    expected = [m.id for m in created]
    for limit in (1, 2, 3, 5, 10, 11, 12, 100):
        pages = await walk_messages(app_engine, tenant, session_id, limit)
        assert [i for page in pages for i in page] == expected, f"limit {limit}"
        assert all(len(page) == limit for page in pages[:-1])
        assert 0 < len(pages[-1]) <= limit


async def test_the_last_full_page_does_not_promise_a_next_page(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    await new_messages(app_engine, tenant, session_id, 4)
    async with repository(app_engine, tenant) as repo:
        first = await repo.list_messages(session_id, 2)
        second = await repo.list_messages(session_id, 2, first.next_cursor)
        assert first.next_cursor is not None
        assert re.fullmatch(r"[A-Za-z0-9_-]+", first.next_cursor)  # opaque, URL-safe, unpadded
        assert len(second.items) == 2 and second.next_cursor is None
        assert (await repo.list_messages(session_id, 4)).next_cursor is None


async def test_rows_that_share_a_timestamp_are_neither_skipped_nor_repeated(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    session_id = await new_session(app_engine, tenant)
    tied = datetime(2026, 11, 5, 12, 0, 0, 123456, tzinfo=UTC)
    later = tied + timedelta(seconds=1)
    earlier = tied - timedelta(seconds=1)
    rows: list[tuple[datetime, UUID]] = []
    for moment, labels in (
        (tied, [f"t{i}" for i in range(7)]),
        (later, ["l0", "l1"]),
        (earlier, ["e0"]),
    ):
        ids = await insert_messages_at(admin_engine, tenant, session_id, moment, labels)
        rows += [(moment, message_id) for message_id in ids]
    expected = [message_id for _, message_id in sorted(rows)]  # oldest first, id breaks ties
    assert len(expected) == 10
    for limit in (1, 2, 3, 4, 7, 9, 10, 11):
        pages = await walk_messages(app_engine, tenant, session_id, limit)
        assert [i for page in pages for i in page] == expected, f"limit {limit}"


async def test_sessions_paginate_newest_first_across_ties_without_gaps_or_duplicates(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    user = tenant.other_user_id
    tied = datetime(2026, 11, 5, 9, 0, 0, tzinfo=UTC)
    rows = await insert_sessions_at(admin_engine, tenant, user, tied, 6)
    rows += await insert_sessions_at(admin_engine, tenant, user, tied + timedelta(hours=1), 2)
    rows += await insert_sessions_at(admin_engine, tenant, user, tied - timedelta(hours=1), 1)
    expected = [session_id for _, session_id in sorted(rows, reverse=True)]
    assert len(expected) == 9
    for limit in (1, 2, 4, 8, 9, 10, 50):
        pages = await walk_sessions(app_engine, tenant, user, limit)
        assert [i for page in pages for i in page] == expected, f"limit {limit}"


async def test_a_session_created_between_pages_does_not_shift_the_next_page(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    user = tenant.other_user_id
    async with repository(app_engine, tenant, user) as repo:
        for index in range(5):
            await repo.create_session(f"s{index}")
    async with repository(app_engine, tenant, user) as repo:
        first = await repo.list_sessions(2)
    await new_session(app_engine, tenant, user, "created while paging")
    async with repository(app_engine, tenant, user) as repo:
        second = await repo.list_sessions(2, first.next_cursor)
        third = await repo.list_sessions(2, second.next_cursor)
    assert [s.title for s in first.items] == ["s4", "s3"]
    assert [s.title for s in second.items] == ["s2", "s1"]
    assert [s.title for s in third.items] == ["s0"] and third.next_cursor is None


# ---------------------------------------------------------------------------------------------
# Cursors


def decode_cursor_body(cursor: str) -> dict[str, Any]:
    body = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    assert isinstance(body, dict)
    return body


def encode_cursor_body(body: object) -> str:
    raw = json.dumps(body, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def amended(**changes: object) -> Callable[[str], str]:
    return lambda cursor: encode_cursor_body({**decode_cursor_body(cursor), **changes})


def without(key: str) -> Callable[[str], str]:
    return lambda cursor: encode_cursor_body(
        {k: v for k, v in decode_cursor_body(cursor).items() if k != key}
    )


def respelled(key: str, change: Callable[[str], str]) -> Callable[[str], str]:
    return lambda cursor: amended(**{key: change(decode_cursor_body(cursor)[key])})(cursor)


def flip_first_character(cursor: str) -> str:
    return ("A" if cursor[0] != "A" else "B") + cursor[1:]


def other_zone(value: str) -> str:
    # The same instant, written with a different offset.
    return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=2))).isoformat()


BAD_CURSORS: dict[str, Callable[[str], str]] = {
    "empty": lambda cursor: "",
    "blank": lambda cursor: "   ",
    "garbage": lambda cursor: "!!!not-a-cursor!!!",
    "non_ascii": lambda cursor: cursor + "é",
    "too_long": lambda cursor: "A" * 600,
    "padded": lambda cursor: cursor + "==",
    "extra_character": lambda cursor: cursor + "A",
    "truncated": lambda cursor: cursor[:-4],
    "first_character_changed": flip_first_character,
    "not_json": lambda cursor: base64.urlsafe_b64encode(b"not json").decode().rstrip("="),
    "json_null": lambda cursor: encode_cursor_body(None),
    "json_list": lambda cursor: encode_cursor_body([]),
    "json_string": lambda cursor: encode_cursor_body("sessions"),
    "empty_object": lambda cursor: encode_cursor_body({}),
    "missing_id": without("i"),
    "missing_time": without("t"),
    "extra_key": amended(extra=1),
    "wrong_kind": amended(k="something_else"),
    "kind_not_text": amended(k=None),
    "time_not_text": amended(t=1_700_000_000),
    "time_without_zone": amended(t="2026-11-05T10:00:00"),
    "time_garbage": amended(t="yesterday"),
    "time_other_spelling_of_a_valid_instant": respelled("t", other_zone),
    "id_not_text": amended(i=5),
    "id_not_a_uuid": amended(i="123"),
    "id_other_spelling": respelled("i", str.upper),
    "scope_changed": amended(s=str(uuid4())),
    "scope_not_text": amended(s=5),
}


@pytest.mark.parametrize("name", list(BAD_CURSORS))
async def test_a_malformed_or_altered_cursor_is_refused_by_both_lists(
    app_engine: AsyncEngine, tenants: Tenants, name: str
) -> None:
    tenant = tenants[0]
    user = tenant.other_user_id
    session_id = await new_session(app_engine, tenant, user)
    await new_session(app_engine, tenant, user)
    await new_messages(app_engine, tenant, session_id, 3, user)
    async with repository(app_engine, tenant, user) as repo:
        sessions_cursor = (await repo.list_sessions(1)).next_cursor
        messages_cursor = (await repo.list_messages(session_id, 1)).next_cursor
        assert sessions_cursor is not None and messages_cursor is not None
        with pytest.raises(InvalidCursor):
            await repo.list_sessions(1, BAD_CURSORS[name](sessions_cursor))
        with pytest.raises(InvalidCursor):
            await repo.list_messages(session_id, 1, BAD_CURSORS[name](messages_cursor))


async def test_a_cursor_only_works_for_the_list_it_came_from(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    user = tenant.other_user_id
    first = await new_session(app_engine, tenant, user)
    second = await new_session(app_engine, tenant, user)
    await new_messages(app_engine, tenant, first, 3, user)
    await new_messages(app_engine, tenant, second, 3, user)
    async with repository(app_engine, tenant, user) as repo:
        sessions_cursor = (await repo.list_sessions(1)).next_cursor
        first_cursor = (await repo.list_messages(first, 1)).next_cursor
        assert sessions_cursor is not None and first_cursor is not None
        with pytest.raises(InvalidCursor):
            await repo.list_messages(first, 1, sessions_cursor)
        with pytest.raises(InvalidCursor):
            await repo.list_sessions(1, first_cursor)
        with pytest.raises(InvalidCursor):
            await repo.list_messages(second, 1, first_cursor)
        assert len((await repo.list_messages(first, 5, first_cursor)).items) == 2


async def test_a_cursor_from_another_users_list_gives_no_view_of_their_data(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    mine, colleague = tenant.user_id, tenant.other_user_id
    session_id = await new_session(app_engine, tenant, mine)
    await new_session(app_engine, tenant, mine)
    await new_messages(app_engine, tenant, session_id, 3, mine)
    async with repository(app_engine, tenant, mine) as repo:
        sessions_cursor = (await repo.list_sessions(1)).next_cursor
        messages_cursor = (await repo.list_messages(session_id, 1)).next_cursor
    assert sessions_cursor is not None and messages_cursor is not None
    async with repository(app_engine, tenant, colleague) as repo:
        assert (await repo.list_sessions(10, sessions_cursor)).items == ()
        with pytest.raises(SessionNotFound):
            await repo.list_messages(session_id, 10, messages_cursor)


async def test_an_invalid_cursor_is_refused_before_the_database_is_asked(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant = tenants[0]
    async with repository(app_engine, tenant) as repo:
        with pytest.raises(InvalidCursor):
            await repo.list_messages(uuid4(), 5, "garbage")  # not SessionNotFound


# ---------------------------------------------------------------------------------------------
# SQL hygiene (static)


def test_every_statement_in_the_repository_is_a_literal_so_values_travel_as_parameters() -> None:
    path = APP / "db" / "repositories" / "chat.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "text"
    ]
    assert len(calls) >= 9
    for call in calls:
        assert len(call.args) == 1 and not call.keywords
        argument = call.args[0]
        assert isinstance(argument, ast.Constant) and isinstance(argument.value, str), (
            f"chat.py:{call.lineno} builds SQL from a non-literal"
        )
        sql = argument.value
        assert re.search(r"select\s+\*", sql, re.IGNORECASE) is None
        # Defense in depth on top of row-level security: every statement names tenant and user.
        assert ":workspace_id" in sql and ":user_id" in sql, f"chat.py:{call.lineno} is not scoped"
