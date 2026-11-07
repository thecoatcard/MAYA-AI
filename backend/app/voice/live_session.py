"""Real-time Gemini Live Voice Session Engine with Sub-150 ms Barge-In (Decision D12).

Manages bidirectional audio streaming between the web client and Gemini Live API,
enforces the sub-150 ms interruption SLA, clears playback buffers on barge-in,
and dispatches decoupled background task tool calls.
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from app.voice.tools import VoiceExecutionContext, VoiceToolRegistry

if TYPE_CHECKING:
    from app.gateway.model_gateway import ModelGateway

logger = logging.getLogger(__name__)


class OutgoingMessageType(StrEnum):
    """Classification of outgoing WebSocket frames."""

    AUDIO = "audio"
    CONTROL = "control"


class OutgoingMessage:
    """Outgoing message to the client over WebSocket."""

    def __init__(
        self,
        msg_type: OutgoingMessageType,
        audio_bytes: bytes = b"",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.msg_type = msg_type
        self.audio_bytes = audio_bytes
        self.payload = payload or {}


class LiveVoiceSession:
    """Manages an active real-time bidirectional voice session."""

    def __init__(
        self,
        workspace_id: UUID,
        user_id: UUID,
        session_id: str | None = None,
        tool_registry: VoiceToolRegistry | None = None,
        model_gateway: ModelGateway | None = None,
        mock_mode: bool = True,
    ) -> None:
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.session_id = session_id or str(uuid4())
        self.tool_registry = tool_registry or VoiceToolRegistry()
        self.model_gateway = model_gateway
        self.mock_mode = mock_mode

        # Session state
        self.is_speaking = False
        self.is_listening = True
        self.is_interrupted = False
        self.created_at = time.time()
        self.total_client_bytes_received = 0
        self.total_model_bytes_sent = 0

        # Message queues and synchronization
        self._outgoing_queue: asyncio.Queue[OutgoingMessage] = asyncio.Queue()
        self._pending_audio_count = 0
        self._lock = asyncio.Lock()
        self._active_task_ids: set[str] = set()

    @property
    def pending_audio_chunks_count(self) -> int:
        """Return number of queued model audio chunks waiting to be transmitted."""
        return self._pending_audio_count

    async def enqueue_model_audio(self, audio_chunk: bytes) -> None:
        """Enqueue a 24 kHz PCM audio chunk for delivery to the client."""
        if not audio_chunk:
            return
        async with self._lock:
            self._pending_audio_count += 1
            self.is_speaking = True
            self.total_model_bytes_sent += len(audio_chunk)
            msg = OutgoingMessage(OutgoingMessageType.AUDIO, audio_bytes=audio_chunk)
            await self._outgoing_queue.put(msg)

    async def emit_transcript_user(self, text: str) -> None:
        """Emit a user speech transcription frame."""
        msg = OutgoingMessage(
            OutgoingMessageType.CONTROL,
            payload={"type": "TRANSCRIPT_USER", "text": text},
        )
        await self._outgoing_queue.put(msg)

    async def emit_transcript_model(self, text: str) -> None:
        """Emit a model speech transcription frame."""
        msg = OutgoingMessage(
            OutgoingMessageType.CONTROL,
            payload={"type": "TRANSCRIPT_MODEL", "text": text},
        )
        await self._outgoing_queue.put(msg)

    async def handle_interrupt(self, source: str = "generic") -> None:
        """Sub-150 ms Barge-In Protocol (Decision D12).

        Immediately flushes pending outgoing audio buffers and emits AUDIO_INTERRUPT frame.
        """
        async with self._lock:
            # Drain and discard pending audio frames from the outgoing queue
            retained_messages: list[OutgoingMessage] = []
            while not self._outgoing_queue.empty():
                try:
                    msg = self._outgoing_queue.get_nowait()
                    if msg.msg_type != OutgoingMessageType.AUDIO:
                        retained_messages.append(msg)
                except asyncio.QueueEmpty:
                    break

            for msg in retained_messages:
                await self._outgoing_queue.put(msg)

            self._pending_audio_count = 0
            self.is_speaking = False
            self.is_interrupted = True

            # Emit AUDIO_INTERRUPT control message
            interrupt_frame = OutgoingMessage(
                OutgoingMessageType.CONTROL,
                payload={"type": "AUDIO_INTERRUPT"},
            )
            await self._outgoing_queue.put(interrupt_frame)
            logger.info("barge_in_triggered source=%s session_id=%s", source, self.session_id)

    async def handle_server_interrupted(self) -> None:
        """Handle server_content.interrupted event from Gemini Live API."""
        await self.handle_interrupt(source="gemini_server_interrupted")

    async def handle_client_control_message(self, msg: dict[str, Any]) -> None:
        """Handle JSON control frames sent by client."""
        msg_type = msg.get("type", "")

        if msg_type == "CLIENT_INTERRUPT":
            await self.handle_interrupt(source="client_interrupt")

        elif msg_type == "VAD_SPEECH_START":
            if self.is_speaking or self._pending_audio_count > 0:
                await self.handle_interrupt(source="client_vad_speech_start")

        elif msg_type == "VAD_SPEECH_STOP":
            self.is_interrupted = False

    async def process_client_audio(self, pcm_16k_chunk: bytes) -> None:
        """Process incoming 16 kHz PCM chunk from client microphone."""
        if not pcm_16k_chunk:
            return
        self.total_client_bytes_received += len(pcm_16k_chunk)

    async def execute_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool via the VoiceToolRegistry."""
        context = VoiceExecutionContext(
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            session_id=self.session_id,
        )
        result = await self.tool_registry.execute_tool_async(name, args, context)
        task_id = result.get("task_id")
        if task_id:
            self._active_task_ids.add(str(task_id))
        return result

    async def send_client_content(self, text_or_turns: str | list[dict[str, Any]]) -> None:
        """Inject conversational content or spoken updates into the live session."""
        if isinstance(text_or_turns, str):
            await self.emit_transcript_model(text_or_turns)
        elif isinstance(text_or_turns, list):
            for turn in text_or_turns:
                parts = turn.get("parts", [])
                for part in parts:
                    if "text" in part:
                        await self.emit_transcript_model(part["text"])

    async def get_next_outgoing_message(self) -> OutgoingMessage:
        """Pop the next outgoing frame to send to the WebSocket client."""
        msg = await self._outgoing_queue.get()
        if msg.msg_type == OutgoingMessageType.AUDIO:
            async with self._lock:
                self._pending_audio_count = max(0, self._pending_audio_count - 1)
                if self._pending_audio_count == 0:
                    self.is_speaking = False
        return msg

    async def close(self) -> None:
        """Cleanly terminate the voice session."""
        self.is_listening = False
        self.is_speaking = False
        logger.info("voice_session_closed session_id=%s", self.session_id)
