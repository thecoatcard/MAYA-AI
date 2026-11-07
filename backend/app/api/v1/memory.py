"""Memory and Knowledge System REST API."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.db.repositories.memory import MemoryRepository
from app.db.tenant import tenant_session
from app.memory.ingestion import MemoryIngestionService
from app.memory.lifecycle import MemoryLifecycleManager
from app.memory.rag_engine import HybridRagEngine
from app.memory.working_memory import WorkingMemoryManager
from app.schemas.memory import (
    MemoryItemCreate,
    MemoryItemResponse,
    MemoryPurgeRequest,
    MemoryPurgeResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResultItem,
)

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


@router.post(
    "/items",
    status_code=status.HTTP_201_CREATED,
    response_model=list[MemoryItemResponse],
)
async def ingest_memory_item(
    request: Request,
    payload: MemoryItemCreate,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> list[MemoryItemResponse]:
    """Ingest a new memory item, coordinating PII scrubbing, chunking, and embedding."""
    gateway = getattr(request.app.state.resources, "model_gateway", None)

    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        repo = MemoryRepository(session)
        service = MemoryIngestionService(repository=repo, gateway=gateway)

        records = await service.ingest_document(
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            category=payload.category,
            content=payload.content,
            metadata=payload.metadata,
            permanent=payload.permanent,
        )
        await session.commit()

        return [
            MemoryItemResponse(
                id=rec.id,
                workspace_id=rec.workspace_id,
                user_id=rec.user_id,
                category=rec.category,
                content=rec.content,
                metadata=rec.metadata,
                permanent=rec.permanent,
                access_count=rec.access_count,
                last_accessed_at=rec.last_accessed_at,
                expires_at=rec.expires_at,
                created_at=rec.created_at,
            )
            for rec in records
        ]


@router.get("/items", response_model=list[MemoryItemResponse])
async def list_memory_items(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
    category: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MemoryItemResponse]:
    """List active memory items for the authenticated user."""
    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        repo = MemoryRepository(session)
        records = await repo.list_items_for_user(
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            category=category,
            limit=limit,
            offset=offset,
        )
        return [
            MemoryItemResponse(
                id=rec.id,
                workspace_id=rec.workspace_id,
                user_id=rec.user_id,
                category=rec.category,
                content=rec.content,
                metadata=rec.metadata,
                permanent=rec.permanent,
                access_count=rec.access_count,
                last_accessed_at=rec.last_accessed_at,
                expires_at=rec.expires_at,
                created_at=rec.created_at,
            )
            for rec in records
        ]


@router.post("/search", response_model=MemorySearchResponse)
async def search_memory(
    request: Request,
    payload: MemorySearchRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> MemorySearchResponse:
    """Execute hybrid search with Defect #16 threshold gating and time decay ranking."""
    gateway = getattr(request.app.state.resources, "model_gateway", None)
    if gateway is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model Gateway is not configured for vector embeddings",
        )

    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        repo = MemoryRepository(session)
        engine = HybridRagEngine(repository=repo, gateway=gateway)

        citations = await engine.search(
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            query_text=payload.query,
            categories=payload.categories,
            limit=payload.limit,
            raw_threshold=payload.raw_threshold,
        )
        await session.commit()

        results = [
            MemorySearchResultItem(
                id=c.id,
                category=c.category,
                content=c.content,
                metadata=c.metadata,
                raw_similarity=c.raw_similarity,
                effective_score=c.effective_score,
                source_id=c.source_id,
                created_at=c.created_at,
            )
            for c in citations
        ]

        return MemorySearchResponse(
            query=payload.query,
            results=results,
            total_results=len(results),
        )


@router.delete("/items/{item_id}")
async def delete_memory_item(
    item_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> dict[str, Any]:
    """Delete a single memory item owned by the authenticated user."""
    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        repo = MemoryRepository(session)
        deleted = await repo.delete_item(
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            item_id=item_id,
        )
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Memory item not found",
            )
        await session.commit()
        return {"deleted": True, "id": str(item_id)}


@router.delete("/purge", response_model=MemoryPurgeResponse)
async def purge_user_memories(
    request: Request,
    payload: MemoryPurgeRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> MemoryPurgeResponse:
    """Execute GDPR Article 17 erasure across PostgreSQL and Redis."""
    redis_client = getattr(request.app.state.resources, "redis", None)
    working_mem = WorkingMemoryManager(redis_client) if redis_client is not None else None

    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        repo = MemoryRepository(session)
        lifecycle = MemoryLifecycleManager(repository=repo, working_memory=working_mem)

        purged_count = await lifecycle.purge_user_memories(
            session_or_conn=session,
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            scope=payload.scope,
        )
        await session.commit()

        return MemoryPurgeResponse(
            status="purged",
            scope=payload.scope,
            records_deleted=purged_count,
        )
