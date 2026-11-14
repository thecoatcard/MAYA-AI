"""``/api/v1/settings/connections``: the caller's own connections to GitHub, Notion, and others.

Contract: ``docs/api/CONNECTIONS.md``. Every route is bearer-authenticated and acts only on the
caller's own workspace and user (there is no user id in any URL or body, so there is nothing to
tamper with). Secrets go in on ``PUT`` and never come out: every response carries a masked
status only. Responses are ``Cache-Control: no-store``. Saving and testing call an outside
service, so they have a tighter rate limit than reading.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.v1.chat import get_chat_rate_limiter, get_chat_settings
from app.auth.dependencies import get_principal
from app.auth.errors import RateLimited
from app.auth.models import Principal
from app.connections.crypto import ConnectionCipher, ConnectionsNotConfigured
from app.connections.ownership import ownership_from_settings
from app.connections.providers import ConnectionInputError, TestResult
from app.connections.service import (
    ConnectionService,
    ConnectionStatus,
    EnvDefaults,
    UnknownProvider,
)
from app.core.config import Settings
from app.core.ratelimit import Limit, RateLimiter
from app.db.repositories.user_settings import UserSettingsStore

logger = logging.getLogger("maya")

router = APIRouter(prefix="/api/v1/settings/connections", tags=["connections"])

NO_STORE: Final = {"Cache-Control": "no-store"}
_WINDOW_SECONDS = 60
_READ_LIMIT = 60
_WRITE_LIMIT = 10  # save, test and disconnect reach an outside service or change a secret


class ConnectionIn(BaseModel):
    """``token`` is a ``SecretStr`` so it can never appear in a repr, a log line or an error."""

    model_config = ConfigDict(extra="forbid")

    token: SecretStr | None = Field(default=None, max_length=600)
    # Notion: the database URL or id. ``""`` clears it; omitted keeps the saved one.
    database_id: str | None = Field(default=None, max_length=600)
    # Mailbox only (``token`` is then the app password, write-only like every token).
    address: str | None = Field(default=None, max_length=320)
    imap_host: str | None = Field(default=None, max_length=253)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_host: str | None = Field(default=None, max_length=253)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)


def build_connection_service(request: Request) -> ConnectionService:
    """The service for this request (a test or the app may pre-build one on ``app.state``)."""
    existing = getattr(request.app.state, "connection_service", None)
    if isinstance(existing, ConnectionService):
        return existing
    settings: Settings = request.app.state.settings
    engine = request.app.state.resources.engine
    assert isinstance(engine, AsyncEngine)
    key = settings.connections_encryption_key
    return ConnectionService(
        UserSettingsStore(engine),
        ConnectionCipher.from_setting(key.get_secret_value() if key else None),
        EnvDefaults.from_settings(settings),
        deployment_owner=ownership_from_settings(settings, engine),
    )


Service = Annotated[ConnectionService, Depends(build_connection_service)]
Limiter = Annotated[RateLimiter, Depends(get_chat_rate_limiter)]
Caller = Annotated[Principal, Depends(get_principal)]
AppSettings = Annotated[Settings, Depends(get_chat_settings)]


async def _limit(principal: Principal, limiter: RateLimiter, *, write: bool) -> None:
    kind = "write" if write else "read"
    name = f"connections-{kind}:{principal.workspace_id}:{principal.user_id}"
    limit = Limit(_WRITE_LIMIT if write else _READ_LIMIT, _WINDOW_SECONDS)
    if not await limiter.hit(name, limit):
        raise RateLimited(retry_after=_WINDOW_SECONDS)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": code, "message": message}, headers=NO_STORE
    )


def _status_json(item: ConnectionStatus) -> dict[str, Any]:
    out = asdict(item)
    out["scopes"] = list(item.scopes) if item.scopes is not None else None
    return out


def _test_json(result: TestResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "code": result.code,
        "message": result.message,
        "account": result.account,
        "scopes": list(result.scopes) if result.scopes is not None else None,
        "database_title": result.database_title,
        "steps": (
            [{"label": a, "ok": b, "message": c} for a, b, c in result.steps]
            if result.steps is not None
            else None
        ),
    }


def _unknown(provider: str) -> JSONResponse:
    return _error(404, "unknown_provider", f"There is no connection called '{provider}'.")


_NOT_CONFIGURED = (
    "Saving a token needs MAYA_CONNECTIONS_ENCRYPTION_KEY on the server. Ask the person who runs "
    "this server to set it (docs/security/CONNECTIONS.md), then try again."
)


@router.get("")
async def list_connections(principal: Caller, service: Service, limiter: Limiter) -> JSONResponse:
    await _limit(principal, limiter, write=False)
    items = await service.status(principal.workspace_id, principal.user_id)
    # Additive owner hint: server tokens exist but MAYA_ENV_CONNECTIONS_USERS names nobody. It is
    # only a boolean (no secret) and is shown to every caller so the owner sees why.
    return JSONResponse(
        content={
            "encryption_available": service.encryption_available,
            "server_defaults_blocked": service.server_defaults_blocked,
            "connections": [_status_json(i) for i in items],
        },
        headers=NO_STORE,
    )


@router.put("/{provider}")
async def save_connection(
    provider: str, body: ConnectionIn, principal: Caller, service: Service, limiter: Limiter
) -> JSONResponse:
    """Save (and then test) the caller's credentials. The answer holds the masked status and the
    test outcome; a failed test still leaves the credentials saved so they can be fixed in place."""
    await _limit(principal, limiter, write=True)
    token = body.token.get_secret_value() if body.token is not None else None
    try:
        if service.spec(provider).kind == "mailbox":
            await service.save_mailbox(
                principal.workspace_id,
                principal.user_id,
                address=body.address,
                app_password=token,
                imap_host=body.imap_host,
                imap_port=body.imap_port,
                smtp_host=body.smtp_host,
                smtp_port=body.smtp_port,
            )
        else:
            await service.save(
                principal.workspace_id,
                principal.user_id,
                provider,
                token=token,
                database_id=body.database_id,
            )
    except UnknownProvider:
        return _unknown(provider)
    except ConnectionsNotConfigured:
        return _error(503, "connections_not_configured", _NOT_CONFIGURED)
    except ConnectionInputError as err:
        return _error(422, err.code, err.message)
    result = await service.test(principal.workspace_id, principal.user_id, provider)
    item = await service.status_of(principal.workspace_id, principal.user_id, provider)
    logger.info("connection_saved provider=%s ok=%s code=%s", provider, result.ok, result.code)
    return JSONResponse(
        content={"connection": _status_json(item), "test": _test_json(result)},
        headers=NO_STORE,
    )


@router.post("/{provider}/test")
async def test_connection(
    provider: str, principal: Caller, service: Service, limiter: Limiter
) -> JSONResponse:
    await _limit(principal, limiter, write=True)
    try:
        result = await service.test(principal.workspace_id, principal.user_id, provider)
        item = await service.status_of(principal.workspace_id, principal.user_id, provider)
    except UnknownProvider:
        return _unknown(provider)
    logger.info("connection_tested provider=%s ok=%s code=%s", provider, result.ok, result.code)
    return JSONResponse(
        content={"connection": _status_json(item), "test": _test_json(result)},
        headers=NO_STORE,
    )


@router.delete("/{provider}")
async def disconnect(
    provider: str, principal: Caller, service: Service, limiter: Limiter
) -> JSONResponse:
    await _limit(principal, limiter, write=True)
    try:
        await service.disconnect(principal.workspace_id, principal.user_id, provider)
        item = await service.status_of(principal.workspace_id, principal.user_id, provider)
    except UnknownProvider:
        return _unknown(provider)
    logger.info("connection_removed provider=%s", provider)
    return JSONResponse(content={"connection": _status_json(item)}, headers=NO_STORE)
