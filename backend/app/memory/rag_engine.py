"""Hybrid RAG Retrieval Engine with Defect #16 threshold gating and time decay ranking.

Dense pgvector cosine similarity + sparse tsvector full-text search.
Enforces tenant isolation, raw cosine similarity threshold gate (S_sim >= 0.78),
and time decay factor (e^(-0.005 * delta_days)) with permanent item bypass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.db.repositories.memory import MemoryRepository
from app.gateway.exceptions import EmbeddingDimensionMismatchError, GatewayError
from app.gateway.model_gateway import ModelGateway


def qualifies_raw_threshold(raw_sim: float, threshold: float = 0.78) -> bool:
    """Evaluate candidate qualification gate prior to decay scoring (Defect #16)."""
    return raw_sim >= threshold


def calculate_hybrid_score(raw_sim: float, text_score: float) -> float:
    """Combine dense cosine similarity and sparse full-text score.

    Formula: 0.7 * S_sim + 0.3 * min(1.0, max(0.0, S_text)).
    """
    capped_text = min(1.0, max(0.0, text_score))
    return (0.7 * raw_sim) + (0.3 * capped_text)


def calculate_effective_score(hybrid_score: float, delta_days: float, permanent: bool) -> float:
    """Apply exponential time decay ranking to hybrid score.

    Permanent items bypass decay completely.
    Formula: S_eff = S_hybrid * exp(-0.005 * delta_days)
    """
    if permanent:
        return hybrid_score
    clamped_days = max(0.0, delta_days)
    return hybrid_score * math.exp(-0.005 * clamped_days)


@dataclass(frozen=True)
class MemoryCitationResult:
    """Retrieved memory item formatted with citations and scores."""

    id: UUID
    workspace_id: UUID
    user_id: UUID
    category: str
    content: str
    metadata: dict[str, Any]
    raw_similarity: float
    effective_score: float
    source_id: str | None
    created_at: datetime
    last_accessed_at: datetime
    permanent: bool


class HybridRagEngine:
    """Hybrid dense/sparse retrieval engine over PostgreSQL memory items."""

    def __init__(
        self,
        repository: MemoryRepository,
        gateway: ModelGateway | None = None,
    ) -> None:
        self.repository = repository
        self.gateway = gateway

    async def search(
        self,
        workspace_id: UUID,
        user_id: UUID,
        query_text: str,
        categories: list[str] | None = None,
        limit: int = 5,
        raw_threshold: float = 0.78,
    ) -> list[MemoryCitationResult]:
        """Execute hybrid search with Defect #16 threshold qualification and decay ranking."""
        clean_query = query_text.strip()
        if not clean_query:
            return []

        if self.gateway is None:
            raise ValueError("ModelGateway is required for hybrid vector retrieval.")

        # 1. Embed query text strictly to 768 dimensions
        embed_res = await self.gateway.embed_content(
            alias="embed",
            contents=clean_query,
            workspace_id=workspace_id,
        )
        if not embed_res.vectors:
            raise GatewayError("ModelGateway returned empty vector list for query.")

        query_vec = embed_res.vectors[0]
        if len(query_vec) != 768:
            raise EmbeddingDimensionMismatchError(
                f"Query embedding produced {len(query_vec)} dimensions, expected exactly 768."
            )

        # 2. Query repository hybrid search with SQL <=> and ts_rank_cd
        matches = await self.repository.search_hybrid(
            workspace_id=workspace_id,
            user_id=user_id,
            query_embedding=query_vec,
            query_text=clean_query,
            categories=categories,
            limit=limit,
            raw_threshold=raw_threshold,
        )

        # 3. Format results with citations
        results: list[MemoryCitationResult] = []
        for m in matches:
            meta = m.metadata
            source_id = meta.get("source_id") or meta.get("source") or meta.get("file")
            results.append(
                MemoryCitationResult(
                    id=m.id,
                    workspace_id=m.workspace_id,
                    user_id=m.user_id,
                    category=m.category,
                    content=m.content,
                    metadata=meta,
                    raw_similarity=m.raw_similarity,
                    effective_score=m.effective_score,
                    source_id=str(source_id) if source_id is not None else None,
                    created_at=m.created_at,
                    last_accessed_at=m.last_accessed_at,
                    permanent=m.permanent,
                )
            )

        return results
