"""Integration tests for Hybrid RAG Engine retrieval with gateway and repository."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.repositories.memory import MemoryRepository, MemorySearchResult
from app.gateway.schemas import EmbeddingResponse, TokenUsage
from app.memory.rag_engine import HybridRagEngine, MemoryCitationResult


def _make_vector(dim: int = 768) -> list[float]:
    raw = [0.1] * dim
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


@pytest.mark.asyncio
async def test_hybrid_rag_retrieval_flow() -> None:
    ws_id = uuid4()
    user_id = uuid4()
    vec = _make_vector(768)

    mock_gateway = MagicMock()
    mock_gateway.embed_content = AsyncMock(
        return_value=EmbeddingResponse(
            vectors=[vec],
            model_id="gemini-embedding-2",
            usage=TokenUsage(prompt_tokens=5, candidates_tokens=0, total_token_count=5),
            alias_used="embed",
            was_fallback=False,
        )
    )

    mock_repo = MagicMock(spec=MemoryRepository)
    item_id = uuid4()
    mock_repo.search_hybrid = AsyncMock(
        return_value=[
            MemorySearchResult(
                id=item_id,
                workspace_id=ws_id,
                user_id=user_id,
                category="knowledge",
                content="PostgreSQL pgvector cosine ops index configuration.",
                metadata={"file": "db_setup.md", "source_id": "doc-123"},
                raw_similarity=0.88,
                effective_score=0.82,
                created_at=datetime.now(UTC),
                last_accessed_at=datetime.now(UTC),
                permanent=False,
            )
        ]
    )

    engine = HybridRagEngine(repository=mock_repo, gateway=mock_gateway)
    results = await engine.search(
        workspace_id=ws_id,
        user_id=user_id,
        query_text="How to configure pgvector cosine ops?",
        categories=["knowledge"],
        limit=5,
        raw_threshold=0.78,
    )

    assert len(results) == 1
    res = results[0]
    assert isinstance(res, MemoryCitationResult)
    assert res.id == item_id
    assert res.category == "knowledge"
    assert res.raw_similarity == 0.88
    assert res.effective_score == 0.82
    assert "pgvector" in res.content
    assert res.source_id == "doc-123"

    mock_gateway.embed_content.assert_awaited_once_with(
        alias="embed",
        contents="How to configure pgvector cosine ops?",
        workspace_id=ws_id,
    )
    mock_repo.search_hybrid.assert_awaited_once_with(
        workspace_id=ws_id,
        user_id=user_id,
        query_embedding=vec,
        query_text="How to configure pgvector cosine ops?",
        categories=["knowledge"],
        limit=5,
        raw_threshold=0.78,
    )
