"""Repository for managing MCP server registrations and supervisor health tracking."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import McpServer

logger = logging.getLogger(__name__)


class McpServerRepository:
    """PostgreSQL repository for MCP servers respecting Row-Level Security."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_server(
        self,
        workspace_id: UUID,
        user_id: UUID,
        name: str,
        transport: str,
        command: str | None = None,
        args: list[Any] | None = None,
        url: str | None = None,
        pinned_version: str | None = None,
        schema_hash: str | None = None,
        env_vars_encrypted: bytes | None = None,
        key_id: str | None = None,
        tools_manifest: list[Any] | None = None,
    ) -> McpServer:
        """Register a new MCP server in the tenant workspace."""
        server = McpServer(
            workspace_id=workspace_id,
            user_id=user_id,
            name=name,
            transport=transport,
            command=command,
            args=args or [],
            url=url,
            pinned_version=pinned_version,
            schema_hash=schema_hash,
            env_vars_encrypted=env_vars_encrypted,
            key_id=key_id,
            is_active=True,
            health_status="healthy",
            tool_count=len(tools_manifest) if tools_manifest else 0,
            tools_manifest=tools_manifest or [],
        )
        self.session.add(server)
        await self.session.flush()
        return server

    async def get_server_by_id(
        self,
        workspace_id: UUID,
        server_id: UUID,
    ) -> McpServer | None:
        """Retrieve a specific MCP server within the tenant boundary."""
        stmt = (
            select(McpServer)
            .where(
                McpServer.id == server_id,
                McpServer.workspace_id == workspace_id,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_servers_for_workspace(
        self,
        workspace_id: UUID,
        is_active: bool | None = None,
    ) -> list[McpServer]:
        """List all MCP servers registered in the tenant workspace."""
        stmt = select(McpServer).where(McpServer.workspace_id == workspace_id)
        if is_active is not None:
            stmt = stmt.where(McpServer.is_active == is_active)
        stmt = stmt.order_by(McpServer.created_at.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_health_status(
        self,
        workspace_id: UUID,
        server_id: UUID,
        health_status: str,
        consecutive_failures: int = 0,
        last_ping_at: datetime | None = None,
        tool_count: int | None = None,
        tools_manifest: list[Any] | None = None,
    ) -> bool:
        """Update supervisor health metrics for a registered server."""
        values: dict[str, Any] = {
            "health_status": health_status,
            "consecutive_failures": consecutive_failures,
            "last_ping_at": last_ping_at or datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        if tool_count is not None:
            values["tool_count"] = tool_count
        if tools_manifest is not None:
            values["tools_manifest"] = tools_manifest

        stmt = (
            update(McpServer)
            .where(
                McpServer.id == server_id,
                McpServer.workspace_id == workspace_id,
            )
            .values(**values)
        )
        res = await self.session.execute(stmt)
        return bool(cast(CursorResult[Any], res).rowcount > 0)

    async def update_schema_hash(
        self,
        workspace_id: UUID,
        server_id: UUID,
        schema_hash: str,
        tool_count: int,
        tools_manifest: list[Any],
    ) -> bool:
        """Update approved schema hash and manifest."""
        stmt = (
            update(McpServer)
            .where(
                McpServer.id == server_id,
                McpServer.workspace_id == workspace_id,
            )
            .values(
                schema_hash=schema_hash,
                tool_count=tool_count,
                tools_manifest=tools_manifest,
                updated_at=datetime.now(UTC),
            )
        )
        res = await self.session.execute(stmt)
        return bool(cast(CursorResult[Any], res).rowcount > 0)

    async def delete_server(
        self,
        workspace_id: UUID,
        server_id: UUID,
    ) -> bool:
        """Delete an MCP server registration within the tenant boundary."""
        stmt = delete(McpServer).where(
            McpServer.id == server_id,
            McpServer.workspace_id == workspace_id,
        )
        res = await self.session.execute(stmt)
        return bool(cast(CursorResult[Any], res).rowcount > 0)
