"""A fake Gemini Live provider for voice tests. No network; messages are real SDK types."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from google.genai import types

PCM_24K = b"\x01\x02" * 2400


def audio_msg(data: bytes = PCM_24K) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(
                role="model",
                parts=[
                    types.Part(inline_data=types.Blob(data=data, mime_type="audio/pcm;rate=24000"))
                ],
            )
        )
    )


def out_tx(text: str) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(output_transcription=types.Transcription(text=text))
    )


def in_tx(text: str) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(input_transcription=types.Transcription(text=text))
    )


def turn_complete() -> types.LiveServerMessage:
    return types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))


def interrupted() -> types.LiveServerMessage:
    return types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True))


def tool_call(name: str, args: dict[str, Any], call_id: str = "call-1") -> types.LiveServerMessage:
    return types.LiveServerMessage(
        tool_call=types.LiveServerToolCall(
            function_calls=[types.FunctionCall(id=call_id, name=name, args=args)]
        )
    )


def resumption(handle: str, resumable: bool = True) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        session_resumption_update=types.LiveServerSessionResumptionUpdate(
            new_handle=handle, resumable=resumable
        )
    )


def go_away(time_left: str = "5s") -> types.LiveServerMessage:
    return types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left=time_left))


class FakeLiveSession:
    """Scripted provider session. ``receive()`` ends at turn boundaries like the real SDK."""

    def __init__(
        self, on_send: Callable[[FakeLiveSession, str, dict[str, Any]], None] | None = None
    ) -> None:
        self.on_send = on_send
        self._turns: asyncio.Queue[list[types.LiveServerMessage] | None] = asyncio.Queue()
        self.realtime: list[dict[str, Any]] = []
        self.client_content: list[dict[str, Any]] = []
        self.tool_responses: list[Any] = []
        self.receive_calls = 0

    def push_turn(self, *messages: types.LiveServerMessage) -> None:
        self._turns.put_nowait(list(messages))

    def drop(self) -> None:
        """Simulate the provider closing the connection."""
        self._turns.put_nowait(None)

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        self.receive_calls += 1
        turn = await self._turns.get()
        if turn is None:
            raise ConnectionError("provider closed")
        for message in turn:
            yield message

    async def send_realtime_input(self, **kwargs: Any) -> None:
        self.realtime.append(kwargs)
        if self.on_send is not None:
            self.on_send(self, "realtime", kwargs)

    async def send_client_content(self, **kwargs: Any) -> None:
        self.client_content.append(kwargs)
        if self.on_send is not None:
            self.on_send(self, "client_content", kwargs)

    async def send_tool_response(self, **kwargs: Any) -> None:
        self.tool_responses.extend(kwargs.get("function_responses", []))


class _Ctx:
    def __init__(self, session: FakeLiveSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeLiveSession:
        return self.session

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeLive:
    """Connector factory: ``FakeLive().connector`` goes to ``GeminiLiveBridge(connector=...)``."""

    def __init__(
        self,
        fail_with: Exception | None = None,
        on_send: Callable[[FakeLiveSession, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.fail_with = fail_with
        self.on_send = on_send
        self.sessions: list[FakeLiveSession] = []
        self.connects: list[tuple[str, Any]] = []

    @property
    def session(self) -> FakeLiveSession:
        return self.sessions[-1]

    def connector(self, model: str, config: Any) -> _Ctx:
        self.connects.append((model, config))
        if self.fail_with is not None:
            raise self.fail_with
        session = FakeLiveSession(self.on_send)
        self.sessions.append(session)
        return _Ctx(session)
