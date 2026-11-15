"""Pydantic schemas for Memory and Knowledge System."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

MemoryCategory = Literal["preference", "project", "episodic", "knowledge"]


class MemoryItemCreate(BaseModel):
    """Payload to ingest a new memory or knowledge document."""

    category: MemoryCategory
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    permanent: bool = False


class MemoryItemResponse(BaseModel):
    """Serialized representation of a persisted memory item."""

    id: UUID
    workspace_id: UUID
    user_id: UUID
    category: str
    content: str
    metadata: dict[str, Any]
    permanent: bool
    access_count: int
    last_accessed_at: datetime
    expires_at: datetime | None
    created_at: datetime


class MemorySearchRequest(BaseModel):
    """Payload to query memory items via hybrid dense/sparse search."""

    query: str
    categories: list[str] | None = None
    limit: int = Field(default=5, ge=1, le=50)
    raw_threshold: float = Field(default=0.78, ge=0.0, le=1.0)


class MemorySearchResultItem(BaseModel):
    """Individual retrieved memory search result with citation."""

    id: UUID
    category: str
    content: str
    metadata: dict[str, Any]
    raw_similarity: float
    effective_score: float
    source_id: str | None
    created_at: datetime


class MemorySearchResponse(BaseModel):
    """Structured response containing ranked citations and score breakdown."""

    query: str
    results: list[MemorySearchResultItem]
    total_results: int


class MemoryPurgeRequest(BaseModel):
    """GDPR Article 17 memory purge request."""

    scope: str = Field(default="all")


class MemoryPurgeResponse(BaseModel):
    """GDPR Article 17 memory purge acknowledgment."""

    status: str = "purged"
    scope: str
    records_deleted: int
