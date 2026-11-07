from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.auth.dependencies import client_address, get_principal
from app.auth.errors import NotAuthenticated
from app.auth.models import Principal


def test_client_address_ignores_client_supplied_forwarded_for() -> None:
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"x-forwarded-for", b"203.0.113.42")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )

    assert client_address(request) == "127.0.0.1"


@pytest.mark.asyncio
async def test_get_principal_with_bearer_credentials() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
        }
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bearer-token-123")
    expected_principal = Principal(
        user_id=uuid4(),
        workspace_id=uuid4(),
        role="member",
        session_id=uuid4(),
    )
    auth_service = MagicMock()
    auth_service.authenticate = AsyncMock(return_value=expected_principal)

    result = await get_principal(
        request=request,
        credentials=creds,
        service=auth_service,
    )
    assert result == expected_principal
    auth_service.authenticate.assert_awaited_once_with("bearer-token-123")


@pytest.mark.asyncio
async def test_get_principal_with_query_token_fallback() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/artifacts/download",
            "raw_path": b"/api/v1/artifacts/download?token=query-token-abc",
            "query_string": b"token=query-token-abc",
            "headers": [],
        }
    )
    expected_principal = Principal(
        user_id=uuid4(),
        workspace_id=uuid4(),
        role="admin",
        session_id=uuid4(),
    )
    auth_service = MagicMock()
    auth_service.authenticate = AsyncMock(return_value=expected_principal)

    result = await get_principal(
        request=request,
        credentials=None,
        service=auth_service,
    )
    assert result == expected_principal
    auth_service.authenticate.assert_awaited_once_with("query-token-abc")


@pytest.mark.asyncio
async def test_get_principal_missing_auth_raises_not_authenticated() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/artifacts",
            "raw_path": b"/api/v1/artifacts",
            "query_string": b"",
            "headers": [],
        }
    )
    auth_service = MagicMock()

    with pytest.raises(NotAuthenticated):
        await get_principal(
            request=request,
            credentials=None,
            service=auth_service,
        )
