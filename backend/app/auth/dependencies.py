from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.auth.errors import NotAuthenticated
from app.auth.models import Principal
from app.auth.service import AuthService
from app.db.tenant import tenant_transaction

bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth_service
    return service


def client_address(request: Request) -> str:
    """Use only the direct peer; forwarded headers are untrusted behind the Next proxy.

    The proxy leaves a client-supplied ``X-Forwarded-For`` intact, so it cannot
    identify a trustworthy source address for per-address limits. Revisit when
    the hosting target provides a front proxy that overwrites that header.
    """
    return request.client.host if request.client is not None else "unknown"


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Principal:
    """The caller behind a valid bearer token whose session is still live."""
    token: str | None = None
    if credentials is not None:
        token = credentials.credentials
    elif "token" in request.query_params:
        query_token = request.query_params.get("token", "").strip()
        if query_token:
            token = query_token

    if not token or service is None:
        raise NotAuthenticated
    return await service.authenticate(token)


@dataclass(frozen=True)
class TenantDatabase:
    """Database access bound to the authenticated workspace.

    Every transaction it opens carries the caller's ``app.workspace_id``, so row-level
    security applies automatically. User-level ownership is still the repository's job.
    """

    engine: AsyncEngine
    workspace_id: UUID

    def transaction(self) -> AbstractAsyncContextManager[AsyncConnection]:
        return tenant_transaction(self.engine, self.workspace_id)


async def get_tenant_database(
    principal: Annotated[Principal, Depends(get_principal)], request: Request
) -> TenantDatabase:
    res = getattr(request.app.state, "resources", None)
    if res is not None and hasattr(res, "register_workspace"):
        await res.register_workspace(principal.workspace_id)
    return TenantDatabase(request.app.state.resources.engine, principal.workspace_id)
