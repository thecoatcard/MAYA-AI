"""Voice WebSocket protocol v2: close codes, limits, origin policy and frame builders.

Everything here is pure (no I/O) so the wire format is defined in one place and tested directly.
The protocol is additive to v1: v1 frame types are still produced for clients that did not ask
for ``protocol: 2``. See ``docs/api/VOICE_SPECIFICATION.md`` and ADR-0015.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any, Final
from urllib.parse import urlsplit
from uuid import UUID

from app.gateway.exceptions import overloaded_user_message

PROTOCOL_VERSION: Final = 2

# Application close codes (4000-4999 is the private-use range).
CLOSE_NORMAL: Final = 1000
CLOSE_MESSAGE_TOO_BIG: Final = 1009
CLOSE_UNAUTHENTICATED: Final = 4401
CLOSE_FORBIDDEN_ORIGIN: Final = 4403
CLOSE_SESSION_NOT_FOUND: Final = 4404
CLOSE_TOO_MANY: Final = 4429
CLOSE_UNAVAILABLE: Final = 4503

# Origin allow-list. A later change can wire ``settings.voice_allowed_origins`` (read via getattr in
# ``origin_allowed``). Loopback hosts are always allowed so local development and a desktop shell
# that loads the bundled server over loopback work; browsers always send Origin on WebSockets.
VOICE_ALLOWED_ORIGINS: Final[tuple[str, ...]] = ()
LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})

# Limits (module constants until a settings change wires them).
HELLO_TIMEOUT_SECONDS: Final = 5.0
MAX_SOCKETS_PER_USER: Final = 2
IDLE_TIMEOUT_SECONDS: Final = 300.0
MAX_DURATION_SECONDS: Final = 3600.0
MAX_BINARY_FRAME_BYTES: Final = 64 * 1024
MAX_TEXT_FRAME_CHARS: Final = 16 * 1024
MAX_USER_TEXT_CHARS: Final = 4000
SEED_HISTORY_MESSAGES: Final = 20
RESUMPTION_TTL_SECONDS: Final = 900

# Voice scope, chosen by the client at connect time (query ``scope=`` or HELLO ``scope``).
# ``master`` is the app-wide progress tracker (top-bar voice): it announces milestones of ALL of
# the user's tasks, naming the conversation. ``conversation`` (the default, and what any unknown
# value means) is the conversation-bound voice. Either way only the caller's own tasks are seen.
SCOPE_MASTER: Final = "master"
SCOPE_CONVERSATION: Final = "conversation"


def normalize_scope(value: Any) -> str:
    """``master`` only for that exact value; everything else is the conversation-bound scope."""
    return (
        SCOPE_MASTER
        if isinstance(value, str) and value.strip().lower() == "master"
        else SCOPE_CONVERSATION
    )


AUDIO_INPUT: Final = {"rate": 16000, "frame_bytes": 3200}
AUDIO_OUTPUT: Final = {"rate": 24000}


def origin_allowed(
    origin: str | None, host: str | None, extra: Iterable[str] = VOICE_ALLOWED_ORIGINS
) -> bool:
    """Same-origin, loopback, or explicitly allowed. A missing Origin (non-browser) is allowed:
    such clients are not subject to cross-site WebSocket hijacking and still need a token."""
    if not origin:
        return True
    if origin in set(extra):
        return True
    try:
        parts = urlsplit(origin)
        hostname = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if hostname in LOOPBACK_HOSTS or f"[{hostname}]" in LOOPBACK_HOSTS:
        return True
    return bool(host) and parts.netloc.lower() == (host or "").lower()


class ConnectionLimiter:
    """Per-process count of open voice sockets per user."""

    def __init__(self, maximum: int = MAX_SOCKETS_PER_USER) -> None:
        self.maximum = maximum
        self._counts: dict[UUID, int] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, user_id: UUID) -> bool:
        async with self._lock:
            current = self._counts.get(user_id, 0)
            if current >= self.maximum:
                return False
            self._counts[user_id] = current + 1
            return True

    async def release(self, user_id: UUID) -> None:
        async with self._lock:
            current = self._counts.get(user_id, 0) - 1
            if current <= 0:
                self._counts.pop(user_id, None)
            else:
                self._counts[user_id] = current

    def count(self, user_id: UUID) -> int:
        return self._counts.get(user_id, 0)


# ---- server -> client frame builders -------------------------------------------------------


def ready(
    session_id: str, *, resumable: bool, persisted: bool, resumed: bool, title: str | None = None
) -> dict[str, Any]:
    return {
        "type": "READY",
        "protocol": PROTOCOL_VERSION,
        "session_id": session_id,
        "mode": "live",
        "input": dict(AUDIO_INPUT),
        "output": dict(AUDIO_OUTPUT),
        "resumable": resumable,
        "resumed": resumed,
        "persisted": persisted,
        "title": title,
    }


def live_unavailable(
    reason: str,
    session_id: str | None = None,
    *,
    models_tried: int = 0,
    keys_tried: int = 0,
) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "LIVE_UNAVAILABLE", "reason": reason, "session_id": session_id}
    if reason in ("quota", "connect_failed"):
        # Plain, secret-free text the web may show as is (no key labels, no provider text).
        frame["message"] = overloaded_user_message(models_tried, keys_tried)
    return frame


def turn_state(turn_id: str | None, state: str) -> dict[str, Any]:
    return {"type": "TURN_STATE", "turn_id": turn_id, "state": state}


def transcript(
    turn_id: str,
    role: str,
    delta: str,
    final: bool,
    source: str = "spoken",
    text: str | None = None,
) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": "TRANSCRIPT",
        "turn_id": turn_id,
        "role": role,
        "source": source,
        "delta": delta,
        "final": final,
    }
    if final and text is not None:
        frame["text"] = text
    return frame


def message_persisted(turn_id: str, message_id: str, role: str) -> dict[str, Any]:
    return {
        "type": "MESSAGE_PERSISTED",
        "turn_id": turn_id,
        "message_id": message_id,
        "role": role,
    }


def file_created(card: dict[str, Any], message_id: str | None) -> dict[str, Any]:
    """Additive frame: a file card made by voice (an image from generate_image). ``file`` has the
    same shape as the chat ``file.created`` event; the bytes come from the chat file route."""
    return {"type": "FILE_CREATED", "message_id": message_id, "file": card}


def input_requested(card: dict[str, Any], message_id: str | None) -> dict[str, Any]:
    """Additive frame: MAYA asked a question by voice (``ask_user``). ``card`` has the shape of the
    chat ``input.requested`` event; the answer is posted as a chat message (``answer``). Clients
    may ignore it. It is never an approval."""
    return {"type": "INPUT_REQUESTED", "message_id": message_id, "card": card}


def schedule_draft(card: dict[str, Any], message_id: str | None) -> dict[str, Any]:
    """Additive frame: MAYA proposed a scheduled report by voice. ``card`` has the shape of the
    chat ``schedule.draft`` event; only a click on Create (REST confirm) schedules it. Never an
    approval by voice."""
    return {"type": "SCHEDULE_DRAFT", "message_id": message_id, "card": card}


def chat_message(message_id: str, content: str, tool: str) -> dict[str, Any]:
    """Additive frame: a tool put an assistant message (code, notes, a list, a long result) into
    the chat. It is already saved under ``message_id``; clients show it live and dedupe by id."""
    return {
        "type": "CHAT_MESSAGE",
        "message_id": message_id,
        "role": "assistant",
        "content": content,
        "tool": tool,
    }


def memory_saved(memory_id: str, text: str, updated: bool) -> dict[str, Any]:
    """Additive frame (Task V): MAYA saved (or updated) a memory by voice. The web can show a chip
    with Undo (``DELETE`` on the Memory API with ``memory_id``). Clients may ignore it."""
    return {"type": "MEMORY_SAVED", "memory_id": memory_id, "text": text, "updated": updated}


def error(code: str, message: str, *, fatal: bool) -> dict[str, Any]:
    return {"type": "ERROR", "code": code, "message": message, "fatal": fatal}


def go_away(retry_ms: int | None, resumable: bool) -> dict[str, Any]:
    return {"type": "GO_AWAY", "retry_ms": retry_ms, "resumable": resumable}


def task_event(task_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": "TASK_EVENT",
        "task_id": task_id,
        "event_type": event_type,
        "subtask_id": payload.get("subtask_id"),
        "title": payload.get("title") or payload.get("description"),
        "status": payload.get("status"),
    }
    step = payload.get("step")
    if isinstance(step, dict):
        # Additive: the progress step of a ``task_progress`` event (services/progress.py shape,
        # already scrubbed and capped when it was written). Clients may ignore it.
        frame["step"] = step
    return frame
