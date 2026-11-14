"""WebSocket voice endpoint (WS /api/v1/voice/live), protocol v2 (backward compatible).

The socket is accepted first and then authenticated, so a rejected client receives a real close
code (4401 unauthenticated, 4403 forbidden origin, 4429 too many sockets, 4503 unavailable)
instead of an opaque handshake failure. The token comes from the optional first ``HELLO`` frame
(preferred), the ``?token=`` query parameter, or an ``Authorization`` header. This module never
logs the URL, query string or token. The conversation logic lives in ``app.voice.connection``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, WebSocket
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.models import Principal
from app.auth.service import AuthService
from app.core.clock import SystemClock
from app.core.ratelimit import RedisRateLimiter
from app.db.repositories.user_settings import UserSettingsStore
from app.mail.config import mailbox_from_settings
from app.mail.service import MailService
from app.memory.chat_memory import ChatMemoryService, engine_store_scope
from app.search.factory import build_web_search
from app.services.artifact_service import ArtifactService
from app.services.chat_repo import ChatRepoTools
from app.services.image_gen import build_image_service
from app.services.task_submission import TaskSubmissionService
from app.tasks.queue import SubtaskQueue
from app.voice import protocol
from app.voice.connection import VoiceConnection, VoiceDeps, build_models
from app.voice.persistence import VoicePersistenceService
from app.voice.session_manager import VoiceSessionManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])


def _bearer_from_headers(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


async def _authenticate(websocket: WebSocket, token: str) -> Principal | None:
    auth_service: AuthService | None = getattr(websocket.app.state, "auth_service", None)
    if auth_service is None:
        logger.warning("voice_websocket_auth_service_not_configured")
        return None
    try:
        return await auth_service.authenticate(token)
    except Exception as err:
        logger.warning("voice_websocket_auth_failed type=%s", type(err).__name__)
        return None


async def _reject(websocket: WebSocket, code: int, reason: str, error_code: str) -> None:
    try:
        await websocket.send_json(protocol.error(error_code, reason, fatal=True))
        await websocket.close(code=code, reason=reason)
    except Exception:
        pass


def _parse_uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (ValueError, TypeError):
        return None


def _app_object(app: Any, name: str, factory: Any) -> Any:
    existing = getattr(app.state, name, None)
    if existing is None:
        existing = factory()
        setattr(app.state, name, existing)
    return existing


async def _voice_mail(app: Any, settings: Any, principal: Principal) -> MailService | None:
    """The caller's mailbox for the voice READ tools: their own saved one, else the server's,
    else None (unset or switched off). Never another user's."""
    connections = getattr(app.state, "connection_service", None)
    if connections is not None:
        try:
            return cast(
                "MailService | None",
                await connections.mail_service(principal.workspace_id, principal.user_id),
            )
        except Exception as error:  # noqa: BLE001 - a mailbox problem must not end the call
            logger.warning("voice_mailbox_failed error=%s", type(error).__name__)
            return None
    config = mailbox_from_settings(settings) if settings is not None else None
    return MailService(config) if config is not None else None


def _voice_memory(app: Any, resources: Any, principal: Principal, model_gateway: Any) -> Any:
    """The caller's memory service (the chat one), or ``None`` without a database or a model client.
    ``app.state.voice_memory`` is the explicit test hook."""
    override = getattr(app.state, "voice_memory", None)
    if override is not None:
        return override
    engine = getattr(resources, "engine", None)
    if not isinstance(engine, AsyncEngine) or model_gateway is None:
        return None
    if getattr(model_gateway, "client", None) is None:
        return None
    return ChatMemoryService(
        store_scope=engine_store_scope(engine, principal.workspace_id, principal.user_id),
        embedder=model_gateway,
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
    )


def _voice_preferences(app: Any, resources: Any) -> Any:
    """The per-user settings store (``memory_in_chat`` covers chat and voice)."""
    override = getattr(app.state, "voice_preferences", None)
    if override is not None:
        return override
    engine = getattr(resources, "engine", None)
    return UserSettingsStore(engine) if isinstance(engine, AsyncEngine) else None


def _voice_web_search(app: Any, settings: Any, model_gateway: Any) -> Any:
    """The chat web search service (one per process, shared with chat), so voice searches with
    the same providers, page reading and Gemini fallback. ``None`` without a model client or
    settings: then web_search is not declared."""
    override = getattr(app.state, "voice_web_search", None)
    if override is not None:
        return override
    if model_gateway is None or getattr(model_gateway, "client", None) is None:
        return None
    service = getattr(app.state, "web_search_service", None)
    if service is None and settings is not None:
        service, _research = build_web_search(settings, model_gateway)
        app.state.web_search_service = service
    return service


def _voice_task_submitter(app: Any, resources: Any) -> Any:
    """The shared task submission service (what chat uses), so voice can propose approval-gated
    Notion writes. ``None`` without a database."""
    override = getattr(app.state, "voice_task_submitter", None)
    if override is not None:
        return override
    engine = getattr(resources, "engine", None)
    if not isinstance(engine, AsyncEngine):
        return None
    queue = getattr(resources, "subtask_queue", None)
    return TaskSubmissionService(
        engine=engine,
        gateway=getattr(resources, "model_gateway", None),
        queue=queue if isinstance(queue, SubtaskQueue) else None,
    )


