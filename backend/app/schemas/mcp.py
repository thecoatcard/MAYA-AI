"""Pydantic schemas for MCP server administration and health monitoring (Phase 08)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class McpServerCreate(BaseModel):
    """Schema for registering a new MCP server in a workspace."""

    name: str = Field(..., min_length=1, max_length=128)
    transport: str = Field(..., pattern="^(stdio|http|streamable_http)$")
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    pinned_version: str | None = None
    schema_hash: str | None = None
    tools_manifest: list[dict[str, Any]] | None = None


class McpServerOut(BaseModel):
    """Schema for returning MCP server metadata."""

    id: UUID
    workspace_id: UUID
    user_id: UUID
    name: str
    transport: str
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    pinned_version: str | None = None
    schema_hash: str | None = None
    is_active: bool
    health_status: str
    tool_count: int
    tools_manifest: list[dict[str, Any]] | None = None
    last_ping_at: datetime | None = None
    consecutive_failures: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class McpServerHealthOut(BaseModel):
    """Schema for server health and heartbeat metrics."""

    server_id: UUID
    name: str
    health_status: str
    last_ping_at: datetime | None = None
    consecutive_failures: int = 0
    tool_count: int = 0
