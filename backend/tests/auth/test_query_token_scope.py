"""Query-string tokens are accepted only on GET file-link routes; the header works everywhere."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import (
    get_file_link_principal,
    get_principal,
)
from app.auth.errors import NotAuthenticated
from app.auth.models import Principal
from app.core.logging import SecretQueryFilter

GOOD_TOKEN = "-".join(["fake", "test", "token", "not", "a", "secret"])
PRINCIPAL = Principal(user_id=uuid4(), workspace_id=uuid4(), role="owner", session_id=uuid4())
PrincipalDep = Annotated[Principal, Depends(get_principal)]
FileLinkPrincipalDep = Annotated[Principal, Depends(get_file_link_principal)]


class _FakeAuthService:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token != GOOD_TOKEN:
            raise NotAuthenticated
        return PRINCIPAL


def _build_app() -> FastAPI:
    app = FastAPI()
    app.state.auth_service = _FakeAuthService()

    @app.exception_handler(NotAuthenticated)
    async def _unauthenticated(_: Request, __: NotAuthenticated) -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})

    @app.get("/header-only")
    async def header_get(principal: PrincipalDep) -> dict[str, bool]:
        return {"ok": True}

    @app.post("/header-only")
    async def header_post(principal: PrincipalDep) -> dict[str, bool]:
        return {"ok": True}

    @app.delete("/header-only")
    async def header_delete(principal: PrincipalDep) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/file-link")
    async def file_link_get(principal: FileLinkPrincipalDep) -> dict[str, bool]:
        return {"ok": True}

    @app.post("/file-link")
    async def file_link_post(principal: FileLinkPrincipalDep) -> dict[str, bool]:
        return {"ok": True}

    @app.delete("/file-link")
    async def file_link_delete(principal: FileLinkPrincipalDep) -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_header_token_works_on_every_method(client: AsyncClient) -> None:
    headers = {"Authorization": f"Bearer {GOOD_TOKEN}"}
    assert (await client.get("/header-only", headers=headers)).status_code == 200
    assert (await client.post("/header-only", headers=headers)).status_code == 200
    assert (await client.delete("/header-only", headers=headers)).status_code == 200
    assert (await client.get("/file-link", headers=headers)).status_code == 200
    assert (await client.post("/file-link", headers=headers)).status_code == 200
    assert (await client.delete("/file-link", headers=headers)).status_code == 200


async def test_query_token_is_rejected_on_delete_and_post(client: AsyncClient) -> None:
    query = {"token": GOOD_TOKEN}
    assert (await client.delete("/header-only", params=query)).status_code == 401
    assert (await client.post("/header-only", params=query)).status_code == 401
    assert (await client.delete("/file-link", params=query)).status_code == 401
    assert (await client.post("/file-link", params=query)).status_code == 401


async def test_query_token_is_rejected_on_ordinary_get_routes(client: AsyncClient) -> None:
    assert (await client.get("/header-only", params={"token": GOOD_TOKEN})).status_code == 401


async def test_query_token_still_works_on_the_file_link_get_route(client: AsyncClient) -> None:
    response = await client.get("/file-link", params={"token": GOOD_TOKEN})
    assert response.status_code == 200


async def test_invalid_query_token_is_rejected_on_the_file_link_get_route(
    client: AsyncClient,
) -> None:
    assert (await client.get("/file-link", params={"token": "invalid-jwt"})).status_code == 401
    assert (await client.get("/file-link", params={"token": "   "})).status_code == 401


async def test_missing_credentials_are_rejected(client: AsyncClient) -> None:
    assert (await client.get("/file-link")).status_code == 401
    assert (await client.get("/header-only")).status_code == 401


async def test_query_token_value_never_reaches_log_records(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    # The test client itself logs every request URL at INFO; silence it so only app logs count.
    caplog.set_level(logging.WARNING, logger="httpx")
    await client.get("/file-link", params={"token": GOOD_TOKEN})
    await client.delete("/header-only", params={"token": GOOD_TOKEN})
    assert GOOD_TOKEN not in caplog.text


async def test_access_log_line_with_query_token_is_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The uvicorn access logger carries the raw request line, so its filter must scrub it."""
    access_logger = logging.getLogger("uvicorn.access")
    secret_filter = SecretQueryFilter()
    access_logger.addFilter(secret_filter)
    caplog.set_level(logging.INFO, logger="uvicorn.access")
    try:
        access_logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:5000",
            "GET",
            f"/api/v1/artifacts/abc?token={GOOD_TOKEN}&inline=true",
            "1.1",
            200,
        )
    finally:
        access_logger.removeFilter(secret_filter)
    assert GOOD_TOKEN not in caplog.text
    assert "token=[redacted]&inline=true" in caplog.text


async def test_real_artifact_routes_reject_query_tokens_except_the_download_link() -> None:
    """The artifacts router is exercised for real: only the GET download link reads ``?token=``.

    Rejected requests stop at the principal dependency, so no database is needed. The
    successful query-token download is covered by tests/integration/test_artifacts_api.py.
    """
    from app.api.v1.artifacts import router as artifacts_router

    app = _build_app()
    app.include_router(artifacts_router)
    artifact_id = uuid4()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        query = {"token": GOOD_TOKEN}
        assert (
            await http.delete(f"/api/v1/artifacts/{artifact_id}", params=query)
        ).status_code == 401
        assert (
            await http.get(f"/api/v1/artifacts/{artifact_id}/metadata", params=query)
        ).status_code == 401
        assert (await http.get("/api/v1/artifacts", params=query)).status_code == 401
