"""Integration tests for LangGraph checkpointer persistence (Decision D6)."""

from typing import cast
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import Checkpoint
from pydantic import SecretStr

from app.agents.checkpointer import (
    create_memory_checkpointer,
    create_postgres_checkpointer,
    get_latest_checkpoint,
    get_postgres_connection_string,
)
from app.core.config import Settings
from tests.support.postgres import SeededTenant

pytestmark = pytest.mark.postgres


def test_postgres_connection_string_conversion() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://app_user:secret@localhost:5432/maya"),
        redis_url=SecretStr("redis://localhost:6379/0"),
        jwt_signing_key=SecretStr("0" * 32),
        otp_hmac_key=SecretStr("1" * 32),
    )
    conn_str = get_postgres_connection_string(settings)
    assert conn_str.startswith("postgresql://")
    assert "+asyncpg" not in conn_str
    assert "app_user:secret@localhost:5432/maya" in conn_str


@pytest.mark.asyncio
async def test_memory_checkpointer_storage_and_retrieval() -> None:
    from app.agents.orchestrator import create_orchestrator_graph
    from app.agents.state import AgentExecutionState, SubTask

    checkpointer = create_memory_checkpointer()
    graph = create_orchestrator_graph(checkpointer=checkpointer)

    initial_state = AgentExecutionState(
        session_id="sess_checkpointer",
        user_id="user_checkpointer",
        workspace_id="ws_checkpointer",
        user_instruction="Checkpointer test",
        interaction_modality="text",
        active_plan=[SubTask(id="st_1", assigned_agent="research", description="Research step")],
    )

    config = cast(RunnableConfig, {"configurable": {"thread_id": "thread_integ_1"}})
    await graph.ainvoke(initial_state, config=config)

    tuple_res = await get_latest_checkpoint(checkpointer, "thread_integ_1")
    assert tuple_res is not None
    assert tuple_res.checkpoint["channel_values"]["plan_status"] == "executing"
    assert tuple_res.checkpoint["channel_values"]["session_id"] == "sess_checkpointer"


@pytest.mark.asyncio
async def test_postgres_checkpointer_isolates_workspace_and_user_owners(
    migrated_database: object,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
) -> None:
    """Runs against PostgreSQL when configured; it is not part of offline verification."""
    from tests.conftest import TestDatabase

    db = migrated_database
    assert isinstance(db, TestDatabase)

    pg_url = db.app_url.render_as_string(hide_password=False).replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    settings = Settings(
        database_url=SecretStr(pg_url),
        redis_url=SecretStr("redis://localhost:6379/0"),
        jwt_signing_key=SecretStr("0" * 32),
        otp_hmac_key=SecretStr("1" * 32),
    )

    first, second = fresh_tenants
    thread_id = f"pg_integ_thread_{uuid4()}"
    config = cast(
        RunnableConfig,
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
    )
    checkpoint = {
        "v": 1,
        "ts": "2026-11-06T12:00:00+00:00",
        "id": str(uuid4()),
        "channel_values": {"status": "completed"},
        "channel_versions": {"status": 1},
        "versions_seen": {},
    }

    async with create_postgres_checkpointer(
        settings, workspace_id=first.workspace_id, user_id=first.user_id
    ) as cp:
        saved_config = await cp.aput(config, cast(Checkpoint, checkpoint), {}, {})
        await cp.aput_writes(saved_config, [("status", "completed")], "writer-1")
        loaded = await cp.aget(saved_config)
        assert loaded is not None
        assert loaded["channel_values"]["status"] == "completed"
        assert await cp.aget_tuple(saved_config) is not None

    for workspace_id, user_id in (
        (first.workspace_id, first.other_user_id),
        (second.workspace_id, second.user_id),
    ):
        async with create_postgres_checkpointer(
            settings, workspace_id=workspace_id, user_id=user_id
        ) as cp:
            assert await cp.aget(saved_config) is None
            assert await cp.aget_tuple(saved_config) is None