@router.websocket("/live")
async def voice_live_websocket(websocket: WebSocket) -> None:
    """Bidirectional voice conversation connected to Gemini Live and the chat tables."""
    await websocket.accept()
    app = websocket.app

    settings = getattr(app.state, "settings", None)
    extra_origins = getattr(settings, "voice_allowed_origins", None)
    if not isinstance(extra_origins, (list, tuple, set, frozenset)):
        extra_origins = protocol.VOICE_ALLOWED_ORIGINS
    if not protocol.origin_allowed(
        websocket.headers.get("origin"), websocket.headers.get("host"), extra_origins
    ):
        await _reject(
            websocket, protocol.CLOSE_FORBIDDEN_ORIGIN, "forbidden_origin", "forbidden_origin"
        )
        return

    token = websocket.query_params.get("token") or _bearer_from_headers(websocket)
    protocol_version = 2 if websocket.query_params.get("protocol") == "2" else 1
    requested_session = _parse_uuid(websocket.query_params.get("session_id"))
    voice_scope = protocol.normalize_scope(websocket.query_params.get("scope"))

    if not token:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), protocol.HELLO_TIMEOUT_SECONDS)
            hello = json.loads(raw)
            if not isinstance(hello, dict) or hello.get("type") != "HELLO":
                raise ValueError
        except Exception:
            await _reject(
                websocket, protocol.CLOSE_UNAUTHENTICATED, "unauthenticated", "unauthenticated"
            )
            return
        token = hello.get("token") if isinstance(hello.get("token"), str) else None
        protocol_version = 2 if hello.get("protocol") == 2 else protocol_version
        requested_session = _parse_uuid(hello.get("session_id")) or requested_session
        if "scope" in hello:
            voice_scope = protocol.normalize_scope(hello.get("scope"))

    principal = await _authenticate(websocket, token) if token else None
    if principal is None:
        await _reject(
            websocket, protocol.CLOSE_UNAUTHENTICATED, "unauthenticated", "unauthenticated"
        )
        return

    limiter: protocol.ConnectionLimiter = _app_object(
        app, "voice_limiter", protocol.ConnectionLimiter
    )
    if not await limiter.acquire(principal.user_id):
        await _reject(websocket, protocol.CLOSE_TOO_MANY, "too_many_sessions", "too_many_sessions")
        return

    try:
        resources = getattr(app.state, "resources", None)
        redis_client = getattr(resources, "redis", None)
        model_gateway = getattr(resources, "model_gateway", None)
        key_pool = getattr(resources, "key_pool", None) or getattr(model_gateway, "key_pool", None)
        if key_pool and not key_pool.is_empty:
            initial_k, _ = key_pool.get_next()
            api_key_str = initial_k.get_secret_value()
        else:
            key = getattr(settings, "gemini_api_key", None)
            api_key_str = key.get_secret_value() if key is not None else ""

        deps = VoiceDeps(
            persistence=getattr(app.state, "voice_persistence", None)
            or VoicePersistenceService(getattr(resources, "engine", None)),
            session_manager=_app_object(
                app, "voice_session_manager", lambda: VoiceSessionManager(redis_client=redis_client)
            ),
            redis=redis_client,
            model_gateway=model_gateway,
            queue=getattr(resources, "subtask_queue", None),
            api_key=api_key_str,
            models=build_models(resources, settings),
            # Explicit integration/test hook; never chosen implicitly or from configuration.
            connector=getattr(app.state, "voice_live_connector", None),
            key_pool=key_pool,
            task_announce=bool(getattr(settings, "voice_task_announce", True)),
            task_submitter=_voice_task_submitter(app, resources),
            memory=_voice_memory(app, resources, principal, model_gateway),
            preferences=_voice_preferences(app, resources),
            mail=await _voice_mail(app, settings, principal),
            web_search=_voice_web_search(app, settings, model_gateway),
            repo_tools=ChatRepoTools(
                getattr(app.state, "connection_service", None),
                principal.workspace_id,
                principal.user_id,
            ),
            images=(
                build_image_service(
                    settings,
                    gateway=model_gateway,
                    artifacts=ArtifactService(storage_root=settings.artifacts_dir),
                    limiter=RedisRateLimiter(redis_client, SystemClock()) if redis_client else None,
                )
                if settings is not None
                else None
            ),
        )
        await VoiceConnection(
            websocket,
            principal,
            deps,
            protocol_version=protocol_version,
            requested_session_id=requested_session,
            voice_scope=voice_scope,
            idle_timeout=getattr(app.state, "voice_idle_timeout", protocol.IDLE_TIMEOUT_SECONDS),
            max_duration=getattr(app.state, "voice_max_duration", protocol.MAX_DURATION_SECONDS),
        ).run()
    finally:
        await limiter.release(principal.user_id)
