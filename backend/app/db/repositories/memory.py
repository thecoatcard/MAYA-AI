"""Persistence and hybrid retrieval repository for Memory Items.

Tenant-isolated repository managing memory storage, pgvector cosine distance,
tsvector full-text search, Defect #16 threshold gating, and time decay ranking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession


@dataclass(frozen=True)
class MemoryItemRecord:
    id: UUID
    workspace_id: UUID
    user_id: UUID
    category: str
    content: str
    metadata: dict[str, Any]
    embedding: list[float] | None
    embedding_model: str
    permanent: bool
    access_count: int
    last_accessed_at: datetime
    expires_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class MemorySearchResult:
    id: UUID
    workspace_id: UUID
    user_id: UUID
    category: str
    content: str
    metadata: dict[str, Any]
    raw_similarity: float
    effective_score: float
    created_at: datetime
    last_accessed_at: datetime
    permanent: bool


def _format_vector(vec: list[float] | None) -> str | None:
    if vec is None:
        return None
    return "[" + ",".join(str(float(x)) for x in vec) + "]"


def _parse_embedding(val: Any) -> list[float] | None:
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        return [float(x) for x in val]
    if isinstance(val, str):
        cleaned = val.strip("[] \t\r\n")
        if not cleaned:
            return []
        return [float(x) for x in cleaned.split(",")]
    return [float(x) for x in val]


def _parse_metadata(val: Any) -> dict[str, Any]:
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


_INSERT_MEMORY_ITEM = text(
    """
    INSERT INTO memory_items (
        id, workspace_id, user_id, category, content, metadata,
        embedding, embedding_model, permanent, access_count,
        last_accessed_at, expires_at, created_at
    ) VALUES (
        :id, :workspace_id, :user_id, :category, :content, CAST(:metadata AS jsonb),
        CAST(:embedding AS text)::vector,
        :embedding_model, :permanent, 1,
        :now, :expires_at, :now
    )
    RETURNING id, workspace_id, user_id, category, content, metadata,
              embedding, embedding_model, permanent, access_count,
              last_accessed_at, expires_at, created_at
    """
)

_GET_MEMORY_ITEM = text(
    """
    SELECT id, workspace_id, user_id, category, content, metadata,
           embedding, embedding_model, permanent, access_count,
           last_accessed_at, expires_at, created_at
    FROM memory_items
    WHERE workspace_id = :workspace_id AND user_id = :user_id AND id = :id
    """
)

_LIST_MEMORY_ITEMS = text(
    """
    SELECT id, workspace_id, user_id, category, content, metadata,
           embedding, embedding_model, permanent, access_count,
           last_accessed_at, expires_at, created_at
    FROM memory_items
    WHERE workspace_id = :workspace_id AND user_id = :user_id
      AND (CAST(:category AS text) IS NULL OR category = CAST(:category AS text))
      AND (expires_at IS NULL OR expires_at > now())
    ORDER BY created_at DESC, id DESC
    LIMIT :limit OFFSET :offset
    """
)

_DELETE_MEMORY_ITEM = text(
    """
    DELETE FROM memory_items
    WHERE workspace_id = :workspace_id AND user_id = :user_id AND id = :id
    RETURNING id
    """
)

_HAS_ITEMS = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM memory_items
        WHERE workspace_id = :workspace_id AND user_id = :user_id
          AND embedding IS NOT NULL
          AND (expires_at IS NULL OR expires_at > now())
    )
    """
)

_UPDATE_CONTENT = text(
    """
    UPDATE memory_items
    SET content = :content,
        metadata = CAST(:metadata AS jsonb),
        embedding = CAST(:embedding AS text)::vector,
        embedding_model = :embedding_model,
        access_count = access_count + 1,
        last_accessed_at = now()
    WHERE workspace_id = :workspace_id AND user_id = :user_id AND id = :id
    RETURNING id, workspace_id, user_id, category, content, metadata,
              embedding, embedding_model, permanent, access_count,
              last_accessed_at, expires_at, created_at
    """
)

_UPDATE_ACCESS = text(
    """
    UPDATE memory_items
    SET access_count = access_count + 1,
        last_accessed_at = now()
    WHERE workspace_id = :workspace_id
      AND user_id = :user_id
      AND id = ANY(:ids)
    """
)

