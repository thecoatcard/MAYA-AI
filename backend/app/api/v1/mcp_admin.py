"""MCP Server Administration and Health Monitoring API endpoints (Phase 08)."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.db.repositories.mcp_servers import McpServerRepository
from app.mcp.normalizer import compute_server_schema_hash
from app.schemas.mcp import McpServerCreate, McpServerHealthOut, McpServerOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])

_TRANSPORT_ALIASES = {"http": "streamable_http"}


def normalize_transport(value: str) -> str:
    """Map the UI alias to the stored value; unknown values are a clean 422."""
    transport = _TRANSPORT_ALIASES.get(value, value)
    if transport not in ("stdio", "streamable_http"):
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "fields": ["transport"]},
        )
    return transport


@router.get("/servers", response_model=list[McpServerOut])
async def list_mcp_servers(
    principal: Annotated[Principal, Depends(get_principal)],
    tenant_db: Annotated[TenantDatabase, Depends(get_tenant_database)],
    is_active: bool | None = Query(default=True),
) -> list[McpServerOut]:
    """List all registered MCP servers for the current tenant workspace."""
    async with tenant_db.transaction() as conn:
        session = AsyncSession(conn)
        repo = McpServerRepository(session)
        servers = await repo.list_servers_for_workspace(principal.workspace_id, is_active=is_active)
    return [McpServerOut.model_validate(s) for s in servers]


@router.post("/servers", response_model=McpServerOut, status_code=status.HTTP_201_CREATED)
async def register_mcp_server(
    payload: McpServerCreate,
    principal: Annotated[Principal, Depends(get_principal)],
    tenant_db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> McpServerOut:
    """Register a new MCP server with schema hash poisoning defense."""
    # Compute schema hash if not explicitly provided
    schema_hash = payload.schema_hash
    if not schema_hash and payload.tools_manifest:
        schema_hash = compute_server_schema_hash(payload.tools_manifest)

    async with tenant_db.transaction() as conn:
        session = AsyncSession(conn)
        repo = McpServerRepository(session)
        server = await repo.create_server(
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            name=payload.name,
            transport=normalize_transport(payload.transport),
            command=payload.command,
            args=payload.args,
            url=payload.url,
            pinned_version=payload.pinned_version,
            schema_hash=schema_hash,
            tools_manifest=payload.tools_manifest,
        )
    return McpServerOut.model_validate(server)


@router.get("/servers/{server_id}/health", response_model=McpServerHealthOut)
async def get_mcp_server_health(
    server_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    tenant_db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> McpServerHealthOut:
    """Inspect health status, heartbeat metrics, and failure counts for an MCP server."""
    async with tenant_db.transaction() as conn:
        session = AsyncSession(conn)
        repo = McpServerRepository(session)
        server = await repo.get_server_by_id(principal.workspace_id, server_id)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{server_id}' not found.",
        )

    return McpServerHealthOut(
        server_id=server.id,
        name=server.name,
        health_status=server.health_status,
        last_ping_at=server.last_ping_at,
        consecutive_failures=server.consecutive_failures,
        tool_count=server.tool_count,
    )


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    server_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    tenant_db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> None:
    """Delete an MCP server registration from the tenant workspace."""
    async with tenant_db.transaction() as conn:
        session = AsyncSession(conn)
        repo = McpServerRepository(session)
        deleted = await repo.delete_server(principal.workspace_id, server_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{server_id}' not found.",
        )
