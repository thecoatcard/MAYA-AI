"""Semantic chunker and document ingestion pipeline.

Chunks documents, scrubs PII/secrets, requests 768-dim embeddings from ModelGateway,
and persists chunks to MemoryRepository.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from app.db.repositories.memory import MemoryItemRecord, MemoryRepository
from app.gateway.exceptions import EmbeddingDimensionMismatchError
from app.gateway.model_gateway import ModelGateway
from app.memory.scrubber import PiiScrubber

_SENTENCE_BOUNDARY: re.Pattern[str] = re.compile(r"(?<=[.!?])\s+")


class SemanticChunker:
    """Splits text into chunks preserving paragraph and sentence boundaries with overlap."""

    def chunk_text(self, text: str, max_tokens: int = 512, overlap: int = 64) -> list[str]:
        """Split text into semantic chunks."""
        if not text or not text.strip():
            return []

        clean_text = text.strip()
        max_chars = max(40, max_tokens * 4)
        overlap_chars = max(0, overlap * 4)

        if len(clean_text) <= max_chars:
            return [clean_text]

        # 1. Break down into atomic segments (paragraphs, or sentences within long paragraphs)
        raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean_text) if p.strip()]
        segments: list[str] = []

        for para in raw_paragraphs:
            if len(para) <= max_chars:
                segments.append(para)
            else:
                sentences = [s.strip() for s in _SENTENCE_BOUNDARY.split(para) if s.strip()]
                for s in sentences:
                    if len(s) <= max_chars:
                        segments.append(s)
                    else:
                        # Split exceptionally long sentence on words
                        words = s.split()
                        buf: list[str] = []
                        buf_len = 0
                        for w in words:
                            if buf_len + len(w) + 1 > max_chars and buf:
                                segments.append(" ".join(buf))
                                buf = [w]
                                buf_len = len(w)
                            else:
                                buf.append(w)
                                buf_len += len(w) + 1
                        if buf:
                            segments.append(" ".join(buf))

        if not segments:
            return []

        # 2. Greedily pack segments into chunks with overlap
        chunks: list[str] = []
        chunk_segments: list[str] = []
        current_len = 0

        for seg in segments:
            seg_len = len(seg)
            # Check if adding this segment would exceed max_chars
            if current_len > 0 and (current_len + seg_len + 1 > max_chars):
                chunk_str = " ".join(chunk_segments).strip()
                if chunk_str:
                    chunks.append(chunk_str)

                # Collect trailing segments for overlap
                tail: list[str] = []
                tail_len = 0
                for prev in reversed(chunk_segments):
                    if tail_len + len(prev) + 1 <= overlap_chars:
                        tail.insert(0, prev)
                        tail_len += len(prev) + 1
                    else:
                        break

                chunk_segments = list(tail)
                current_len = tail_len

            chunk_segments.append(seg)
            current_len += seg_len + 1

        if chunk_segments:
            final_chunk = " ".join(chunk_segments).strip()
            if final_chunk:
                chunks.append(final_chunk)

        return chunks


class MemoryIngestionService:
    """Coordinates scrubbing, chunking, embedding generation, and repository persistence."""

    def __init__(
        self,
        repository: MemoryRepository,
        scrubber: PiiScrubber | None = None,
        chunker: SemanticChunker | None = None,
        gateway: ModelGateway | None = None,
    ) -> None:
        self.repository = repository
        self.scrubber = scrubber or PiiScrubber()
        self.chunker = chunker or SemanticChunker()
        self.gateway = gateway

    async def ingest_document(
        self,
        workspace_id: UUID,
        user_id: UUID,
        category: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        permanent: bool = False,
        max_tokens: int = 512,
        overlap: int = 64,
    ) -> list[MemoryItemRecord]:
        """Ingest, scrub, chunk, embed, and persist a memory or knowledge document."""
        # 1. Deterministic PII & Secret Scrubbing
        scrub_res = self.scrubber.scrub(content)
        clean_text = scrub_res.clean_text.strip()
        if not clean_text:
            return []

        # 2. Semantic Chunking
        chunks = self.chunker.chunk_text(clean_text, max_tokens=max_tokens, overlap=overlap)
        if not chunks:
            return []

        # 3. Generate 768-dim embeddings via ModelGateway
        vectors: list[list[float] | None] = []
        embedding_model = "gemini-embedding-2"

        if self.gateway is not None:
            embed_res = await self.gateway.embed_content(
                alias="embed",
                contents=chunks,
                workspace_id=workspace_id,
            )
            # Validate 768 dimensions
            for idx, v in enumerate(embed_res.vectors):
                if len(v) != 768:
                    raise EmbeddingDimensionMismatchError(
                        f"Chunk {idx} produced vector of {len(v)} dimensions, expected 768."
                    )
                vectors.append(v)
            embedding_model = embed_res.model_id or embedding_model
        else:
            # When gateway is not supplied, vector remains None
            vectors = [None] * len(chunks)

        # 4. Store chunks in MemoryRepository
        created_records: list[MemoryItemRecord] = []
        total = len(chunks)

        for i, chunk in enumerate(chunks):
            chunk_metadata = {
                "chunk_index": i,
                "total_chunks": total,
                "redactions_count": scrub_res.redactions_count,
                "detected_types": scrub_res.detected_types,
                **(metadata or {}),
            }
            vec: list[float] | None = vectors[i] if i < len(vectors) else None

            record = await self.repository.create_item(
                workspace_id=workspace_id,
                user_id=user_id,
                category=category,
                content=chunk,
                metadata=chunk_metadata,
                embedding=vec,
                embedding_model=embedding_model,
                permanent=permanent,
            )
            created_records.append(record)

        return created_records