_MARK_EXPIRED = text(
    """
    UPDATE memory_items
    SET expires_at = now()
    WHERE workspace_id = :workspace_id
      AND permanent = FALSE
      AND expires_at IS NULL
      AND last_accessed_at < (now() - (CAST(:threshold_days AS integer) * INTERVAL '1 day'))
    """
)

_PURGE_USER_MEMORIES = text(
    """
    DELETE FROM memory_items
    WHERE workspace_id = :workspace_id
      AND user_id = :user_id
      AND (CAST(:category AS text) IS NULL OR category = CAST(:category AS text))
    """
)

_SEARCH_HYBRID = text(
    """
    WITH ranked AS (
        SELECT
            id,
            workspace_id,
            user_id,
            category,
            content,
            metadata,
            permanent,
            access_count,
            last_accessed_at,
            created_at,
            (1.0 - (embedding <=> CAST(:query_embedding AS text)::vector)) AS raw_sim,
            LEAST(1.0, ts_rank_cd(tsv, plainto_tsquery('english', :query_text))) AS text_score
        FROM memory_items
        WHERE workspace_id = :workspace_id
          AND user_id = :user_id
          AND embedding IS NOT NULL
          AND (expires_at IS NULL OR expires_at > now())
          AND (:has_categories = FALSE OR category = ANY(:categories))
    )
    SELECT
        id,
        workspace_id,
        user_id,
        category,
        content,
        metadata,
        permanent,
        access_count,
        last_accessed_at,
        created_at,
        raw_sim,
        CASE
            WHEN permanent THEN (0.7 * raw_sim + 0.3 * text_score)
            ELSE (0.7 * raw_sim + 0.3 * text_score) * EXP(
                -0.005 * GREATEST(0.0, EXTRACT(EPOCH FROM (now() - last_accessed_at)) / 86400.0)
            )
        END AS effective_score
    FROM ranked
    WHERE raw_sim >= :raw_threshold
    ORDER BY effective_score DESC, id DESC
    LIMIT :limit
    """
)


