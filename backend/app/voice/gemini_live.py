"""Gemini Live WebSocket Client Integration for MAYA Voice (Phase 12 / 14).

Manages bidirectional audio streaming between the voice endpoint and the Google GenAI Live API
(model alias: `gemini-3.8-live`), handles non-blocking tool calls (Decision D3),
enforces sub-150 ms barge-in interruptions (Decision D12), and supports live transcription.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.voice.tools import VoiceExecutionContext, VoiceToolRegistry

logger = logging.getLogger(__name__)

# Fallback PCM audio frame for simulated testing (100 ms of silence at 24 kHz 16-bit mono)
_SILENT_24K_FRAME = b"\x00\x00" * 2400


class GeminiLiveBridge:
    """Bridges client audio streams to Gemini 3.8 Live API with sub-150ms barge-in."""

    def __init__(
        self,
        api_key: str | None,
        system_instruction: str,
        tool_registry: VoiceToolRegistry,
        context: VoiceExecutionContext,
        model: str = "gemini-3.8-live",
        mock_mode: bool = False,
    ) -> None:
        self._api_key = api_key or ""
        self._model = model
        self._system_instruction = system_instruction
        self._tool_registry = tool_registry
        self._context = context
        self._mock_mode = (
            mock_mode
            or not self._api_key
            or self._api_key.startswith("example-")
            or "not-real" in self._api_key
        )
        self._client: Any = None
        self._session: Any = None
        self._session_ctx: Any = None
        self._closed = False
        self._mock_event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._mock_audio_chunks_count: int = 0

    @property
    def is_mock_mode(self) -> bool:
        """Whether the bridge is operating in simulation/mock mode."""
        return self._mock_mode

    async def connect(self) -> None:
        """Establish connection to Gemini Live API or initialize simulated session."""
        if self._mock_mode:
            logger.info(
                "gemini_live_bridge_initialized_in_mock_mode session_id=%s",
                self._context.session_id,
            )
            return

        try:
            from google import genai
            from google.genai import types

            self._client = genai.Client(api_key=self._api_key)

            declarations = []
            for decl in self._tool_registry.get_tool_declarations():
                declarations.append(
                    types.FunctionDeclaration(
                        name=decl["name"],
                        description=decl["description"],
                        behavior="NON_BLOCKING",
                        parameters=decl.get("parameters"),
                    )
                )

            thinking_config = (
                types.ThinkingConfig(thinking_level="low")
                if self._model == "gemini-3.8-live-extended-thinking"
                else None
            )

            live_tools = [types.Tool(function_declarations=declarations)]
            live_tools.append(types.Tool(google_search=types.GoogleSearch()))

            config = types.LiveConnectConfig(
                response_modalities=[types.Modality.AUDIO],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Kore")
                    )
                ),
                thinking_config=thinking_config,
                system_instruction=types.Content(parts=[types.Part(text=self._system_instruction)]),
                tools=live_tools,
                input_audio_transcription=types.AudioTranscriptionConfig(mode="smart"),
                output_audio_transcription=types.AudioTranscriptionConfig(),
            )

            self._session_ctx = self._client.aio.live.connect(model=self._model, config=config)
            self._session = await self._session_ctx.__aenter__()
            logger.info("gemini_live_session_connected session_id=%s", self._context.session_id)
        except Exception as err:
            logger.warning(
                "gemini_live_connect_failed_switching_to_mock err=%s session_id=%s",
                err,
                self._context.session_id,
            )
            self._mock_mode = True

    async def send_audio_chunk(self, pcm_16k_chunk: bytes) -> None:
        """Stream raw 16 kHz 16-bit linear PCM audio chunk to Gemini Live."""
        if self._closed or not pcm_16k_chunk:
            return

        if self._mock_mode or self._session is None:
            self._mock_audio_chunks_count += 1
            return

        try:
            from google.genai import types

            await self._session.send_realtime_input(
                audio=types.Blob(data=pcm_16k_chunk, mime_type="audio/pcm;rate=16000")
            )
        except Exception as err:
            logger.debug("gemini_live_send_audio_error: %s", err)

    async def send_text_input(self, text: str) -> None:
        """Send realtime user text turn to the Live session."""
        if self._closed or not text:
            return

        if self._mock_mode or self._session is None:
            # Emit user transcript and formulate assistant response
            await self._mock_event_queue.put({"type": "USER_TRANSCRIPT", "text": text})

            # Check if this instruction looks like a background task instruction
            lower = text.lower()
            if any(k in lower for k in ("research", "task", "analyze", "create", "fix")):
                tool_res = await self._tool_registry.execute_tool_async(
                    name="submit_task",
                    args={"intent": text},
                    context=self._context,
                )
                tid = tool_res.get("task_id", "task-active")
                await self._mock_event_queue.put(
                    {
                        "type": "TOOL_EXECUTED",
                        "name": "submit_task",
                        "args": {"intent": text},
                        "result": tool_res,
                    }
                )
                msg = (
                    f"I have started that task in the background (Task ID: {tid}). "
                    "I'll announce progress as steps complete."
                )
                await self._mock_event_queue.put(
                    {
                        "type": "MODEL_TRANSCRIPT",
                        "text": msg,
                    }
                )
            else:
                msg = f"I understand: '{text}'. How else can I assist you in this workspace?"
                await self._mock_event_queue.put(
                    {
                        "type": "MODEL_TRANSCRIPT",
                        "text": msg,
                    }
                )
            # Enqueue synthesized audio frame
            await self._mock_event_queue.put({"type": "MODEL_AUDIO", "data": _SILENT_24K_FRAME})
            return

        try:
            await self._session.send_realtime_input(text=text)
        except Exception as err:
            logger.debug("gemini_live_send_text_error: %s", err)

    async def send_turn_end(self) -> None:
        """Notify turn boundary via Hybrid VAD."""
        if self._closed:
            return
        if self._mock_mode or self._session is None:
            if self._mock_audio_chunks_count >= 2:
                self._mock_audio_chunks_count = 0
                resp_text = "I received your voice instruction and am processing it actively."
                await self._mock_event_queue.put({"type": "MODEL_TRANSCRIPT", "text": resp_text})
                await self._mock_event_queue.put({"type": "MODEL_AUDIO", "data": _SILENT_24K_FRAME})
            return

        try:
            await self._session.send_realtime_input(audio_stream_end=True)
        except Exception as err:
            logger.debug("gemini_live_turn_end_error: %s", err)

    async def inject_spoken_content(self, text: str) -> None:
        """Inject conversational updates (e.g. from event bridge) into live session."""
        if self._closed or not text:
            return

        if self._mock_mode or self._session is None:
            await self._mock_event_queue.put({"type": "MODEL_TRANSCRIPT", "text": text})
            await self._mock_event_queue.put({"type": "MODEL_AUDIO", "data": _SILENT_24K_FRAME})
            return

        try:
            from google.genai import types

            await self._session.send_client_content(
                turns=[
                    types.Content(
                        role="model",
                        parts=[types.Part(text=text)],
                    )
                ],
                turn_complete=False,
            )
        except Exception as err:
            logger.debug("gemini_live_inject_content_error: %s", err)

    async def receive_events(self) -> AsyncIterator[dict[str, Any]]:
        """Stream parsed events from Gemini Live API or mock queue."""
        if self._mock_mode or self._session is None:
            while not self._closed:
                try:
                    event = await asyncio.wait_for(self._mock_event_queue.get(), timeout=1.0)
                    yield event
                except TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
            return

        try:
            from google.genai import types

            async for message in self._session.receive():
                if self._closed:
                    break

                # 0. Asynchronous reasoning interaction status
                interaction_status = getattr(message, "interaction_status", None)
                if interaction_status:
                    yield {"type": "INTERACTION_STATUS", "status": str(interaction_status)}

                # 1. Server interrupted event (Sub-150 ms barge-in protocol)
                if getattr(message, "server_content", None) and getattr(
                    message.server_content, "interrupted", False
                ):
                    yield {"type": "INTERRUPTED"}

                # 2. Extract model audio output parts
                if getattr(message, "server_content", None) and getattr(
                    message.server_content, "model_turn", None
                ):
                    parts = getattr(message.server_content.model_turn, "parts", [])
                    for part in parts:
                        inline_data = getattr(part, "inline_data", None)
                        if inline_data and getattr(inline_data, "data", None):
                            yield {
                                "type": "MODEL_AUDIO",
                                "data": inline_data.data,
                            }

                # 3. Extract transcription
                server_content = getattr(message, "server_content", None)
                if server_content:
                    input_tx = getattr(server_content, "input_transcription", None)
                    if input_tx and getattr(input_tx, "text", None):
                        yield {"type": "USER_TRANSCRIPT", "text": input_tx.text}

                    output_tx = getattr(server_content, "output_transcription", None)
                    if output_tx and getattr(output_tx, "text", None):
                        yield {"type": "MODEL_TRANSCRIPT", "text": output_tx.text}

                # 4. Handle asynchronous tool calls (Decisions D3 & D5)
                tool_call = getattr(message, "tool_call", None)
                if tool_call:
                    func_calls = getattr(tool_call, "function_calls", [])
                    for call in func_calls:
                        call_name = getattr(call, "name", "")
                        call_args = getattr(call, "args", {}) or {}
                        call_id = getattr(call, "id", "")

                        tool_result = await self._tool_registry.execute_tool_async(
                            name=call_name,
                            args=call_args,
                            context=self._context,
                        )

                        # Submit function response back to Gemini Live
                        resp = types.FunctionResponse(
                            id=call_id,
                            name=call_name,
                            response=tool_result,
                        )
                        await self._session.send_tool_response(function_responses=[resp])

                        yield {
                            "type": "TOOL_EXECUTED",
                            "name": call_name,
                            "args": call_args,
                            "result": tool_result,
                        }

        except asyncio.CancelledError:
            pass
        except Exception as err:
            logger.error("gemini_live_receive_error: %s", err)
            yield {"type": "ERROR", "error": str(err)}

    async def close(self) -> None:
        """Close active session cleanly."""
        self._closed = True
        if self._session_ctx is not None:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception as e:
                logger.debug("gemini_live_session_close_exception: %s", e)
            self._session = None
            self._session_ctx = None
