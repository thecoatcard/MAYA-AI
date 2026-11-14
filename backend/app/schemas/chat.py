"""Chat schemas: the request body, the read models and the Server-Sent Events of ``/api/v1/chat``.

The shapes are the owner-approved contract in ``docs/api/CHAT.md`` (ADR-0008). Requests reject
unknown fields. The event models are frozen and carry exactly the documented fields, so a
provider error text, a prompt or any other free text can never ride along on an ``error`` event.
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.services.chat_library import clean_title
from app.services.progress import MAX_STEPS, ProgressStep, parse_steps

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
ErrorCode = Literal[
    "content_blocked",
    "reply_deadline_exceeded",
    "stream_interrupted",
    "model_unavailable",
    "internal_error",
]


class ChatMessageRequest(BaseModel):
    """Body of ``POST /api/v1/chat/message``.

    Strictness is per field on purpose: model-level ``strict=True`` would make Pydantic refuse a
    UUID given as a string, which is the only form JSON has. ``content`` has no fixed length: the
    model's context budget decides (``message_too_long``), in the service, and the body size is
    bounded by the transport guard.
    """

    model_config = ConfigDict(extra="forbid")

    content: Annotated[str, Field(min_length=1, strict=True)]
    session_id: UUID | None = None
    modality: Literal["text"] = "text"
    # Upload ids (POST /api/v1/files/uploads) the message refers to (ADR-0022). Additive.
    attachments: list[Annotated[str, Field(strict=True, min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=10
    )
    # The user's reply to an ``input.requested`` question card (ask_user). Additive. The service
    # re-validates it against the stored card and rebuilds ``content`` from it.
    answer: "InputAnswerIn | None" = None
    # Optional request mode. "deep_research" asks for the multi-source research run (the composer's
    # brain toggle). Absent means a normal chat turn. Additive.
    mode: Literal["deep_research"] | None = None
    # The assistant message (of this session) the user means by "make this a PDF", "save this to
    # Notion as it is" or "email this" (the message right-click menu). Additive. The service checks
    # that it is an assistant message of the caller's own ``session_id``.
    source_message_id: UUID | None = None

    @field_validator("content")
    @classmethod
    def _no_control_characters(cls, value: str) -> str:
        if _FORBIDDEN_CONTENT.search(value) is not None:
            raise ValueError("content contains characters that are not allowed")
        return value

    @field_validator("session_id", "source_message_id", mode="before")
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
    # The caller's own flags (right-click menu); both false when never set. Additive.
    pinned: bool = False
    archived: bool = False


class SessionPatchIn(BaseModel):
    """Body of ``PATCH /sessions/{id}``: at least one of the three fields."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(strict=True)] | None = None
    pinned: Annotated[bool, Field(strict=True)] | None = None
    archived: Annotated[bool, Field(strict=True)] | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = clean_title(value)
        if cleaned is None:
            raise ValueError("title must be 1 to 120 characters without control characters")
        return cleaned

    @model_validator(mode="after")
    def _something(self) -> "SessionPatchIn":
        if self.title is None and self.pinned is None and self.archived is None:
            raise ValueError("nothing to change")
        return self


class SessionDuplicateIn(BaseModel):
    """Body of ``POST /sessions/{id}/duplicate`` (the body itself is optional)."""

    model_config = ConfigDict(extra="forbid")

    from_message_id: UUID | None = None


class MessageFeedbackIn(BaseModel):
    """Body of ``POST /messages/{id}/feedback``: ``value`` is required; ``null`` clears."""

    model_config = ConfigDict(extra="forbid")

    value: Literal["up", "down"] | None
    note: Annotated[str, Field(strict=True, max_length=500)] | None = None

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _FORBIDDEN_CONTENT.search(value) is not None:
            raise ValueError("note contains characters that are not allowed")
        return value.strip() or None


