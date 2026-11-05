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
    """The direct peer. Proxy headers are ignored until a trusted-proxy setting is approved."""
    return request.client.host if request.client is not None else "unknown"


async def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Principal:
    """The caller behind a valid bearer token whose session is still live."""
    if credentials is None:
        raise NotAuthenticated
    return await service.authenticate(credentials.credentials)


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
    return TenantDatabase(request.app.state.resources.engine, principal.workspace_id)
