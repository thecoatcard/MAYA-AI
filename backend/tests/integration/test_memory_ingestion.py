"""Integration tests for MemoryIngestionService."""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.repositories.memory import MemoryItemRecord, MemoryRepository
from app.gateway.exceptions import EmbeddingDimensionMismatchError
from app.gateway.schemas import EmbeddingResponse, TokenUsage
from app.memory.ingestion import MemoryIngestionService, SemanticChunker
from app.memory.scrubber import PiiScrubber


def _make_vector(dim: int = 768) -> list[float]:
    raw = [0.1] * dim
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


@pytest.mark.asyncio
async def test_memory_ingestion_with_scrubbing_and_embeddings() -> None:
    ws_id = uuid4()
    user_id = uuid4()

    mock_repo = MagicMock(spec=MemoryRepository)
    created_items: list[MemoryItemRecord] = []

    async def fake_create_item(**kwargs: Any) -> MemoryItemRecord:
        item = MemoryItemRecord(
            id=uuid4(),
            workspace_id=kwargs["workspace_id"],
            user_id=kwargs["user_id"],
            category=kwargs["category"],
            content=kwargs["content"],
            metadata=kwargs.get("metadata", {}),
            embedding=kwargs.get("embedding"),
            embedding_model=kwargs.get("embedding_model", "gemini-embedding-2"),
            permanent=kwargs.get("permanent", False),
            access_count=1,
            last_accessed_at=None,  # type: ignore[arg-type]
            expires_at=None,
            created_at=None,  # type: ignore[arg-type]
        )
        created_items.append(item)
        return item

    mock_repo.create_item = AsyncMock(side_effect=fake_create_item)

    mock_gateway = MagicMock()

    # Mock embed_content returning 768-dim vectors
    async def fake_embed_content(
        alias: str, contents: list[str] | str, **kwargs: Any
    ) -> EmbeddingResponse:
        count = len(contents) if isinstance(contents, list) else 1
        return EmbeddingResponse(
            vectors=[_make_vector(768) for _ in range(count)],
            model_id="gemini-embedding-2",
            usage=TokenUsage(prompt_tokens=10, candidates_tokens=0, total_token_count=10),
            alias_used="embed",
            was_fallback=False,
        )

    mock_gateway.embed_content = AsyncMock(side_effect=fake_embed_content)

    service = MemoryIngestionService(
        repository=mock_repo,
        scrubber=PiiScrubber(),
        chunker=SemanticChunker(),
        gateway=mock_gateway,
    )

    sensitive_doc = (
        "Project Orion guidelines. Use AWS key AKIAIOSFODNN7EXAMPLE for deployment.\n\n"
        "Billing account is tied to Visa card 4532-0151-1283-0366."
    )

    items = await service.ingest_document(
        workspace_id=ws_id,
        user_id=user_id,
        category="knowledge",
        content=sensitive_doc,
        metadata={"project": "Orion"},
        permanent=True,
    )

    assert len(items) > 0
    # Verify PII was scrubbed
    for item in items:
        assert "AKIAIOSFODNN7EXAMPLE" not in item.content
        assert "4532-0151-1283-0366" not in item.content
        assert item.permanent is True
        assert item.category == "knowledge"
        assert item.metadata["project"] == "Orion"
        assert item.metadata["redactions_count"] >= 2
        assert item.embedding is not None
        assert len(item.embedding) == 768


@pytest.mark.asyncio
async def test_memory_ingestion_fails_on_dimension_mismatch() -> None:
    ws_id = uuid4()
    user_id = uuid4()

    mock_repo = MagicMock(spec=MemoryRepository)
    mock_gateway = MagicMock()

    # Returns 512-dim vector instead of 768
    async def bad_embed_content(
        alias: str, contents: list[str] | str, **kwargs: Any
    ) -> EmbeddingResponse:
        return EmbeddingResponse(
            vectors=[_make_vector(512)],
            model_id="bad-embedder",
            usage=TokenUsage(prompt_tokens=10, candidates_tokens=0, total_token_count=10),
            alias_used="embed",
            was_fallback=False,
        )

    mock_gateway.embed_content = AsyncMock(side_effect=bad_embed_content)

    service = MemoryIngestionService(
        repository=mock_repo,
        scrubber=PiiScrubber(),
        chunker=SemanticChunker(),
        gateway=mock_gateway,
    )

    with pytest.raises(EmbeddingDimensionMismatchError):
        await service.ingest_document(
            workspace_id=ws_id,
            user_id=user_id,
            category="project",
            content="Sample text content for dimension validation.",
        )
