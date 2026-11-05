"""Helpers for tests that run against the real local PostgreSQL service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

TENANT_TABLES = (
    "users",
    "sessions",
    "messages",
    "tasks",
    "subtasks",
    "approval_requests",
    "compensations",
    "audit_log",
    "mcp_servers",
    "file_artifacts",
    "memory_items",
    "auth_otp_challenges",
    "auth_sessions",
)
ALL_PROTECTED_TABLES = ("workspaces", *TENANT_TABLES)


def sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


@asynccontextmanager
async def scoped(
    engine: AsyncEngine, workspace: UUID | str | None
) -> AsyncIterator[AsyncConnection]:
    """A transaction as the runtime role with the tenant context set, always rolled back."""
    async with engine.connect() as connection:
        await connection.begin()
        try:
            if workspace is not None:
                await connection.execute(
                    text("SELECT set_config('app.workspace_id', :workspace, true)"),
                    {"workspace": str(workspace)},
                )
            yield connection
        finally:
            await connection.rollback()


@dataclass(frozen=True)
class SeededTenant:
    """One workspace with two users and one row in every table."""

    workspace_id: UUID
    user_id: UUID
    other_user_id: UUID
    session_id: UUID
    task_id: UUID
    subtask_id: UUID
    username: str
    email: str
    row_ids: dict[str, set[Any]]


async def seed_tenant(connection: AsyncConnection, label: str) -> SeededTenant:
    """Insert as the administrator (which bypasses row-level security)."""
    workspace, first, second = uuid4(), uuid4(), uuid4()
    session, task, subtask = uuid4(), uuid4(), uuid4()
    ids: dict[str, set[Any]] = {"workspaces": {workspace}, "users": {first, second}}

    async def run(sql: str, **parameters: Any) -> None:
        await connection.execute(text(sql), parameters)

    await run(
        "INSERT INTO workspaces (id, name, slug) VALUES (:id, :name, :slug)",
        id=workspace,
        name=f"workspace {label}",
        slug=f"{label}-{workspace.hex[:12]}",
    )
    names: dict[UUID, tuple[str, str]] = {}
    for user, suffix in ((first, "one"), (second, "two")):
        names[user] = (
            f"{label}_{suffix}_{user.hex[:8]}",
            f"{label}.{suffix}.{user.hex[:8]}@example.com",
        )
        await run(
            "INSERT INTO users (id, workspace_id, username, email, full_name, hashed_password) "
            "VALUES (:id, :ws, :username, :email, 'Seed User', 'seed-hash')",
            id=user,
            ws=workspace,
            username=names[user][0],
            email=names[user][1],
        )
    await run(
        "INSERT INTO sessions (id, workspace_id, user_id) VALUES (:id, :ws, :user)",
        id=session,
        ws=workspace,
        user=first,
    )
    ids["sessions"] = {session}
    message = uuid4()
    await run(
        "INSERT INTO messages (id, workspace_id, session_id, sender, content) "
        "VALUES (:id, :ws, :session, 'user', 'seed')",
        id=message,
        ws=workspace,
        session=session,
    )
    ids["messages"] = {message}
    await run(
        "INSERT INTO tasks (id, workspace_id, session_id, user_id, idempotency_key, title, "
        "instruction, dag_plan) VALUES (:id, :ws, :session, :user, :key, 't', 'i', '{}')",
        id=task,
        ws=workspace,
        session=session,
        user=first,
        key=uuid4().hex,
    )
    ids["tasks"] = {task}
    await run(
        "INSERT INTO subtasks (id, workspace_id, task_id, assigned_agent, description) "
        "VALUES (:id, :ws, :task, 'agent', 'seed')",
        id=subtask,
        ws=workspace,
        task=task,
    )
    ids["subtasks"] = {subtask}
    approval, compensation, server, artifact, memory = (uuid4() for _ in range(5))
    await run(
        "INSERT INTO approval_requests (id, workspace_id, task_id, subtask_id, user_id, tool_name, "
        "permission_level, action_description, payload_hash, payload_diff, expires_at) "
        "VALUES (:id, :ws, :task, :subtask, :user, 'tool', 'RESTRICTED', 'seed', repeat('a', 64), "
        "'{}', now() + interval '5 minutes')",
        id=approval,
        ws=workspace,
        task=task,
        subtask=subtask,
        user=first,
    )
    ids["approval_requests"] = {approval}
    await run(
        "INSERT INTO compensations (id, workspace_id, task_id, forward_tool, forward_ref, "
        "permission_level) VALUES (:id, :ws, :task, 'tool', '{}', 'READ')",
        id=compensation,
        ws=workspace,
        task=task,
    )
    ids["compensations"] = {compensation}
    audit = (
        await connection.execute(
            text(
                "INSERT INTO audit_log (workspace_id, user_ref, status, row_hash) "
                "VALUES (:ws, 'seed', 'success', '\\x00') RETURNING id"
            ),
            {"ws": workspace},
        )
    ).scalar_one()
    ids["audit_log"] = {audit}
    await run(
        "INSERT INTO mcp_servers (id, workspace_id, user_id, name, transport) "
        "VALUES (:id, :ws, :user, 'seed', 'stdio')",
        id=server,
        ws=workspace,
        user=first,
    )
    ids["mcp_servers"] = {server}
    await run(
        "INSERT INTO file_artifacts (id, workspace_id, task_id, user_id, file_name, file_type, "
        "storage_uri, size_bytes, sha256) VALUES (:id, :ws, :task, :user, 'f.md', 'md', "
        "'s3://seed', "
        "1, repeat('a', 64))",
        id=artifact,
        ws=workspace,
        task=task,
        user=first,
    )
    ids["file_artifacts"] = {artifact}
    await run(
        "INSERT INTO memory_items (id, workspace_id, user_id, category, content, embedding_model) "
        "VALUES (:id, :ws, :user, 'preference', 'seed', 'model')",
        id=memory,
        ws=workspace,
        user=first,
    )
    ids["memory_items"] = {memory}
    challenge, auth_session = uuid4(), uuid4()
    await run(
        "INSERT INTO auth_otp_challenges "
        "(id, workspace_id, user_id, purpose, code_hmac, expires_at) "
        "VALUES (:id, :ws, :user, 'password_reset', '\\x00', now() + interval '10 minutes')",
        id=challenge,
        ws=workspace,
        user=first,
    )
    ids["auth_otp_challenges"] = {challenge}
    await run(
        "INSERT INTO auth_sessions (id, workspace_id, user_id, refresh_jti, refresh_expires_at, "
        "absolute_expires_at) VALUES (:id, :ws, :user, gen_random_uuid(), "
        "now() + interval '1 day', now() + interval '2 days')",
        id=auth_session,
        ws=workspace,
        user=first,
    )
    ids["auth_sessions"] = {auth_session}
    return SeededTenant(
        workspace, first, second, session, task, subtask, *names[first], row_ids=ids
    )


def insert_statement(table: str, tenant: SeededTenant) -> tuple[str, dict[str, Any]]:
    """A valid INSERT of one new row of ``table`` that belongs to ``tenant``."""
    unique = uuid4().hex[:10]
    base: dict[str, Any] = {
        "ws": tenant.workspace_id,
        "user": tenant.user_id,
        "other_user": tenant.other_user_id,
        "session": tenant.session_id,
        "task": tenant.task_id,
        "subtask": tenant.subtask_id,
        "unique": unique,
    }
    statements = {
        "users": (
            "INSERT INTO users (workspace_id, username, email, full_name, hashed_password) "
            "VALUES (:ws, 'n_' || :unique, :unique || '@example.com', 'n', 'h')"
        ),
        "sessions": "INSERT INTO sessions (workspace_id, user_id) VALUES (:ws, :user)",
        "messages": (
            "INSERT INTO messages (workspace_id, session_id, sender, content) "
            "VALUES (:ws, :session, 'user', 'x')"
        ),
        "tasks": (
            "INSERT INTO tasks (workspace_id, session_id, user_id, idempotency_key, title, "
            "instruction, dag_plan) VALUES (:ws, :session, :user, :unique, 't', 'i', '{}')"
        ),
        "subtasks": (
            "INSERT INTO subtasks (workspace_id, task_id, assigned_agent, description) "
            "VALUES (:ws, :task, 'a', 'd')"
        ),
        "approval_requests": (
            "INSERT INTO approval_requests (workspace_id, task_id, subtask_id, user_id, tool_name, "
            "permission_level, action_description, payload_hash, payload_diff, expires_at) "
            "VALUES (:ws, :task, :subtask, :user, 't', 'RESTRICTED', 'a', repeat('b', 64), "
            "'{}', now())"
        ),
        "compensations": (
            "INSERT INTO compensations (workspace_id, task_id, forward_tool, forward_ref, "
            "permission_level) VALUES (:ws, :task, 't', '{}', 'READ')"
        ),
        "audit_log": (
            "INSERT INTO audit_log (workspace_id, user_ref, status, row_hash) "
            "VALUES (:ws, 'r', 's', '\\x00')"
        ),
        "mcp_servers": (
            "INSERT INTO mcp_servers (workspace_id, user_id, name, transport) "
            "VALUES (:ws, :user, 'n', 'stdio')"
        ),
        "file_artifacts": (
            "INSERT INTO file_artifacts (workspace_id, task_id, user_id, file_name, file_type, "
            "storage_uri, size_bytes, sha256) VALUES (:ws, :task, :user, 'f', 'md', 's', 1, "
            "repeat('b', 64))"
        ),
        "memory_items": (
            "INSERT INTO memory_items (workspace_id, user_id, category, content, embedding_model) "
            "VALUES (:ws, :user, 'preference', 'c', 'm')"
        ),
        "auth_otp_challenges": (
            "INSERT INTO auth_otp_challenges "
            "(workspace_id, user_id, purpose, code_hmac, expires_at) "
            "VALUES (:ws, :other_user, 'password_reset', '\\x00', now())"
        ),
        "auth_sessions": (
            "INSERT INTO auth_sessions (workspace_id, user_id, refresh_jti, refresh_expires_at, "
            "absolute_expires_at) VALUES (:ws, :user, gen_random_uuid(), now(), now())"
        ),
    }
    return statements[table], base


# A no-op assignment per table, using only columns the runtime role may update.
UPDATE_ASSIGNMENT = {
    "users": "email_verified_at = email_verified_at",
    "sessions": "title = title",
    "messages": "content = content",
    "tasks": "title = title",
    "subtasks": "description = description",
    "approval_requests": "action_description = action_description",
    "compensations": "forward_tool = forward_tool",
    "mcp_servers": "name = name",
    "file_artifacts": "file_name = file_name",
    "memory_items": "content = content",
    "auth_otp_challenges": "attempts = attempts",
    "auth_sessions": "last_used_at = last_used_at",
}
DELETABLE_TABLES = (
    "sessions",
    "messages",
    "tasks",
    "subtasks",
    "approval_requests",
    "compensations",
    "mcp_servers",
    "file_artifacts",
    "memory_items",
)
