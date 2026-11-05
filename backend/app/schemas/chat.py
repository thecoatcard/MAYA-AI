"""Chat schemas: the request body, the read models and the Server-Sent Events of ``/api/v1/chat``.

The shapes are the owner-approved contract in ``docs/api/CHAT.md`` (ADR-0008). Requests reject
unknown fields. The event models are frozen and carry exactly the documented fields, so a
provider error text, a prompt or any other free text can never ride along on an ``error`` event.
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Control characters other than newline and tab (C0 without \t and \n, DEL and the C1 range), and
# lone surrogates, which cannot be encoded as UTF-8 and would fail later at the database.
_FORBIDDEN_CONTENT = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")
_UUID_TEXT_LENGTH: Final = 36  # the canonical 8-4-4-4-12 form; no braces, urn: prefix or bare hex
_DECIMAL_STRING = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_EVENT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}")
# JSON leaves these three raw, and they end a line for Python's ``str.splitlines`` and some
# proxies. Escaping them keeps the JSON equal and the frame to exactly one data line.
_LINE_BREAK_ESCAPES: Final = {
    0x85: "\\u0085",
    0x2028: "\\u2028",
    0x2029: "\\u2029",
}

Channel = Literal["web", "voice", "desktop"]
Role = Literal["user", "assistant"]
FinishReason = Literal["stop", "max_tokens", "interrupted"]
ErrorCode = Literal["content_blocked", "stream_interrupted", "model_unavailable", "internal_error"]


class ChatMessageRequest(BaseModel):
    """Body of ``POST /api/v1/chat/message``.

    Strictness is per field on purpose: model-level ``strict=True`` would make Pydantic refuse a
    UUID given as a string, which is the only form JSON has. The maximum length of ``content`` is
    ``MAYA_CHAT_MAX_MESSAGE_CHARS`` and is enforced by the service, not here.
    """

    model_config = ConfigDict(extra="forbid")

    content: Annotated[str, Field(min_length=1, strict=True)]
    session_id: UUID | None = None
    modality: Literal["text"] = "text"

    @field_validator("content")
    @classmethod
    def _no_control_characters(cls, value: str) -> str:
        if _FORBIDDEN_CONTENT.search(value) is not None:
            raise ValueError("content contains characters that are not allowed")
        return value

    @field_validator("session_id", mode="before")
    @classmethod
    def _canonical_session_id(cls, value: object) -> object:
        # Only a UUID or its canonical text; not bytes, integers or the odd spellings Python's
        # UUID parser tolerates, so one session has one spelling.
        if value is None or isinstance(value, UUID):
            return value
        if isinstance(value, str) and len(value) == _UUID_TEXT_LENGTH:
            return value
        raise ValueError("session_id must be a UUID")


class SessionOut(BaseModel):
    """One session in ``GET /sessions``."""

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: UUID
    title: str | None
    channel: Channel
    status: str
    created_at: datetime


class MessageOut(BaseModel):
    """One message in ``GET /sessions/{id}/messages``.

    ``finish_reason`` and ``model_id`` are set only on assistant messages; on user messages they
    are serialised as ``null``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: UUID
    role: Role
    content: str
    created_at: datetime
    finish_reason: str | None = None
    model_id: str | None = None


class Page[T](BaseModel):
    """A page of a keyset-paginated list. ``next_cursor`` is ``None`` on the last page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[T]
    next_cursor: str | None = None


def format_cost_usd(value: Decimal | None) -> str | None:
    """A cost as a plain decimal string, or ``None`` when it is unknown.

    ``str(Decimal)`` can produce an exponent (``1E-7``), which is not a decimal string; this
    never does. An unknown cost stays ``None``; it is never turned into ``"0"``.
    """
    if value is None:
        return None
    return format(value, "f")


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MessageAcceptedEvent(_Event):
    """First event of a response. The user message is already stored."""

    session_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    replayed: bool


class DeltaEvent(_Event):
    """The next piece of the reply, or (when replayed) the complete stored reply."""

    text: str


class UsageOut(_Event):
    """Token usage. ``cost_usd`` is a decimal string, or ``None`` when the cost is unknown."""

    prompt_tokens: Annotated[int, Field(ge=0)]
    candidates_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    cost_usd: str | None
    is_estimate: bool

    @field_validator("cost_usd")
    @classmethod
    def _plain_decimal(cls, value: str | None) -> str | None:
        if value is not None and _DECIMAL_STRING.fullmatch(value) is None:
            raise ValueError("cost_usd must be a plain non-negative decimal string")
        return value


class DoneEvent(_Event):
    """Last event on success."""

    assistant_message_id: UUID
    finish_reason: FinishReason
    usage: UsageOut


class ErrorEvent(_Event):
    """Last event on failure. Carries a code only, never provider or exception text."""

    code: ErrorCode
    retryable: bool


def encode_sse(event_id: int, event: str, data: BaseModel) -> str:
    """One well-formed Server-Sent Events frame: ``id``, ``event``, one ``data`` line, blank line.

    ``data`` is compact JSON on a single line. Newlines inside strings are JSON escapes, and the
    three characters JSON leaves raw but that end a line elsewhere (U+0085, U+2028, U+2029) are
    escaped too, so submitted or generated text cannot add fields or events to the frame.

    Raises ``ValueError`` for an ``event_id`` below 1 or an ``event`` that is not a plain name.
    """
    if isinstance(event_id, bool) or event_id < 1:
        raise ValueError("event_id must be an integer of at least 1")
    if _EVENT_NAME.fullmatch(event) is None:
        raise ValueError("event must be a plain event name")
    payload = data.model_dump_json().translate(_LINE_BREAK_ESCAPES)
    if "\n" in payload or "\r" in payload:  # unreachable for valid JSON; the frame stays safe
        raise ValueError("event data must be a single line")
    return f"id: {event_id}\nevent: {event}\ndata: {payload}\n\n"