class MessageOut(BaseModel):
    """One message in ``GET /sessions/{id}/messages``.

    ``finish_reason`` and ``model_id`` are set only on assistant messages; on user messages they
    are serialised as ``null``. ``modality`` is ``audio`` for a spoken voice turn; ``turn_id`` and
    ``source`` come from the voice turn metadata and are ``null`` for typed messages. ``metadata``
    exposes only the task and memories a reply created (never usage, model or agent details).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    id: UUID
    role: Role
    content: str
    created_at: datetime
    finish_reason: str | None = None
    model_id: str | None = None
    modality: Literal["text", "audio", "multimodal"] = "text"
    turn_id: str | None = None
    source: str | None = None
    metadata: "MessageMetadataOut" = Field(default_factory=lambda: MessageMetadataOut())
    # The caller's own thumbs on this reply (``up``, ``down`` or ``null``). Additive.
    feedback: Literal["up", "down"] | None = None


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


class TaskCreatedEvent(_Event):
    """A background task was started for this message. Sent at most once per response.

    ``promoted`` is true when an inline answer was moved to the background because it went over
    the inline budget (the reply says so). It is optional and defaults to false.
    """

    task_id: UUID
    title: str
    status: str
    promoted: bool = False


class ToolStartedEvent(_Event):
    """The model started an inline tool call (for example a file being created). Progress only."""

    call_id: str
    name: str


class ToolFinishedEvent(_Event):
    """An inline tool call finished. ``status`` is ``ok``, ``error`` or ``skipped``."""

    call_id: str
    name: str
    status: Literal["ok", "error", "skipped"]


class FileCreatedEvent(_Event):
    """A text file was created (``created``) or a new version of an attached file was saved
    (``edited``) for this message. Stored with the reply, so the file card survives a reload.

    The original attached file is never changed. ``source_upload_id`` names the attachment an
    edit started from. The file bytes are fetched through the chat file download route.
    """

    file_id: UUID
    name: str
    file_type: str
    mime_type: str
    size_bytes: int
    sha256: str
    action: Literal["created", "edited"]
    source_upload_id: str | None = None
    # Images only (file_type "image"): the pixel size read from the file header, when known.
    width: int | None = None
    height: int | None = None


class MemorySavedEvent(_Event):
    """MAYA saved (or updated) a memory for this message. Can be sent more than once per response.

    ``memory_id`` is the id the Memory API deletes (the chip's Undo). ``updated`` is true when a
    near-identical memory was changed instead of a new one being added.
    """

    memory_id: UUID
    text: str
    updated: bool = False


class AgentDraftEvent(_Event):
    """MAYA drafted a custom agent for this message (ADR-0023). Nothing is written yet.

    ``content`` is the exact file that Approve writes (through ``POST /api/v1/agents/custom``
    with ``confirm: true``, which the user triggers); ``path`` is relative to the connected folder.
    """

    name: str
    description: str
    path: str
    content: str
    capabilities: list[str] = Field(default_factory=list)
    instructions: str = ""


class ScheduleDraftEvent(_Event):
    """MAYA proposed a scheduled report (ADR-0033). Nothing is scheduled yet.

    ``schedule`` is exactly what ``POST /api/v1/schedules/drafts/{draft_id}/confirm`` creates and
    ``preview`` is the configuration in words; only a click on Create calls confirm (a typed or
    spoken "yes" never does). ``POST .../cancel`` discards the draft.
    """

    draft_id: str
    name: str
    preview: str
    schedule: dict[str, Any]
    expires_at: str


class InputOption(_Event):
    """One choice of an ``ask_user`` question. Plain text only: it is never rendered as Markdown."""

    label: str
    description: str = ""


class InputQuestion(_Event):
    """One question of an ``input.requested`` card. No options means a short free-text question."""

    question: str
    header: str = ""
    options: list[InputOption] = Field(default_factory=list)
    multi_select: bool = False
    allow_text: bool = True


InputState = Literal["awaiting_input", "answered", "dismissed"]


class InputRequestedEvent(_Event):
    """MAYA asks the user a question (the ``ask_user`` tool). The turn ends until the user answers.

    Stored with the reply (metadata ``input_request``), so the card survives a reload. This is not
    an approval: answering never approves or starts anything by itself. ``state`` is
    ``awaiting_input`` on the stream; a stored card reads ``answered`` or ``dismissed`` once the
    next user message exists.
    """

    ask_id: str
    questions: list[InputQuestion]
    source: Literal["chat", "voice", "task"] = "chat"
    state: InputState = "awaiting_input"


class InputAnswerItemIn(BaseModel):
    """The reply to one question: chosen option labels and/or text."""

    model_config = ConfigDict(extra="forbid")

    selected: list[Annotated[str, Field(max_length=400)]] = Field(
        default_factory=list, max_length=6
    )
    text: Annotated[str, Field(max_length=2000)] = ""


class InputAnswerIn(BaseModel):
    """Request payload that answers an ``input.requested`` card (``POST /chat/message``)."""

    model_config = ConfigDict(extra="forbid")

    ask_id: Annotated[str, Field(min_length=1, max_length=64)]
    answers: list[InputAnswerItemIn] = Field(max_length=4)


class InputAnswerItemOut(_Event):
    question: str = ""
    selected: list[str] = Field(default_factory=list)
    text: str = ""


class InputAnswerOut(_Event):
    """The sanitised answers stored on the user message (metadata ``answers``)."""

    ask_id: str
    answers: list[InputAnswerItemOut]


class ProgressEvent(ProgressStep):
    """SSE ``progress`` event: one state change of a step of the live step list.

    The fields and allowed values are those of ``app.services.progress.ProgressStep``. The same
    objects are stored, unchanged, under the assistant message metadata key ``progress``.
    """


class TaskResultOut(BaseModel):
    """Marks an assistant message as the "what the task got" result of a finished task
    (``app.services.task_result``): the task it reports on, its final state and its file ids."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    task_id: str
    status: Literal["done", "partial", "failed", "cancelled", "needs_attention"]
    artifact_ids: list[str] = Field(default_factory=list)


class MessageMetadataOut(BaseModel):
    """The task, memories, files and steps stored with a reply. Unreadable data reads as none."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    task: TaskCreatedEvent | None = None
    memories_saved: list[MemorySavedEvent] = Field(default_factory=list)
    agent_drafts: list[AgentDraftEvent] = Field(default_factory=list)
    schedule_drafts: list[ScheduleDraftEvent] = Field(default_factory=list)
    # The first schedule preview as {id, name, preview, schedule, expires_at}: the shape the web
    # app reads (``metadata.schedule_draft.id``). Same data as ``schedule_drafts[0]``.
    schedule_draft: dict[str, Any] | None = None
    files_created: list[FileCreatedEvent] = Field(default_factory=list)
    progress: list[ProgressEvent] = Field(default_factory=list)
    # Token usage of the reply (turns, elapsed time, cached tokens) for the work-log footer.
    usage: "UsageOut | None" = None
    # The question card of an ``ask_user`` reply (assistant message) and the answer it received
    # (user message). Both are additive; unreadable data reads as none.
    input_request: InputRequestedEvent | None = None
    answer: InputAnswerOut | None = None
    # Set on the message MAYA posts when a task ends: what it got (additive, ``None`` otherwise).
    task_result: TaskResultOut | None = None

    @model_validator(mode="before")
    @classmethod
    def _tolerant(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return {}
        out: dict[str, Any] = {}
        out["progress"] = [
            ProgressEvent.model_validate(step.model_dump())
            for step in parse_steps(value.get("progress"))[:MAX_STEPS]
        ]
        try:
            out["task"] = TaskCreatedEvent.model_validate(value.get("task"))
        except ValidationError:
            out["task"] = None
        files: list[FileCreatedEvent] = []
        raw_files = value.get("files_created")
        for item in raw_files if isinstance(raw_files, list) else []:
            try:
                files.append(FileCreatedEvent.model_validate(item))
            except ValidationError:
                continue
        out["files_created"] = files
        saved: list[MemorySavedEvent] = []
        raw = value.get("memories_saved")
        for item in raw if isinstance(raw, list) else []:
            try:
                saved.append(MemorySavedEvent.model_validate(item))
            except ValidationError:
                continue
        out["memories_saved"] = saved
        drafts: list[AgentDraftEvent] = []
        raw_drafts = value.get("agent_drafts")
        for item in raw_drafts if isinstance(raw_drafts, list) else []:
            try:
                drafts.append(AgentDraftEvent.model_validate(item))
            except ValidationError:
                continue
        out["agent_drafts"] = drafts
        sched: list[ScheduleDraftEvent] = []
        raw_sched = value.get("schedule_drafts")
        for item in raw_sched if isinstance(raw_sched, list) else []:
            try:
                sched.append(ScheduleDraftEvent.model_validate(item))
            except ValidationError:
                continue
        out["schedule_drafts"] = sched
        out["schedule_draft"] = (
            {
                "id": sched[0].draft_id,
                "name": sched[0].name,
                "preview": sched[0].preview,
                "schedule": sched[0].schedule,
                "expires_at": sched[0].expires_at,
            }
            if sched
            else None
        )
        try:
            out["usage"] = UsageOut.model_validate(value.get("usage"))
        except ValidationError:
            out["usage"] = None
        raw_ask = value.get("input_request")
        try:
            out["input_request"] = (
                InputRequestedEvent.model_validate(
                    {k: v for k, v in raw_ask.items() if k != "call"}
                )
                if isinstance(raw_ask, dict)
                else None
            )
        except ValidationError:
            out["input_request"] = None
        out["task_result"] = None
        if value.get("kind") == "task_result":
            try:
                out["task_result"] = TaskResultOut.model_validate(
                    {
                        "task_id": value.get("task_id"),
                        "status": value.get("status"),
                        "artifact_ids": [str(x) for x in value.get("artifact_ids") or []],
                    }
                )
            except (ValidationError, TypeError):
                out["task_result"] = None
        raw_answer = value.get("answers_to")
        raw_items = value.get("answers")
        try:
            out["answer"] = (
                InputAnswerOut.model_validate({"ask_id": raw_answer, "answers": raw_items})
                if isinstance(raw_answer, str) and isinstance(raw_items, list)
                else None
            )
        except ValidationError:
            out["answer"] = None
        return out


class UsageOut(_Event):
    """Token usage. ``cost_usd`` is a decimal string, or ``None`` when the cost is unknown."""

    prompt_tokens: Annotated[int, Field(ge=0)]
    candidates_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    cost_usd: str | None
    is_estimate: bool
    # Prompt tokens the provider served from its cache (0 when none or not reported).
    cached_tokens: Annotated[int, Field(ge=0)] = 0
    # Model calls made for this reply (the first answer plus one per tool round). ``None`` when
    # the server did not count them: a client shows nothing rather than guessing.
    turns: Annotated[int, Field(ge=1)] | None = None
    # Wall-clock milliseconds from the start of the reply to its end. ``None`` when not measured.
    elapsed_ms: Annotated[int, Field(ge=0)] | None = None

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