class MemoryRepository:
    """Repository handling long-term and profile memory items in PostgreSQL."""

    def __init__(self, session_or_conn: AsyncSession | AsyncConnection) -> None:
        self._executor = session_or_conn

    async def create_item(
        self,
        workspace_id: UUID,
        user_id: UUID,
        category: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
        embedding_model: str = "gemini-embedding-2",
        permanent: bool = False,
        expires_at: datetime | None = None,
        id: UUID | None = None,
    ) -> MemoryItemRecord:
        """Create a new memory item row."""
        item_id = id or uuid4()
        now = datetime.now(UTC)
        meta = metadata or {}
        vec_str = _format_vector(embedding)

        res = await self._executor.execute(
            _INSERT_MEMORY_ITEM,
            {
                "id": item_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "category": category,
                "content": content,
                "metadata": json.dumps(meta),
                "embedding": vec_str,
                "embedding_model": embedding_model,
                "permanent": permanent,
                "expires_at": expires_at,
                "now": now,
            },
        )
        row = res.mappings().one()
        return self._to_record(row)

    async def get_item_by_id(
        self, workspace_id: UUID, user_id: UUID, item_id: UUID
    ) -> MemoryItemRecord | None:
        """Fetch a single memory item by ID scoped to workspace and user."""
        res = await self._executor.execute(
            _GET_MEMORY_ITEM,
            {"workspace_id": workspace_id, "user_id": user_id, "id": item_id},
        )
        row = res.mappings().one_or_none()
        if not row:
            return None
        return self._to_record(row)

    async def list_items_for_user(
        self,
        workspace_id: UUID,
        user_id: UUID,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryItemRecord]:
        """List active memory items for a user with optional category filtering."""
        res = await self._executor.execute(
            _LIST_MEMORY_ITEMS,
            {
                "workspace_id": workspace_id,
                "user_id": user_id,
                "category": category,
                "limit": limit,
                "offset": offset,
            },
        )
        return [self._to_record(row) for row in res.mappings().all()]

    async def delete_item(self, workspace_id: UUID, user_id: UUID, item_id: UUID) -> bool:
        """Delete a single memory item."""
        res = await self._executor.execute(
            _DELETE_MEMORY_ITEM,
            {"workspace_id": workspace_id, "user_id": user_id, "id": item_id},
        )
        row = res.mappings().one_or_none()
        return row is not None

    async def has_items(self, workspace_id: UUID, user_id: UUID) -> bool:
        """True when the user has at least one searchable (embedded, unexpired) memory.

        A single indexed existence probe, so a caller can skip the embedding call for an empty
        memory.
        """
        res = await self._executor.execute(
            _HAS_ITEMS, {"workspace_id": workspace_id, "user_id": user_id}
        )
        return bool(res.scalar())

    async def update_item_content(
        self,
        workspace_id: UUID,
        user_id: UUID,
        item_id: UUID,
        content: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
        embedding_model: str = "gemini-embedding-2",
    ) -> MemoryItemRecord | None:
        """Replace the text, vector and metadata of one memory; ``None`` when it is not found."""
        res = await self._executor.execute(
            _UPDATE_CONTENT,
            {
                "workspace_id": workspace_id,
                "user_id": user_id,
                "id": item_id,
                "content": content,
                "metadata": json.dumps(metadata or {}),
                "embedding": _format_vector(embedding),
                "embedding_model": embedding_model,
            },
        )
        row = res.mappings().one_or_none()
        return None if row is None else self._to_record(row)

    async def update_access(self, workspace_id: UUID, user_id: UUID, ids: list[UUID]) -> int:
        """Increment access count and touch last_accessed_at for listed IDs."""
        if not ids:
            return 0
        res = await self._executor.execute(
            _UPDATE_ACCESS,
            {"workspace_id": workspace_id, "user_id": user_id, "ids": ids},
        )
        return getattr(res, "rowcount", len(ids))

    async def mark_expired(self, workspace_id: UUID, threshold_days: int = 180) -> int:
        """Mark non-permanent memories untouched for threshold_days as expired."""
        res = await self._executor.execute(
            _MARK_EXPIRED,
            {"workspace_id": workspace_id, "threshold_days": threshold_days},
        )
        return getattr(res, "rowcount", 0)

    async def purge_user_memories(
        self, workspace_id: UUID, user_id: UUID, category: str | None = None
    ) -> int:
        """Erase memory records for a user under GDPR Article 17."""
        res = await self._executor.execute(
            _PURGE_USER_MEMORIES,
            {"workspace_id": workspace_id, "user_id": user_id, "category": category},
        )
        return getattr(res, "rowcount", 0)

    async def search_hybrid(
        self,
        workspace_id: UUID,
        user_id: UUID,
        query_embedding: list[float],
        query_text: str,
        categories: list[str] | None = None,
        limit: int = 5,
        raw_threshold: float = 0.78,
    ) -> list[MemorySearchResult]:
        """Perform hybrid search enforcing raw similarity gate (Defect #16) and decay ranking."""
        vec_str = _format_vector(query_embedding)
        cats = categories or []
        has_cats = len(cats) > 0

        res = await self._executor.execute(
            _SEARCH_HYBRID,
            {
                "workspace_id": workspace_id,
                "user_id": user_id,
                "query_embedding": vec_str,
                "query_text": query_text,
                "categories": cats,
                "has_categories": has_cats,
                "raw_threshold": raw_threshold,
                "limit": limit,
            },
        )
        rows = res.mappings().all()
        results: list[MemorySearchResult] = []
        result_ids: list[UUID] = []

        for row in rows:
            result_ids.append(row["id"])
            results.append(
                MemorySearchResult(
                    id=row["id"],
                    workspace_id=row["workspace_id"],
                    user_id=row["user_id"],
                    category=row["category"],
                    content=row["content"],
                    metadata=_parse_metadata(row["metadata"]),
                    raw_similarity=float(row["raw_sim"]),
                    effective_score=float(row["effective_score"]),
                    created_at=row["created_at"],
                    last_accessed_at=row["last_accessed_at"],
                    permanent=bool(row["permanent"]),
                )
            )

        if result_ids:
            await self.update_access(workspace_id, user_id, result_ids)

        return results

    @staticmethod
    def _to_record(row: Any) -> MemoryItemRecord:
        return MemoryItemRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            category=row["category"],
            content=row["content"],
            metadata=_parse_metadata(row["metadata"]),
            embedding=_parse_embedding(row["embedding"]),
            embedding_model=row["embedding_model"],
            permanent=bool(row["permanent"]),
            access_count=int(row["access_count"]),
            last_accessed_at=row["last_accessed_at"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )
