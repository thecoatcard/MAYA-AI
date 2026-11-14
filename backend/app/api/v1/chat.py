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
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.agents.specialized.research import ResearchAgent
from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.core.chat_streams import ChatStreamLimiter, RedisChatStreamLimiter
from app.core.clock import SystemClock
from app.core.config import Settings
from app.core.ratelimit import RateLimiter, RedisRateLimiter
from app.db.repositories.user_settings import UserSettingsStore
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import ModelRegistry
from app.memory.chat_memory import ChatMemory, ChatMemoryService, engine_store_scope
from app.schemas.chat import (
    ChatMessageRequest,
    MessageFeedbackIn,
    MessageOut,
    Page,
    SessionDuplicateIn,
    SessionOut,
    SessionPatchIn,
)
from app.search.factory import build_web_search
from app.services.chat_idempotency import IdempotencyStore, RedisIdempotencyStore
from app.services.chat_repo import ChatRepoTools
from app.services.chat_service import (
    ChatPreferences,
    ChatService,
    ChatTurn,
    parse_idempotency_key,
)
from app.services.document_tools import JobReader
from app.services.job_status import TaskJobReader
from app.services.loop_tools import GroundedSearch
from app.services.task_submission import TaskSubmissionService, TaskSubmitter
from app.tasks.queue import SubtaskQueue

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


async def get_task_submitter(request: Request) -> TaskSubmitter | None:
    """The task submitter for the chat ``submit_task`` tool; ``None`` without a database."""
    resources = getattr(request.app.state, "resources", None)
    engine = getattr(resources, "engine", None)
    if not isinstance(engine, AsyncEngine):
        return None
    queue = getattr(resources, "subtask_queue", None)
    return TaskSubmissionService(
        engine=engine,
        gateway=getattr(resources, "model_gateway", None),
        queue=queue if isinstance(queue, SubtaskQueue) else None,
    )


async def get_chat_preferences(request: Request) -> ChatPreferences | None:
    """The caller's stored chat preferences; ``None`` without a database."""
    engine = getattr(getattr(request.app.state, "resources", None), "engine", None)
    return UserSettingsStore(engine) if isinstance(engine, AsyncEngine) else None


