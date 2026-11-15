"""The per-user mailbox in the real database: sealed at rest, row level security, isolation."""

from __future__ import annotations

import base64
import os
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connections.crypto import ConnectionCipher
from app.connections.service import ConnectionService
from app.db.repositories.user_settings import UserSettingsStore
from tests.support.mail_fakes import ADDRESS, APP_PASSWORD
from tests.support.postgres import SeededTenant

pytestmark = pytest.mark.postgres

Tenants = tuple[SeededTenant, SeededTenant]
KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
GROUPED = "fake appp assw ord0"


@pytest.fixture
def tenants(fresh_tenants: Tenants) -> Tenants:
    return fresh_tenants


def service(app_engine: AsyncEngine) -> ConnectionService:
    return ConnectionService(UserSettingsStore(app_engine), ConnectionCipher.from_setting(KEY))


async def save(svc: ConnectionService, tenant: SeededTenant, user: Any) -> Any:
    return await svc.save_mailbox(
        tenant.workspace_id,
        user,
        address=ADDRESS,
        app_password=GROUPED,
        imap_host=None,
        imap_port=None,
        smtp_host=None,
        smtp_port=None,
    )


async def test_the_stored_row_is_ciphertext_and_isolated(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    a, b = tenants
    svc = service(app_engine)
    await save(svc, a, a.user_id)
    async with admin_engine.connect() as connection:
        raw = (
            await connection.execute(
                text(
                    "SELECT convert_to(settings::text, 'UTF8') FROM user_settings "
                    "WHERE workspace_id = :ws AND user_id = :u"
                ),
                {"ws": a.workspace_id, "u": a.user_id},
            )
        ).scalar_one()
    stored = bytes(raw)
    assert b"connection:mail" in stored and b"v1." in stored
    for secret in (APP_PASSWORD, GROUPED, ADDRESS, "gmail.com"):
        assert secret.encode() not in stored  # not plaintext in the column bytes
    mine = await svc.resolve_mailbox(a.workspace_id, a.user_id)
    assert mine is not None and mine.password == APP_PASSWORD
    # Same workspace, other user; and another workspace: nothing resolves.
    assert await svc.resolve_mailbox(a.workspace_id, a.other_user_id) is None
    assert await svc.resolve_mailbox(b.workspace_id, b.user_id) is None
    assert (await svc.status_of(b.workspace_id, b.user_id, "mail")).state == "not_connected"


async def test_row_level_security_hides_the_row_from_another_tenant_session(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    a, b = tenants
    svc = service(app_engine)
    await save(svc, a, a.user_id)
    # Reading A's key while scoped to B's workspace and user returns nothing.
    store = UserSettingsStore(app_engine)
    assert await store.get_text(b.workspace_id, b.user_id, "connection:mail") is None
    assert await store.get_text(a.workspace_id, a.other_user_id, "connection:mail") is None


async def test_disconnect_deletes_the_secret(
    app_engine: AsyncEngine, admin_engine: AsyncEngine, tenants: Tenants
) -> None:
    a, _b = tenants
    svc = service(app_engine)
    await save(svc, a, a.user_id)
    await svc.disconnect(a.workspace_id, a.user_id, "mail")
    async with admin_engine.connect() as connection:
        left = (
            await connection.execute(
                text(
                    "SELECT settings ? 'connection:mail' FROM user_settings "
                    "WHERE workspace_id = :ws AND user_id = :u"
                ),
                {"ws": a.workspace_id, "u": a.user_id},
            )
        ).scalar_one()
    assert left is False
