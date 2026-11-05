"""The HTTP surface against a real database: tokens, tenant context and isolation together."""

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Annotated, Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.dependencies import TenantDatabase, get_tenant_database
from app.auth.postgres import PostgresAuthStore
from app.main import create_app
from tests.support.auth import GOOD_PASSWORD, NEW_PASSWORD, Harness, build_harness
from tests.support.settings import make_settings

pytestmark = pytest.mark.postgres

BASE = "/api/v1/auth"


@pytest.fixture
async def world(
    app_engine: AsyncEngine, admin_engine: AsyncEngine
) -> AsyncIterator[tuple[httpx.AsyncClient, Harness, AsyncEngine]]:
    harness = build_harness(PostgresAuthStore(app_engine))
    app: FastAPI = create_app(make_settings(), auth_service=harness.service)
    app.state.auth_service = harness.service
    app.state.resources = SimpleNamespace(engine=app_engine)

    @app.get("/probe/chat-sessions")
    async def probe(
        database: Annotated[TenantDatabase, Depends(get_tenant_database)],
    ) -> list[str]:
        async with database.transaction() as connection:
            rows = await connection.execute(text("SELECT id FROM sessions ORDER BY id"))
            return [str(row[0]) for row in rows]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, harness, admin_engine


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def onboard(
    client: httpx.AsyncClient, harness: Harness, admin: AsyncEngine, chat_sessions: int
) -> dict[str, Any]:
    """Register, verify and log in a new account; give its workspace some chat sessions."""
    command = harness.command()
    body = {
        "username": command.username,
        "email": command.email,
        "password": command.password,
        "full_name": command.full_name,
    }
    assert (await client.post(f"{BASE}/register", json=body)).status_code == 202
    code = harness.email.codes_for(command.email)[-1]
    verified = await client.post(
        f"{BASE}/verify-email",
        json={"email": command.email, "code": code, "password": command.password},
    )
    assert verified.status_code == 200
    tokens = (
        await client.post(
            f"{BASE}/token", json={"username": command.username, "password": command.password}
        )
    ).json()
    me = (await client.get(f"{BASE}/me", headers=bearer(tokens["access_token"]))).json()
    ids = {uuid4() for _ in range(chat_sessions)}
    async with admin.begin() as connection:
        for session_id in ids:
            await connection.execute(
                text("INSERT INTO sessions (id, workspace_id, user_id) VALUES (:id, :w, :u)"),
                {"id": session_id, "w": UUID(me["workspace_id"]), "u": UUID(me["user_id"])},
            )
    return {"command": command, "tokens": tokens, "me": me, "sessions": {str(i) for i in ids}}


async def test_each_token_reaches_only_its_own_workspace(
    world: tuple[httpx.AsyncClient, Harness, AsyncEngine],
) -> None:
    client, harness, admin = world
    alice = await onboard(client, harness, admin, 3)
    bob = await onboard(client, harness, admin, 2)
    assert alice["me"]["workspace_id"] != bob["me"]["workspace_id"]
    for account in (alice, bob):
        response = await client.get(
            "/probe/chat-sessions", headers=bearer(account["tokens"]["access_token"])
        )
        assert response.status_code == 200
        assert set(response.json()) == account["sessions"]


async def test_concurrent_requests_from_different_workspaces_never_mix(
    world: tuple[httpx.AsyncClient, Harness, AsyncEngine],
) -> None:
    client, harness, admin = world
    accounts = [await onboard(client, harness, admin, 2 + index) for index in range(3)]

    async def fetch(account: dict[str, Any]) -> None:
        response = await client.get(
            "/probe/chat-sessions", headers=bearer(account["tokens"]["access_token"])
        )
        assert set(response.json()) == account["sessions"]

    await asyncio.gather(*(fetch(accounts[index % 3]) for index in range(45)))


async def test_unauthenticated_and_revoked_callers_never_reach_the_database(
    world: tuple[httpx.AsyncClient, Harness, AsyncEngine],
) -> None:
    client, harness, admin = world
    account = await onboard(client, harness, admin, 1)
    assert (await client.get("/probe/chat-sessions")).status_code == 401
    assert (await client.get("/probe/chat-sessions", headers=bearer("garbage"))).status_code == 401
    token = account["tokens"]
    assert (
        await client.post(f"{BASE}/logout", json={"refresh_token": token["refresh_token"]})
    ).status_code == 204
    assert (
        await client.get("/probe/chat-sessions", headers=bearer(token["access_token"]))
    ).status_code == 401


async def test_password_reset_ends_access_immediately_not_at_token_expiry(
    world: tuple[httpx.AsyncClient, Harness, AsyncEngine],
) -> None:
    client, harness, admin = world
    account = await onboard(client, harness, admin, 1)
    email = account["command"].email
    token = account["tokens"]["access_token"]
    assert (await client.get("/probe/chat-sessions", headers=bearer(token))).status_code == 200
    assert (await client.post(f"{BASE}/forgot-password", json={"email": email})).status_code == 202
    code = harness.email.codes_for(email)[-1]
    reset = await client.post(
        f"{BASE}/reset-password", json={"email": email, "code": code, "new_password": NEW_PASSWORD}
    )
    assert reset.status_code == 200
    assert (await client.get("/probe/chat-sessions", headers=bearer(token))).status_code == 401
    fresh = await client.post(
        f"{BASE}/token", json={"username": account["command"].username, "password": NEW_PASSWORD}
    )
    assert fresh.status_code == 200
    assert (
        await client.post(
            f"{BASE}/token",
            json={"username": account["command"].username, "password": GOOD_PASSWORD},
        )
    ).status_code == 401


async def test_the_full_lifecycle_runs_on_the_restricted_role_only(
    world: tuple[httpx.AsyncClient, Harness, AsyncEngine], app_engine: AsyncEngine
) -> None:
    client, harness, admin = world
    account = await onboard(client, harness, admin, 0)
    refreshed = await client.post(
        f"{BASE}/refresh", json={"refresh_token": account["tokens"]["refresh_token"]}
    )
    assert refreshed.status_code == 200
    async with app_engine.connect() as connection:
        role = (await connection.execute(text("SELECT current_user"))).scalar_one()
    assert role == "maya_app"
