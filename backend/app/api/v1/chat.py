"""``/api/v1/chat``: send a message and stream the reply, list sessions, read messages.

The contract is ``docs/api/CHAT.md`` (ADR-0008). Authentication runs first, then the service does
the rest in the documented order. The request body is read and validated by the service rather
than by FastAPI, so that a missing token is always a ``401`` and a bad ``Idempotency-Key`` is
always a ``400``, whatever the body looks like.

Every collaborator is a small dependency function that reads ``request.app.state`` (``resources``
and ``settings``, both set by ``create_app``). Tests replace any of them with
``app.dependency_overrides``:
``get_model_gateway``, ``get_model_registry``, ``get_chat_settings``, ``get_chat_rate_limiter``,
``get_idempotency_store`` and, to swap everything at once, ``get_chat_service``.
"""

import asyncio
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.core.chat_streams import ChatStreamLimiter, RedisChatStreamLimiter
from app.core.clock import SystemClock
from app.core.config import Settings
from app.core.ratelimit import RateLimiter, RedisRateLimiter
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import ModelRegistry
from app.schemas.chat import ChatMessageRequest, MessageOut, Page, SessionOut
from app.services.chat_idempotency import IdempotencyStore, RedisIdempotencyStore
from app.services.chat_service import ChatService, ChatTurn

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

_SESSIONS_MAX_LIMIT = 50
_MESSAGES_MAX_LIMIT = 100


# ------------------------------------------------------------------------- dependency points


async def get_model_gateway(request: Request) -> ModelGateway:
    gateway: ModelGateway = request.app.state.resources.model_gateway
    return gateway


async def get_model_registry(request: Request) -> ModelRegistry:
    registry: ModelRegistry = request.app.state.resources.registry
    return registry


async def get_chat_settings(request: Request) -> Settings:
    """The settings the application was created with (``create_app`` stores them)."""
    settings: Settings = request.app.state.settings
    return settings


async def get_chat_rate_limiter(request: Request) -> RateLimiter:
    return RedisRateLimiter(request.app.state.resources.redis, SystemClock())


async def get_idempotency_store(request: Request) -> IdempotencyStore:
    return RedisIdempotencyStore(request.app.state.resources.redis)


async def get_chat_stream_limiter(request: Request) -> ChatStreamLimiter:
    return RedisChatStreamLimiter(request.app.state.resources.redis)


async def get_chat_service(
    principal: Annotated[Principal, Depends(get_principal)],
    database: Annotated[TenantDatabase, Depends(get_tenant_database)],
    gateway: Annotated[ModelGateway, Depends(get_model_gateway)],
    registry: Annotated[ModelRegistry, Depends(get_model_registry)],
    settings: Annotated[Settings, Depends(get_chat_settings)],
    limiter: Annotated[RateLimiter, Depends(get_chat_rate_limiter)],
    idempotency: Annotated[IdempotencyStore, Depends(get_idempotency_store)],
    stream_limiter: Annotated[ChatStreamLimiter, Depends(get_chat_stream_limiter)],
) -> ChatService:
    """The caller's chat service. ``get_principal`` is listed first, so a missing or invalid
    token is rejected before anything else is looked at."""
    return ChatService(
        principal=principal,
        database=database,
        gateway=gateway,
        registry=registry,
        settings=settings,
        limiter=limiter,
        idempotency=idempotency,
        stream_limiter=stream_limiter,
    )


Service = Annotated[ChatService, Depends(get_chat_service)]


# ------------------------------------------------------------------------------------ response


class EventStreamResponse(StreamingResponse):
    """``text/event-stream`` over a ``ChatTurn``.

    Starlette ends a streaming response by cancelling the task that sends it, which can leave the
    event generator suspended between two events with nobody left to resume or close it. This
    class closes the turn when the response is over, however it ended, so a reply that was cut
    short is always stored and its provider stream always closed before the request completes.
    """

    def __init__(self, turn: ChatTurn) -> None:
        super().__init__(
            turn.events(),
            media_type="text/event-stream",
            # no-transform stops a compressing proxy (the Next.js rewrite gzips by
            # default) from holding events back until its buffer fills.
            headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"},
        )
        self._turn = turn

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await asyncio.shield(self._turn.close())


# ------------------------------------------------------------------------------------- routes

_POST_OPENAPI: dict[str, Any] = {
    "parameters": [
        {
            "name": "Idempotency-Key",
            "in": "header",
            "required": True,
            "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]{8,128}$"},
        }
    ],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": ChatMessageRequest.model_json_schema()}},
    },
}
_POST_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Server-Sent Events: message.accepted, delta*, then done or error.",
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    },
    400: {"description": "idempotency_key_required"},
    401: {"description": "not_authenticated or invalid_token"},
    404: {"description": "session_not_found"},
    409: {"description": "request_in_progress"},
    422: {"description": "validation_error or idempotency_key_reused"},
    429: {"description": "rate_limited or concurrent_stream_limit (Retry-After header)"},
    503: {"description": "model_unavailable or chat_unavailable"},
}


@router.post(
    "/message",
    response_model=None,
    openapi_extra=_POST_OPENAPI,
    responses=_POST_RESPONSES,
)
async def post_message(request: Request, service: Service) -> EventStreamResponse:
    """Send one text message and stream the assistant's reply."""
    turn = await service.begin_message(
        idempotency_keys=request.headers.getlist("idempotency-key"),
        body=await request.body(),
    )
    return EventStreamResponse(turn)


@router.get("/sessions", response_model=Page[SessionOut])
async def list_sessions(
    response: Response,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=_SESSIONS_MAX_LIMIT)] = 20,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> Page[SessionOut]:
    """The caller's own sessions, newest first."""
    response.headers["Cache-Control"] = "no-store"  # private conversation data
    return await service.list_sessions(limit, cursor)


@router.get("/sessions/{session_id}/messages", response_model=Page[MessageOut])
async def list_messages(
    response: Response,
    session_id: UUID,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=_MESSAGES_MAX_LIMIT)] = 50,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> Page[MessageOut]:
    """The messages of one session, oldest first."""
    response.headers["Cache-Control"] = "no-store"  # private conversation data
    return await service.list_messages(session_id, limit, cursor)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: UUID,
    service: Service,
) -> Response:
    """Delete one session and all its messages. 204 on success; 404 if not found."""
    await service.delete_session(session_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
