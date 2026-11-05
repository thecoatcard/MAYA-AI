"""Composite foreign keys: a row can only reference parents of its own workspace.

Referential checks bypass row-level security, so these constraints are what stops one workspace
from pointing at (or cascading into) another. The tests use the administrator, which row-level
security does not restrict, to show the constraint alone is enough.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.support.postgres import SeededTenant, sqlstate

pytestmark = pytest.mark.postgres

Tenants = tuple[SeededTenant, SeededTenant]

FOREIGN_KEY_VIOLATION = "23503"

# (table, INSERT that references a parent of the OTHER tenant while naming this tenant's workspace)
CROSS_REFERENCES = {
    "sessions": (
        "INSERT INTO sessions (workspace_id, user_id) VALUES (:ws, :other_user)",
        "user",
    ),
    "messages": (
        "INSERT INTO messages (workspace_id, session_id, sender, content) "
        "VALUES (:ws, :other_session, 'user', 'x')",
        "session",
    ),
    "tasks (session)": (
        "INSERT INTO tasks (workspace_id, session_id, user_id, idempotency_key, title, "
        "instruction, dag_plan) VALUES (:ws, :other_session, :user, :key, 't', 'i', '{}')",
        "session",
    ),
    "tasks (user)": (
        "INSERT INTO tasks (workspace_id, session_id, user_id, idempotency_key, title, "
        "instruction, dag_plan) VALUES (:ws, :session, :other_user, :key, 't', 'i', '{}')",
        "user",
    ),
    "subtasks": (
        "INSERT INTO subtasks (workspace_id, task_id, assigned_agent, description) "
        "VALUES (:ws, :other_task, 'a', 'd')",
        "task",
    ),
    "approval_requests (subtask)": (
        "INSERT INTO approval_requests (workspace_id, task_id, subtask_id, user_id, tool_name, "
        "permission_level, action_description, payload_hash, payload_diff, expires_at) "
        "VALUES (:ws, :task, :other_subtask, :user, 't', 'RESTRICTED', 'a', repeat('c', 64), "
        "'{}', now())",
        "subtask",
    ),
    "compensations": (
        "INSERT INTO compensations (workspace_id, task_id, forward_tool, forward_ref, "
        "permission_level) VALUES (:ws, :other_task, 't', '{}', 'READ')",
        "task",
    ),
    "mcp_servers": (
        "INSERT INTO mcp_servers (workspace_id, user_id, name, transport) "
        "VALUES (:ws, :other_user, 'n', 'stdio')",
        "user",
    ),
    "file_artifacts": (
        "INSERT INTO file_artifacts (workspace_id, task_id, user_id, file_name, file_type, "
        "storage_uri, size_bytes, sha256) VALUES (:ws, :other_task, :user, 'f', 'md', 's', 1, "
        "repeat('c', 64))",
        "task",
    ),
    "memory_items": (
        "INSERT INTO memory_items (workspace_id, user_id, category, content, embedding_model) "
        "VALUES (:ws, :other_user, 'preference', 'c', 'm')",
        "user",
    ),
    "auth_otp_challenges": (
        "INSERT INTO auth_otp_challenges (workspace_id, user_id, purpose, code_hmac, expires_at) "
        "VALUES (:ws, :other_user, 'email_verification', '\\x00', now())",
        "user",
    ),
    "auth_sessions": (
        "INSERT INTO auth_sessions (workspace_id, user_id, refresh_jti, refresh_expires_at, "
        "absolute_expires_at) VALUES (:ws, :other_user, gen_random_uuid(), now(), now())",
        "user",
    ),
}


@pytest.fixture
def tenants(fresh_tenants: Tenants) -> Tenants:
    return fresh_tenants


@pytest.mark.parametrize("name", CROSS_REFERENCES)
async def test_a_row_cannot_reference_a_parent_in_another_workspace(
    admin_engine: AsyncEngine, tenants: Tenants, name: str
) -> None:
    mine, theirs = tenants
    statement, _ = CROSS_REFERENCES[name]
    parameters = {
        "ws": mine.workspace_id,
        "user": mine.user_id,
        "session": mine.session_id,
        "task": mine.task_id,
        "other_user": theirs.user_id,
        "other_session": theirs.session_id,
        "other_task": theirs.task_id,
        "other_subtask": theirs.subtask_id,
        "key": uuid4().hex,
    }
    async with admin_engine.connect() as connection:
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(text(statement), parameters)
        await connection.rollback()
    assert sqlstate(caught.value) == FOREIGN_KEY_VIOLATION


async def test_the_same_statement_succeeds_when_the_parent_is_in_the_same_workspace(
    admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    mine, _ = tenants
    async with admin_engine.begin() as connection:
        result = await connection.execute(
            text("INSERT INTO sessions (workspace_id, user_id) VALUES (:ws, :user)"),
            {"ws": mine.workspace_id, "user": mine.user_id},
        )
    assert result.rowcount == 1


async def test_deleting_a_workspace_cascades_only_inside_it(
    admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    mine, theirs = tenants
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM workspaces WHERE id = :id"), {"id": mine.workspace_id}
        )
    async with admin_engine.connect() as connection:
        counts = {}
        for table in ("users", "sessions", "tasks", "subtasks", "messages", "memory_items"):
            counts[table] = {
                "mine": (
                    await connection.execute(
                        text(f"SELECT count(*) FROM {table} WHERE workspace_id = :id"),
                        {"id": mine.workspace_id},
                    )
                ).scalar_one(),
                "theirs": (
                    await connection.execute(
                        text(f"SELECT count(*) FROM {table} WHERE workspace_id = :id"),
                        {"id": theirs.workspace_id},
                    )
                ).scalar_one(),
            }
    assert all(entry["mine"] == 0 for entry in counts.values())
    assert counts["users"]["theirs"] == 2
    assert counts["tasks"]["theirs"] == 1


async def test_deleting_a_subtask_only_clears_the_subtask_reference_of_a_compensation(
    admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    """ON DELETE SET NULL must not null the workspace column, which is part of the same key."""
    mine, _ = tenants
    compensation = uuid4()
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO compensations (id, workspace_id, task_id, subtask_id, forward_tool, "
                "forward_ref, permission_level) "
                "VALUES (:id, :ws, :task, :subtask, 't', '{}', 'READ')"
            ),
            {
                "id": compensation,
                "ws": mine.workspace_id,
                "task": mine.task_id,
                "subtask": mine.subtask_id,
            },
        )
        await connection.execute(
            text("DELETE FROM subtasks WHERE id = :id"), {"id": mine.subtask_id}
        )
    async with admin_engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT workspace_id, subtask_id FROM compensations WHERE id = :id"),
                {"id": compensation},
            )
        ).one()
    assert row[0] == mine.workspace_id
    assert row[1] is None
