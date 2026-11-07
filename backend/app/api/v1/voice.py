"""WebSocket Voice Bridge Endpoint (Phase 12 / 14, WS /api/v1/voice/live).

Bidirectional linear PCM streaming, real-time Gemini Live AI processing,
PostgreSQL session & transcript persistence, and decoupled background task execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.auth.models import Principal
from app.auth.service import AuthService
from app.voice.event_bridge import VoiceEventBridge
from app.voice.gemini_live import GeminiLiveBridge
from app.voice.live_session import (
    LiveVoiceSession,
    OutgoingMessage,
    OutgoingMessageType,
)
from app.voice.persistence import VoicePersistenceService
from app.voice.session_manager import VoiceSessionManager
from app.voice.task_wiring import wire_voice_tools
from app.voice.tools import VoiceExecutionContext, VoiceToolRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

VOICE_SYSTEM_INSTRUCTION = (
    "You are MAYA, an autonomous AI assistant and digital employee for this workspace. "
    "You communicate via real-time spoken voice.\n"
    "Rules for your speech:\n"
    "1. Speak naturally, warmly, and concisely (1 to 3 sentences per response).\n"
    "2. Never use Markdown syntax, asterisks, bullet points, numbered lists, or tables.\n"
    "3. You have tools: 'submit_task' to start autonomous multi-agent background tasks, "
    "and 'get_task_status'. When a user asks to research, build, analyze, or execute complex work, "
    "call 'submit_task' immediately.\n"
    "4. Voice Approval Shielding (Decision D5): Sensitive operations require approval from the "
    "secure web dashboard. You cannot accept verbal approvals."
)


async def _authenticate_websocket(websocket: WebSocket) -> Principal | None:
    """Extract and authenticate the bearer token from query params or Authorization header."""
    token = websocket.query_params.get("token")
    if not token:
        headers = websocket.headers
        auth_header = headers.get("authorization") or headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()

    if not token:
        logger.warning("voice_websocket_missing_token")
        return None

    auth_service: AuthService | None = getattr(websocket.app.state, "auth_service", None)
    if auth_service is None:
        logger.warning("voice_websocket_auth_service_not_configured")
        return None

    try:
        principal = await auth_service.authenticate(token)
        return principal
    except Exception as err:
        logger.warning("voice_websocket_auth_failed: %s", err)
        return None


@router.websocket("/live")
async def voice_live_websocket(websocket: WebSocket) -> None:
    """Bidirectional streaming audio WebSocket endpoint connected to Gemini Live and PostgreSQL."""
    principal = await _authenticate_websocket(websocket)
    if principal is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    # Extract application resources and configuration
    resources = getattr(websocket.app.state, "resources", None)
    settings = getattr(websocket.app.state, "settings", None)
    db_engine = getattr(resources, "engine", None)
    redis_client = getattr(resources, "redis", None)
    model_gateway = getattr(resources, "model_gateway", None)
    api_key_secret = getattr(settings, "gemini_api_key", None)
    gemini_key = api_key_secret.get_secret_value() if api_key_secret is not None else ""

    # 1. Resolve or create persistent database session in PostgreSQL (channel='voice')
    persistence = VoicePersistenceService(db_engine)
    client_session_param = websocket.query_params.get("session_id")
    target_uuid: UUID | None = None
    if client_session_param:
        with contextlib.suppress(ValueError, TypeError):
            target_uuid = UUID(client_session_param)

    persisted_session_id = await persistence.get_or_create_voice_session(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        session_id=target_uuid,
    )
    session_id_str = str(persisted_session_id)

    logger.info(
        "voice_websocket_connected workspace_id=%s user_id=%s session_id=%s",
        principal.workspace_id,
        principal.user_id,
        session_id_str,
    )

    # 2. Setup Tool Registry & Background Task Event Bridge (Decisions D3 & D5)
    tool_registry = VoiceToolRegistry()
    bridge = VoiceEventBridge(workspace_id=principal.workspace_id, redis_client=redis_client)
    await bridge.start_listening()

    wire_voice_tools(
        tool_registry=tool_registry,
        event_bridge=bridge,
        db_engine=db_engine,
        model_gateway=model_gateway,
        queue=getattr(resources, "subtask_queue", None),
    )

    context = VoiceExecutionContext(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        session_id=session_id_str,
    )

    # 3. Setup Live Voice Session & Upstream Gemini Bridge
    session = LiveVoiceSession(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        session_id=session_id_str,
        tool_registry=tool_registry,
        model_gateway=model_gateway,
        mock_mode=False,
    )

    gemini_bridge = GeminiLiveBridge(
        api_key=gemini_key,
        system_instruction=VOICE_SYSTEM_INSTRUCTION,
        tool_registry=tool_registry,
        context=context,
        model="gemini-3.8-live",
        mock_mode=False,
    )
    await gemini_bridge.connect()

    session_manager = VoiceSessionManager(redis_client=redis_client)
    await session_manager.store_resumption_token(
        principal.workspace_id, session_id_str, f"resumption-init-{session_id_str}"
    )

    # Transcript buffers for persistence into PostgreSQL
    user_transcript_accumulator: list[str] = []
    model_transcript_accumulator: list[str] = []

    sender_task: asyncio.Task[None] | None = None
    gemini_rx_task: asyncio.Task[None] | None = None
    bridge_task: asyncio.Task[None] | None = None

    async def _send_loop() -> None:
        """Forward outgoing audio frames and JSON control messages to client WebSocket."""
        while True:
            try:
                msg: OutgoingMessage = await session.get_next_outgoing_message()
                if msg.msg_type == OutgoingMessageType.AUDIO:
                    await websocket.send_bytes(msg.audio_bytes)
                elif msg.msg_type == OutgoingMessageType.CONTROL:
                    await websocket.send_json(msg.payload)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("voice_sender_error: %s", e)
                break

    async def _gemini_rx_loop() -> None:
        """Stream real-time audio and transcription events from Gemini Live Bridge."""
        nonlocal user_transcript_accumulator, model_transcript_accumulator
        async for event in gemini_bridge.receive_events():
            ev_type = event.get("type")

            if ev_type == "MODEL_AUDIO":
                await session.enqueue_model_audio(event["data"])

            elif ev_type == "USER_TRANSCRIPT":
                text = event.get("text", "")
                if text:
                    user_transcript_accumulator.append(text)
                    await session.emit_transcript_user(text)

            elif ev_type == "MODEL_TRANSCRIPT":
                text = event.get("text", "")
                if text:
                    model_transcript_accumulator.append(text)
                    await session.emit_transcript_model(text)

            elif ev_type == "INTERRUPTED":
                # Decision D12: Sub-150 ms Barge-in interruption
                await session.handle_interrupt(source="gemini_interrupted")

            elif ev_type == "TOOL_EXECUTED":
                tool_res = event.get("result", {})
                task_id = tool_res.get("task_id")
                tool_args = event.get("args", {})
                intent_val = tool_args.get("intent") if isinstance(tool_args, dict) else None
                msg = OutgoingMessage(
                    OutgoingMessageType.CONTROL,
                    payload={
                        "type": "TASK_STATUS",
                        "task_id": task_id,
                        "status": tool_res.get("status", "pending"),
                        "intent": intent_val or event.get("name", "task"),
                        "tool_name": event.get("name", "submit_task"),
                        "args": tool_args,
                        "result": tool_res,
                    },
                )
                await session._outgoing_queue.put(msg)

    async def _bridge_loop() -> None:
        """Inject spoken background task announcements into the live voice stream."""
        while True:
            try:
                announcement = await bridge.get_next_announcement()
                await gemini_bridge.inject_spoken_content(announcement.spoken_text)
                await session.send_client_content(announcement.spoken_text)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("voice_bridge_poller_error: %s", e)
                break

    sender_task = asyncio.create_task(_send_loop())
    gemini_rx_task = asyncio.create_task(_gemini_rx_loop())
    bridge_task = asyncio.create_task(_bridge_loop())

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            # 1. Binary frame = client PCM16 microphone audio
            raw_bytes = message.get("bytes")
            if raw_bytes:
                await session.process_client_audio(raw_bytes)
                await gemini_bridge.send_audio_chunk(raw_bytes)

            # 2. Text frame = JSON control frame or conversational text turn
            raw_text = message.get("text")
            if raw_text:
                try:
                    payload = json.loads(raw_text)
                    msg_type = payload.get("type")

                    if msg_type == "USER_TEXT":
                        text_val = payload.get("text", "")
                        if text_val:
                            user_transcript_accumulator.append(text_val)
                            await gemini_bridge.send_text_input(text_val)

                    elif msg_type == "CLIENT_INTERRUPT":
                        await session.handle_interrupt(source="client_interrupt")

                    elif msg_type == "VAD_SPEECH_START":
                        if session.is_speaking or session.pending_audio_chunks_count > 0:
                            await session.handle_interrupt(source="client_vad_speech_start")

                    elif msg_type == "VAD_SPEECH_STOP":
                        session.is_interrupted = False
                        await gemini_bridge.send_turn_end()

                    else:
                        await session.handle_client_control_message(payload)
                except Exception as parse_err:
                    logger.warning("invalid_json_control_message: %s", parse_err)

    except WebSocketDisconnect:
        logger.info("voice_websocket_client_disconnected session_id=%s", session_id_str)
    except Exception as e:
        logger.error("voice_websocket_unexpected_error: %s", e)
    finally:
        # Cancel background async tasks
        for t in (sender_task, gemini_rx_task, bridge_task):
            if t is not None:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t

        # Persist completed speech turns to PostgreSQL messages table under RLS
        if user_transcript_accumulator:
            await persistence.save_turn(
                workspace_id=principal.workspace_id,
                user_id=principal.user_id,
                session_id=persisted_session_id,
                sender="user",
                content=" ".join(user_transcript_accumulator),
            )
        if model_transcript_accumulator:
            await persistence.save_turn(
                workspace_id=principal.workspace_id,
                user_id=principal.user_id,
                session_id=persisted_session_id,
                sender="orchestrator",
                content=" ".join(model_transcript_accumulator),
            )

        await gemini_bridge.close()
        await bridge.close()
        await session.close()
        logger.info("voice_websocket_cleaned_up session_id=%s", session_id_str)
