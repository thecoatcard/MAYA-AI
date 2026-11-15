"""LangGraph checkpointer configuration for PostgreSQL state persistence (Decision D6)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from uuid import UUID

from langgraph.checkpoint.base import CheckpointTuple
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

if TYPE_CHECKING:
    from app.core.config import Settings


def get_postgres_connection_string(settings: Settings) -> str:
    """Extract standard PostgreSQL connection URL compatible with Psycopg 3 from app settings."""
    url = settings.database_url.get_secret_value()
    # Replace asyncpg dialect with standard postgresql scheme for psycopg3
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url.removeprefix("postgresql+asyncpg://")
    return url


def ensure_compatible_event_loop() -> None:
    """Set and verify Windows' selector policy before any event loop starts."""
    if sys.platform != "win32":
        return

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "Windows SelectorEventLoopPolicy must be configured before the event loop starts"
        )

    policy_type = asyncio.WindowsSelectorEventLoopPolicy
    if not isinstance(asyncio.get_event_loop_policy(), policy_type):
        asyncio.set_event_loop_policy(policy_type())
    if not isinstance(asyncio.get_event_loop_policy(), policy_type):
        raise RuntimeError("Windows SelectorEventLoopPolicy could not be installed")


def _validate_compatible_running_loop() -> None:
    """Fail if the current Windows loop was created without selector compatibility."""
    if sys.platform == "win32" and not isinstance(
        asyncio.get_running_loop(), asyncio.SelectorEventLoop
    ):
        raise RuntimeError(
            "Psycopg async checkpointer requires a Windows SelectorEventLoop; "
            "configure the event loop policy before starting the server"
        )


@asynccontextmanager
async def create_postgres_checkpointer(
    settings: Settings,
    *,
    workspace_id: UUID,
    user_id: UUID,
) -> AsyncIterator[AsyncPostgresSaver]:
    """Open a checkpointer bound to one authenticated workspace and user.

    The dedicated connection keeps its server-set owner context for every saver operation.
    Schema creation is managed by Alembic, never by the runtime checkpointer.
    """
    if not isinstance(workspace_id, UUID) or not isinstance(user_id, UUID):
        raise TypeError("workspace_id and user_id must be authenticated UUID identities")

    _validate_compatible_running_loop()
    conn_string = get_postgres_connection_string(settings)

    async with await AsyncConnection.connect(
        conn_string,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    ) as connection:
        await connection.execute(
            """
            SELECT set_config('app.workspace_id', %s, false),
                   set_config('app.user_id', %s, false)
            """,
            (str(workspace_id), str(user_id)),
        )
        yield AsyncPostgresSaver(conn=connection)


def create_memory_checkpointer() -> MemorySaver:
    """Create in-memory checkpointer for testing and lightweight orchestration."""
    return MemorySaver()


async def get_latest_checkpoint(
    checkpointer: Any,
    thread_id: str,
) -> CheckpointTuple | None:
    """Retrieve the latest checkpoint tuple for a given thread_id."""
    config = {"configurable": {"thread_id": thread_id}}
    if hasattr(checkpointer, "aget_tuple"):
        result: CheckpointTuple | None = await checkpointer.aget_tuple(config)
        return result
    if hasattr(checkpointer, "get_tuple"):
        result = checkpointer.get_tuple(config)
        return result
    return None
