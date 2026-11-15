"""PostgreSQL integration tests for MemoryRepository.

Verifies:
- CRUD operations for memory items (preference, project, episodic, knowledge)
- Vector persistence (768-dimension vector)
- Hybrid search (dense vector cosine similarity + sparse full-text search)
- Defect #16: Raw cosine similarity threshold gating (S_sim >= 0.78) prior to time-decay scoring
- Permanent items bypassing decay
- Automatic access_count increment and last_accessed_at update
- Stale memory expiration
- Atomic purge (GDPR Article 17)
- Multi-tenant RLS isolation between workspaces
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.repositories.memory import MemoryRepository
from app.db.tenant import tenant_session
from tests.support.postgres import SeededTenant

pytestmark = pytest.mark.postgres

Tenants = tuple[SeededTenant, SeededTenant]


@pytest.fixture
def tenants(fresh_tenants: Tenants) -> Tenants:
    return fresh_tenants


def _make_vector(seed_val: float = 0.1, dim: int = 768) -> list[float]:
    """Generate a normalized 768-dim vector."""
    raw = [seed_val + (0.001 * (i % 10)) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def _make_orthogonal_vector(dim: int = 768) -> list[float]:
    """Generate a vector nearly orthogonal to _make_vector."""
    raw = [(-1.0 if i % 2 == 0 else 1.0) * 0.1 for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


async def test_memory_crud_operations(admin_engine: AsyncEngine, tenants: Tenants) -> None:
    tenant_a, _ = tenants
    vec = _make_vector(0.1)

    async with tenant_session(admin_engine, tenant_a.workspace_id, tenant_a.user_id) as session:
        repo = MemoryRepository(session)

        # 1. Create item
        item = await repo.create_item(
            workspace_id=tenant_a.workspace_id,
            user_id=tenant_a.user_id,
            category="preference",
            content="User prefers dark mode and concise responses.",
            metadata={"source": "onboarding", "theme": "dark"},
            embedding=vec,
            embedding_model="gemini-embedding-2",
            permanent=True,
        )
        await session.commit()

        assert item.id is not None
        assert item.workspace_id == tenant_a.workspace_id
        assert item.user_id == tenant_a.user_id
        assert item.category == "preference"
        assert item.content == "User prefers dark mode and concise responses."
        assert item.metadata == {"source": "onboarding", "theme": "dark"}
        assert item.embedding_model == "gemini-embedding-2"
        assert item.permanent is True
        assert item.access_count == 1

        # 2. Get item by id
        fetched = await repo.get_item_by_id(tenant_a.workspace_id, tenant_a.user_id, item.id)
        assert fetched is not None
        assert fetched.id == item.id
        assert fetched.content == item.content
        assert fetched.permanent is True

        # 3. List items
        items = await repo.list_items_for_user(
            tenant_a.workspace_id, tenant_a.user_id, category="preference"
        )
        assert any(it.id == item.id for it in items)

        # 4. Delete item
        deleted = await repo.delete_item(tenant_a.workspace_id, tenant_a.user_id, item.id)
        await session.commit()
        assert deleted is True

        missing = await repo.get_item_by_id(tenant_a.workspace_id, tenant_a.user_id, item.id)
        assert missing is None


async def test_tenant_rls_isolation(admin_engine: AsyncEngine, tenants: Tenants) -> None:
    tenant_a, tenant_b = tenants
    vec = _make_vector(0.2)

    # Insert in tenant A
    async with tenant_session(admin_engine, tenant_a.workspace_id, tenant_a.user_id) as session_a:
        repo_a = MemoryRepository(session_a)
        item_a = await repo_a.create_item(
            workspace_id=tenant_a.workspace_id,
            user_id=tenant_a.user_id,
            category="knowledge",
            content="Confidential project code for Workspace A.",
            metadata={"classification": "secret"},
            embedding=vec,
            embedding_model="gemini-embedding-2",
        )
        await session_a.commit()

    # Query in tenant B
    async with tenant_session(admin_engine, tenant_b.workspace_id, tenant_b.user_id) as session_b:
        repo_b = MemoryRepository(session_b)
        # Attempt to fetch item_a by ID
        fetched = await repo_b.get_item_by_id(tenant_b.workspace_id, tenant_b.user_id, item_a.id)
        assert fetched is None

        # Attempt to search item_a by vector similarity in tenant B
        results = await repo_b.search_hybrid(
            workspace_id=tenant_b.workspace_id,
            user_id=tenant_b.user_id,
            query_embedding=vec,
            query_text="Confidential project",
            raw_threshold=0.5,
        )
        assert len(results) == 0


async def test_hybrid_search_and_defect_16_threshold(
    admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant_a, _ = tenants
    target_vec = _make_vector(0.5)
    ortho_vec = _make_orthogonal_vector()

    async with tenant_session(admin_engine, tenant_a.workspace_id, tenant_a.user_id) as session:
        repo = MemoryRepository(session)

        # High similarity item matching target_vec (sim ~ 1.0)
        item_high = await repo.create_item(
            workspace_id=tenant_a.workspace_id,
            user_id=tenant_a.user_id,
            category="project",
            content="FastAPI backend setup with async SQLAlchemy.",
            metadata={"doc": "backend"},
            embedding=target_vec,
            embedding_model="gemini-embedding-2",
            permanent=False,
        )

        # Low similarity item (sim < 0.78)
        await repo.create_item(
            workspace_id=tenant_a.workspace_id,
            user_id=tenant_a.user_id,
            category="project",
            content="Unrelated culinary recipes and baking techniques.",
            metadata={"doc": "culinary"},
            embedding=ortho_vec,
            embedding_model="gemini-embedding-2",
            permanent=False,
        )
        await session.commit()

        # Search with threshold 0.78: item_high must qualify, item_low must be excluded
        results = await repo.search_hybrid(
            workspace_id=tenant_a.workspace_id,
            user_id=tenant_a.user_id,
            query_embedding=target_vec,
            query_text="FastAPI backend setup",
            raw_threshold=0.78,
        )
        await session.commit()

        assert len(results) == 1
        assert results[0].id == item_high.id
        assert results[0].raw_similarity >= 0.78
        assert results[0].effective_score > 0.0

        # Verify access count was bumped on returned result
        refetched = await repo.get_item_by_id(tenant_a.workspace_id, tenant_a.user_id, item_high.id)
        assert refetched is not None
        assert refetched.access_count == 2


async def test_time_decay_ranking_and_permanent_bypass(
    admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    tenant_a, _ = tenants
    vec = _make_vector(0.3)

    async with tenant_session(admin_engine, tenant_a.workspace_id, tenant_a.user_id) as session:
        repo = MemoryRepository(session)

        # Item 1: Recent, non-permanent
        await repo.create_item(
            workspace_id=tenant_a.workspace_id,
            user_id=tenant_a.user_id,
            category="episodic",
            content="Meeting notes discussing architecture roadmap.",
            embedding=vec,
            embedding_model="gemini-embedding-2",
            permanent=False,
        )

        # Item 2: Old (60 days ago), permanent
        item_permanent_old = await repo.create_item(
            workspace_id=tenant_a.workspace_id,
            user_id=tenant_a.user_id,
            category="episodic",
            content="Core architectural guideline for system design.",
            embedding=vec,
            embedding_model="gemini-embedding-2",
            permanent=True,
        )

        # Item 3: Old (60 days ago), non-permanent
        item_decayed_old = await repo.create_item(
            workspace_id=tenant_a.workspace_id,
            user_id=tenant_a.user_id,
            category="episodic",
            content="Old discussion about temporary architecture prototype.",
            embedding=vec,
            embedding_model="gemini-embedding-2",
            permanent=False,
        )

        # Manually backdate item 2 and item 3 last_accessed_at by 60 days
        sixty_days_ago = datetime.now(UTC) - timedelta(days=60)
        await session.execute(
            text("UPDATE memory_items SET last_accessed_at = :past WHERE id IN (:id2, :id3)"),
            {"past": sixty_days_ago, "id2": item_permanent_old.id, "id3": item_decayed_old.id},
        )
        await session.commit()

        # Search
        results = await repo.search_hybrid(
            workspace_id=tenant_a.workspace_id,
            user_id=tenant_a.user_id,
            query_embedding=vec,
            query_text="architecture guideline roadmap",
            raw_threshold=0.78,
            limit=5,
        )

        # All 3 pass raw threshold >= 0.78 (Defect #16 decay decoupling)
        assert len(results) == 3
        # item_decayed_old must have a lower effective score than item_permanent_old
        scores_by_id = {r.id: r.effective_score for r in results}
        assert scores_by_id[item_permanent_old.id] > scores_by_id[item_decayed_old.id]


async def test_mark_expired_and_atomic_purge(admin_engine: AsyncEngine, tenants: Tenants) -> None:
    tenant_a, _ = tenants
    vec = _make_vector(0.4)

    async with tenant_session(admin_engine, tenant_a.workspace_id, tenant_a.user_id) as session:
        repo = MemoryRepository(session)

        # Create 1 permanent, 1 recent non-permanent, 1 stale non-permanent
        p_item = await repo.create_item(
            workspace_id=tenant_a.workspace_id,
            user_id=tenant_a.user_id,
            category="preference",
            content="Permanent user preference.",
            embedding=vec,
            embedding_model="gemini-embedding-2",
            permanent=True,
        )
        await repo.create_item(
            workspace_id=tenant_a.workspace_id,
            user_id=tenant_a.user_id,
            category="episodic",
            content="Recent episodic memory.",
            embedding=vec,
            embedding_model="gemini-embedding-2",
            permanent=False,
        )
        s_item = await repo.create_item(
            workspace_id=tenant_a.workspace_id,
            user_id=tenant_a.user_id,
            category="episodic",
            content="Stale episodic memory from 200 days ago.",
            embedding=vec,
            embedding_model="gemini-embedding-2",
            permanent=False,
        )

        # Backdate stale item last_accessed_at by 200 days
        two_hundred_days_ago = datetime.now(UTC) - timedelta(days=200)
        await session.execute(
            text("UPDATE memory_items SET last_accessed_at = :past WHERE id = :id"),
            {"past": two_hundred_days_ago, "id": s_item.id},
        )
        await session.commit()

        # Archive stale memories (> 180 days)
        expired_count = await repo.mark_expired(tenant_a.workspace_id, threshold_days=180)
        await session.commit()
        assert expired_count == 1

        # Verify s_item is marked expired
        s_fetched = await repo.get_item_by_id(tenant_a.workspace_id, tenant_a.user_id, s_item.id)
        assert s_fetched is not None
        assert s_fetched.expires_at is not None

        # Verify permanent item is NOT expired
        p_fetched = await repo.get_item_by_id(tenant_a.workspace_id, tenant_a.user_id, p_item.id)
        assert p_fetched is not None
        assert p_fetched.expires_at is None

        # GDPR Purge for tenant_a.user_id
        purged = await repo.purge_user_memories(tenant_a.workspace_id, tenant_a.user_id)
        await session.commit()
        assert purged >= 3

        # Confirm 0 rows remain for user
        remaining = await repo.list_items_for_user(tenant_a.workspace_id, tenant_a.user_id)
        assert len(remaining) == 0
