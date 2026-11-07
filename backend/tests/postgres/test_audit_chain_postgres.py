"""Audit hash chain against real PostgreSQL as the non-owner runtime role (FORCE RLS)."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.tenant import tenant_session
from app.services.audit_service import (
    AuditWriteError,
    ChainVerification,
    pseudonymise_user_ref,
    purge_user_ref,
    record_audit,
    verify_chain,
)
from tests.support.postgres import SeededTenant

pytestmark = pytest.mark.postgres

Tenants = tuple[SeededTenant, SeededTenant]


async def _write(engine: AsyncEngine, tenant: SeededTenant, n: int) -> None:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        await record_audit(
            session,
            workspace_id=tenant.workspace_id,
            user_ref=str(tenant.user_id),
            action="task.started",
            status="success",
            input={"n": n},
        )
        await session.commit()


async def _verify(engine: AsyncEngine, tenant: SeededTenant) -> ChainVerification:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        return await verify_chain(session, tenant.workspace_id)


@pytest.mark.asyncio
async def test_concurrent_writers_form_one_linear_chain(
    app_engine: AsyncEngine, fresh_tenants: Tenants
) -> None:
    tenant, _ = fresh_tenants
    await asyncio.gather(*(_write(app_engine, tenant, i) for i in range(12)))
    result = await _verify(app_engine, tenant)
    assert result.valid is True
    assert result.first_bad_row_id is None
    assert result.count == 12
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        rows = (
            await session.execute(text("SELECT prev_hash, row_hash FROM audit_log ORDER BY id"))
        ).all()
    assert rows[0][0] is None
    for earlier, later in zip(rows, rows[1:], strict=False):
        assert later[0] == earlier[1]


@pytest.mark.asyncio
async def test_chain_is_per_workspace_and_isolated(
    app_engine: AsyncEngine, fresh_tenants: Tenants
) -> None:
    a, b = fresh_tenants
    await asyncio.gather(*(_write(app_engine, t, i) for i in range(3) for t in (a, b)))
    assert (await _verify(app_engine, a)).count == 3
    assert (await _verify(app_engine, b)).count == 3
    async with tenant_session(app_engine, a.workspace_id, a.user_id) as session:
        other = await session.execute(
            text("SELECT count(*) FROM audit_log WHERE workspace_id = :w"), {"w": b.workspace_id}
        )
        assert other.scalar_one() == 0
        first = (
            await session.execute(text("SELECT prev_hash FROM audit_log ORDER BY id LIMIT 1"))
        ).scalar_one()
        assert first is None


@pytest.mark.asyncio
async def test_tampering_is_detected_at_first_bad_row(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, fresh_tenants: Tenants
) -> None:
    tenant, _ = fresh_tenants
    for i in range(4):
        await _write(app_engine, tenant, i)
    async with admin_engine.begin() as conn:
        found = await conn.execute(
            text("SELECT id FROM audit_log WHERE workspace_id=:w ORDER BY id"),
            {"w": tenant.workspace_id},
        )
        ids = [r[0] for r in found.all()]
        await conn.execute(text("UPDATE audit_log SET status='failed' WHERE id=:i"), {"i": ids[1]})
    result = await _verify(app_engine, tenant)
    assert result.valid is False
    assert result.first_bad_row_id == ids[1]


@pytest.mark.asyncio
async def test_runtime_role_cannot_update_or_delete(
    app_engine: AsyncEngine, fresh_tenants: Tenants
) -> None:
    tenant, _ = fresh_tenants
    await _write(app_engine, tenant, 0)
    for sql in ("UPDATE audit_log SET status='success'", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError):
            async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
                await session.execute(text(sql))
    assert (await _verify(app_engine, tenant)).count == 1


@pytest.mark.asyncio
async def test_payloads_are_redacted(app_engine: AsyncEngine, fresh_tenants: Tenants) -> None:
    tenant, _ = fresh_tenants
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijk"  # secret-scan:allow: test dummy JWT
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        await record_audit(
            session,
            workspace_id=tenant.workspace_id,
            user_ref="u",
            server_name="github",
            tool_name="create_issue",
            permission_level="WRITE",
            status="success",
            input={
                "password": "hunter2",
                "note": f"Bearer abc.def-123 {jwt}",
                "card": "4111 1111 1111 1111",
            },
            output={
                "text": "api_key=sk-abcdefghijklmnopqrstuv",
                "nested": [{"token": "x"}],
                "ratio": 1.5,
            },
        )
        await session.commit()
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        row = (
            await session.execute(text("SELECT input_redacted, output_redacted FROM audit_log"))
        ).one()
    blob = str(row)
    for secret in ("hunter2", "abc.def-123", jwt, "4111", "sk-abcdefghij"):
        assert secret not in blob
    assert row[1]["ratio"] == "1.5"
    assert (await _verify(app_engine, tenant)).valid


@pytest.mark.asyncio
async def test_bad_input_raises_typed_error_without_poisoning_transaction(
    app_engine: AsyncEngine, fresh_tenants: Tenants
) -> None:
    tenant, _ = fresh_tenants
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        with pytest.raises(AuditWriteError):
            await record_audit(
                session, workspace_id=tenant.workspace_id, user_ref="u", status="bogus"
            )
        await record_audit(
            session,
            workspace_id=tenant.workspace_id,
            user_ref="u",
            action="auth.login",
            status="denied",
        )
        await session.commit()
    assert (await _verify(app_engine, tenant)).count == 1


@pytest.mark.asyncio
async def test_other_workspace_id_is_refused_by_rls(
    app_engine: AsyncEngine, fresh_tenants: Tenants
) -> None:
    a, b = fresh_tenants
    async with tenant_session(app_engine, a.workspace_id, a.user_id) as session:
        with pytest.raises(AuditWriteError):
            await record_audit(
                session,
                workspace_id=b.workspace_id,
                user_ref="u",
                action="task.started",
                status="success",
            )


@pytest.mark.asyncio
async def test_purge_pseudonymises_and_reseals_chain(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, fresh_tenants: Tenants
) -> None:
    tenant, _ = fresh_tenants
    uid = str(tenant.user_id)
    for i in range(3):
        await _write(app_engine, tenant, i)
    async with AsyncSession(admin_engine) as admin:
        changed, pseud = await purge_user_ref(admin, tenant.workspace_id, uid)
        await admin.commit()
    assert changed == 3
    assert pseud == pseudonymise_user_ref(uid, tenant.workspace_id)
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        refs = {r[0] for r in (await session.execute(text("SELECT user_ref FROM audit_log"))).all()}
    assert refs == {pseud}
    assert (await _verify(app_engine, tenant)).valid


def test_pseudonym_is_stable_and_workspace_scoped() -> None:
    w1, w2 = uuid4(), uuid4()
    assert pseudonymise_user_ref("u", w1) == pseudonymise_user_ref("u", w1)
    assert pseudonymise_user_ref("u", w1) != pseudonymise_user_ref("u", w2)
    assert pseudonymise_user_ref("u", w1) != "u"