async def get_chat_memory(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> ChatMemory | None:
    """Save, recall and forget for the caller; ``None`` without a database or a model client."""
    resources = getattr(request.app.state, "resources", None)
    engine = getattr(resources, "engine", None)
    gateway = getattr(resources, "model_gateway", None)
    if not isinstance(engine, AsyncEngine) or gateway is None or gateway.client is None:
        return None
    return ChatMemoryService(
        store_scope=engine_store_scope(engine, principal.workspace_id, principal.user_id),
        embedder=gateway,
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
    )


async def get_chat_repo_tools(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> ChatRepoTools:
    """Read-only GitHub tools for the caller: their own connection, or public repos without one."""
    connections = getattr(request.app.state, "connection_service", None)
    return ChatRepoTools(connections, principal.workspace_id, principal.user_id)


async def get_job_reader(request: Request) -> JobReader | None:
    """Reads a chat job's status from the task rows (check_job); ``None`` without a database."""
    engine = getattr(getattr(request.app.state, "resources", None), "engine", None)
    return TaskJobReader(engine) if isinstance(engine, AsyncEngine) else None


async def get_web_search(request: Request) -> GroundedSearch | None:
    """The research agent's grounded search for the loop's web_search tool (ADR-0026, Phase 2).
    The same call the research step makes; ``None`` without a model client."""
    gateway = getattr(getattr(request.app.state, "resources", None), "model_gateway", None)
    if gateway is None or getattr(gateway, "client", None) is None:
        return None
    # The native search stack (providers, page reading); Gemini grounding is its last fallback.
    service = getattr(request.app.state, "web_search_service", None)
    if service is None:
        settings = getattr(request.app.state, "settings", None)
        if settings is None:
            return ResearchAgent(model_gateway=gateway)
        service, _research = build_web_search(settings, gateway)
        request.app.state.web_search_service = service
    return cast(GroundedSearch, service)


async def get_chat_service(
    principal: Annotated[Principal, Depends(get_principal)],
    database: Annotated[TenantDatabase, Depends(get_tenant_database)],
    gateway: Annotated[ModelGateway, Depends(get_model_gateway)],
    registry: Annotated[ModelRegistry, Depends(get_model_registry)],
    settings: Annotated[Settings, Depends(get_chat_settings)],
    limiter: Annotated[RateLimiter, Depends(get_chat_rate_limiter)],
    idempotency: Annotated[IdempotencyStore, Depends(get_idempotency_store)],
    stream_limiter: Annotated[ChatStreamLimiter, Depends(get_chat_stream_limiter)],
    task_submitter: Annotated[TaskSubmitter | None, Depends(get_task_submitter)],
    preferences: Annotated[ChatPreferences | None, Depends(get_chat_preferences)],
    memory: Annotated[ChatMemory | None, Depends(get_chat_memory)],
    repo_tools: Annotated[ChatRepoTools, Depends(get_chat_repo_tools)],
    job_reader: Annotated[JobReader | None, Depends(get_job_reader)],
    web_search: Annotated[GroundedSearch | None, Depends(get_web_search)],
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
        task_submitter=task_submitter,
        preferences=preferences,
        memory=memory,
        repo_tools=repo_tools,
        job_reader=job_reader,
        web_search=web_search,
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
        "description": (
            "Server-Sent Events: message.accepted, delta*, optionally task.created, then done "
            "or error."
        ),
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    },
    400: {"description": "idempotency_key_required"},
    401: {"description": "not_authenticated or invalid_token"},
    404: {"description": "session_not_found"},
    409: {"description": "request_in_progress"},
    422: {"description": "validation_error, message_too_long or idempotency_key_reused"},
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
    archived: Annotated[Literal["false", "true", "all"], Query()] = "false",
) -> Page[SessionOut]:
    """The caller's own sessions: pinned first, then newest first. Archived ones are hidden
    unless ``archived=true`` (only those) or ``archived=all``."""
    response.headers["Cache-Control"] = "no-store"  # private conversation data
    return await service.list_sessions(limit, cursor, archived)


@router.patch("/sessions/{session_id}", response_model=SessionOut)
async def patch_session(
    response: Response, session_id: UUID, body: SessionPatchIn, service: Service
) -> SessionOut:
    """Rename (``title``), pin (``pinned``) or archive (``archived``) one of the caller's
    sessions. Answers with the session as the list shows it. 404 for another user's id."""
    response.headers["Cache-Control"] = "no-store"
    return await service.patch_session(
        session_id, title=body.title, pinned=body.pinned, archived=body.archived
    )


@router.post("/sessions/{session_id}/duplicate", response_model=SessionOut, status_code=201)
async def duplicate_session(
    request: Request,
    response: Response,
    session_id: UUID,
    service: Service,
    body: SessionDuplicateIn | None = None,
) -> SessionOut:
    """Branch a conversation into a new session holding its plain text turns (up to and
    including ``from_message_id``). With an ``Idempotency-Key`` a repeat returns the same
    session."""
    response.headers["Cache-Control"] = "no-store"
    keys = request.headers.getlist("idempotency-key")
    key = parse_idempotency_key(keys) if keys else None
    return await service.duplicate_session(
        session_id, body.from_message_id if body is not None else None, key
    )


@router.get("/sessions/{session_id}/export")
async def export_session(
    session_id: UUID,
    service: Service,
    format: Annotated[Literal["md"], Query()] = "md",  # noqa: A002 - the documented query name
) -> Response:
    """The conversation as a Markdown download (``text/markdown``, attachment)."""
    name, markdown = await service.export_session(session_id)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.post("/messages/{message_id}/feedback", status_code=204)
async def message_feedback(message_id: UUID, body: MessageFeedbackIn, service: Service) -> Response:
    """Thumbs up or down on an assistant reply (``value: null`` clears it). Stored per user for
    later calibration; never fed into a prompt. 404 for an unknown or foreign message."""
    if not await service.set_message_feedback(message_id, body.value, body.note):
        raise HTTPException(status_code=404, detail={"error": "message_not_found"})
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


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


@router.get("/sessions/{session_id}/messages/{message_id}")
async def get_message_state(
    response: Response, session_id: UUID, message_id: UUID, service: Service
) -> dict[str, Any]:
    """The state of one assistant reply by id, for a client that left while it ran.

    ``{"state": "stored", "message": MessageOut}`` once the reply is stored (completed or
    interrupted; ``message.metadata.progress`` and ``finish_reason`` tell which), and
    ``{"state": "pending", "message": null}`` while it is not stored yet (still running, or an
    unknown id). Poll until ``stored``.
    """
    response.headers["Cache-Control"] = "no-store"
    found = await service.message_state(session_id, message_id)
    if found is None:
        return {"state": "pending", "message": None}
    return {"state": "stored", "message": found.model_dump(mode="json")}


@router.get("/sessions/{session_id}/messages/{message_id}/files/{file_id}")
async def download_chat_file(
    session_id: UUID,
    message_id: UUID,
    file_id: UUID,
    service: Service,
) -> FileResponse:
    """A file made in this chat (``file.created``): always an attachment download, served with
    ``nosniff``. 404 for any other session, message or file."""
    found = await service.chat_file(session_id, message_id, file_id)
    if found is None:
        if await service.chat_file_state(session_id, message_id, file_id) == "deleted":
            raise HTTPException(status_code=410, detail={"error": "file_deleted"})
        raise HTTPException(status_code=404, detail={"error": "file_not_found"})
    path, card = found
    return FileResponse(
        path=str(path),
        media_type=card.mime_type,
        filename=card.name,
        content_disposition_type="attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "X-Content-Sha256": card.sha256,
        },
    )


@router.delete("/sessions/{session_id}/messages/{message_id}/files/{file_id}", status_code=204)
async def delete_chat_file(
    session_id: UUID,
    message_id: UUID,
    file_id: UUID,
    service: Service,
) -> Response:
    """Delete MAYA's stored copy of a file made in this chat. The card stays and reads as
    deleted. 404 for any other session, message or file, and when it is already deleted."""
    if not await service.delete_chat_file(session_id, message_id, file_id):
        raise HTTPException(status_code=404, detail={"error": "file_not_found"})
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: UUID,
    service: Service,
    delete_files_and_tasks: Annotated[bool, Query()] = False,
) -> Response:
    """Delete one session and all its messages. 204 on success; 404 if not found.

    Its files and tasks are kept unless ``delete_files_and_tasks`` is true: then its finished
    tasks and their files, and the files made in the chat, are deleted too (unfinished tasks
    stay; the user's own working-folder copies are never touched)."""
    await service.delete_session(session_id)
    if delete_files_and_tasks:
        await service.delete_session_content(session_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
