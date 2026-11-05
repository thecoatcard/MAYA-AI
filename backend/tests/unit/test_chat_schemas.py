"""Chat schemas: request validation, read models and the exact Server-Sent Events frames."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas.chat import (
    ChatMessageRequest,
    DeltaEvent,
    DoneEvent,
    ErrorEvent,
    MessageAcceptedEvent,
    MessageOut,
    Page,
    SessionOut,
    UsageOut,
    encode_sse,
    format_cost_usd,
)

SESSION = UUID("8f8b80e8-0b53-4f9e-99f5-748a2784cf22")
USER_MESSAGE = UUID("11111111-1111-4111-8111-111111111111")
ASSISTANT_MESSAGE = UUID("22222222-2222-4222-8222-222222222222")
WHEN = datetime(2026, 11, 5, 10, 0, 0, tzinfo=UTC)


def usage(cost: str | None = "0.0001") -> UsageOut:
    return UsageOut(
        prompt_tokens=10,
        candidates_tokens=20,
        total_tokens=30,
        cost_usd=cost,
        is_estimate=False,
    )


@dataclass
class ParsedEvent:
    id: str | None
    event: str | None
    data: list[str]


def parse_sse(stream: str) -> list[ParsedEvent]:
    """A minimal reader following the SSE processing rules (lines end in LF, CR or CRLF only)."""
    events: list[ParsedEvent] = []
    current = ParsedEvent(None, None, [])
    for line in stream.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line == "":
            if current.id is not None or current.event is not None or current.data:
                events.append(current)
            current = ParsedEvent(None, None, [])
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "id":
            current.id = value
        elif field == "event":
            current.event = value
        elif field == "data":
            current.data.append(value)
    return events


# ---------------------------------------------------------------------------------------------
# ChatMessageRequest


def test_a_minimal_request_gets_the_documented_defaults() -> None:
    request = ChatMessageRequest.model_validate({"content": "hello"})
    assert request.content == "hello"
    assert request.session_id is None
    assert request.modality == "text"


def test_a_request_with_a_session_id_accepts_the_json_string_form() -> None:
    body = {"content": "Summarize the open risks.", "session_id": str(SESSION)}
    assert ChatMessageRequest.model_validate(body).session_id == SESSION
    assert ChatMessageRequest.model_validate_json(json.dumps(body)).session_id == SESSION
    assert (
        ChatMessageRequest.model_validate({"content": "x", "session_id": None}).session_id is None
    )


@pytest.mark.parametrize(
    "body",
    [
        {"content": "hi", "extra": 1},
        {"content": "hi", "user_id": str(SESSION)},
        {"content": "hi", "workspace_id": str(SESSION)},
        {"content": "hi", "idempotency_key": "abcdefgh"},
    ],
)
def test_unknown_fields_are_rejected(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as caught:
        ChatMessageRequest.model_validate(body)
    assert caught.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"content": ""},
        {"content": None},
        {"content": 5},
        {"content": ["hi"]},
        {"content": True},
        {"content": "hi", "modality": "audio"},
        {"content": "hi", "modality": "multimodal"},
        {"content": "hi", "modality": None},
        {"content": "hi", "session_id": "not-a-uuid"},
        {"content": "hi", "session_id": 5},
        {"content": "hi", "session_id": ""},
        {"content": "hi", "session_id": "8f8b80e80b534f9e99f5748a2784cf22"},
        {"content": "hi", "session_id": "{8f8b80e8-0b53-4f9e-99f5-748a2784cf22}"},
        {"content": "hi", "session_id": "urn:uuid:8f8b80e8-0b53-4f9e-99f5-748a2784cf22"},
        {"content": "hi", "session_id": SESSION.bytes},
    ],
)
def test_invalid_values_are_rejected(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ChatMessageRequest.model_validate(body)


def test_the_maximum_length_is_not_the_schemas_business() -> None:
    assert len(ChatMessageRequest(content="x" * 200_000).content) == 200_000


def test_newlines_tabs_unicode_and_quotes_are_valid_content() -> None:
    content = 'line one\n\tline "two"\nemoji \U0001f600 and 中文 and \N{LINE SEPARATOR} separator'
    assert ChatMessageRequest(content=content).content == content


@pytest.mark.parametrize(
    "character", ["\x00", "\x01", "\x08", "\x0b", "\x0c", "\r", "\x1b", "\x7f", "\x85", "\x9f"]
)
def test_control_characters_other_than_newline_and_tab_are_rejected(character: str) -> None:
    with pytest.raises(ValidationError) as caught:
        ChatMessageRequest(content=f"before{character}after")
    assert caught.value.errors()[0]["loc"] == ("content",)
    assert "before" not in str(caught.value.errors()[0]["msg"])  # the message never repeats input


def test_a_lone_surrogate_is_rejected_because_it_cannot_be_stored() -> None:
    with pytest.raises(ValidationError):
        ChatMessageRequest(content="a\ud800b")


# ---------------------------------------------------------------------------------------------
# Read models


@dataclass
class FakeSessionRecord:
    id: UUID
    title: str | None
    channel: str
    status: str
    created_at: datetime
    workspace_id: UUID  # extra attributes are ignored


def test_a_session_serialises_to_the_documented_fields() -> None:
    out = SessionOut(
        id=SESSION,
        title="Summarize the open risks.",
        channel="web",
        status="active",
        created_at=WHEN,
    )
    assert json.loads(out.model_dump_json()) == {
        "id": str(SESSION),
        "title": "Summarize the open risks.",
        "channel": "web",
        "status": "active",
        "created_at": "2026-11-05T10:00:00Z",
    }


def test_a_session_can_be_built_from_a_record_and_may_have_no_title() -> None:
    record = FakeSessionRecord(SESSION, None, "voice", "active", WHEN, USER_MESSAGE)
    out = SessionOut.model_validate(record)
    assert out.title is None and out.channel == "voice"
    assert set(out.model_dump()) == {"id", "title", "channel", "status", "created_at"}


def test_an_unknown_channel_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SessionOut(id=SESSION, title=None, channel="sms", status="active", created_at=WHEN)


def test_a_user_message_has_no_model_fields_and_an_assistant_message_has_them() -> None:
    user = MessageOut(id=USER_MESSAGE, role="user", content="hi", created_at=WHEN)
    assert user.finish_reason is None and user.model_id is None
    assistant = MessageOut(
        id=ASSISTANT_MESSAGE,
        role="assistant",
        content="hello",
        created_at=WHEN,
        finish_reason="stop",
        model_id="gemini-3.8-flash",
    )
    assert json.loads(assistant.model_dump_json()) == {
        "id": str(ASSISTANT_MESSAGE),
        "role": "assistant",
        "content": "hello",
        "created_at": "2026-11-05T10:00:00Z",
        "finish_reason": "stop",
        "model_id": "gemini-3.8-flash",
    }


@pytest.mark.parametrize("role", ["system", "agent", "orchestrator", "model", ""])
def test_a_message_role_is_user_or_assistant_only(role: str) -> None:
    with pytest.raises(ValidationError):
        MessageOut(id=USER_MESSAGE, role=role, content="x", created_at=WHEN)


def test_messages_do_not_accept_fields_the_contract_does_not_expose() -> None:
    with pytest.raises(ValidationError):
        MessageOut.model_validate(
            {
                "id": str(USER_MESSAGE),
                "role": "user",
                "content": "x",
                "created_at": WHEN,
                "metadata": {"usage": 1},
            }
        )


def test_a_page_always_carries_next_cursor_even_when_it_is_null() -> None:
    page = Page[MessageOut](
        items=[MessageOut(id=USER_MESSAGE, role="user", content="hi", created_at=WHEN)]
    )
    body = json.loads(page.model_dump_json())
    assert body["next_cursor"] is None
    assert list(body) == ["items", "next_cursor"]
    assert body["items"][0]["content"] == "hi"
    assert Page[SessionOut](items=[], next_cursor="abc").next_cursor == "abc"


def test_a_page_validates_its_item_type() -> None:
    with pytest.raises(ValidationError):
        Page[SessionOut].model_validate({"items": [{"id": "nope"}], "next_cursor": None})


# ---------------------------------------------------------------------------------------------
# Event models: exactly the documented fields


def test_event_payloads_have_exactly_the_documented_fields() -> None:
    assert set(MessageAcceptedEvent.model_fields) == {
        "session_id",
        "user_message_id",
        "assistant_message_id",
        "replayed",
    }
    assert set(DeltaEvent.model_fields) == {"text"}
    assert set(UsageOut.model_fields) == {
        "prompt_tokens",
        "candidates_tokens",
        "total_tokens",
        "cost_usd",
        "is_estimate",
    }
    assert set(DoneEvent.model_fields) == {"assistant_message_id", "finish_reason", "usage"}
    assert set(ErrorEvent.model_fields) == {"code", "retryable"}


@pytest.mark.parametrize(
    "event",
    [
        MessageAcceptedEvent(
            session_id=SESSION,
            user_message_id=USER_MESSAGE,
            assistant_message_id=ASSISTANT_MESSAGE,
            replayed=False,
        ),
        DeltaEvent(text="x"),
        usage(),
        DoneEvent(assistant_message_id=ASSISTANT_MESSAGE, finish_reason="stop", usage=usage()),
        ErrorEvent(code="internal_error", retryable=False),
    ],
)
def test_events_are_immutable_and_reject_extra_fields(event: BaseModel) -> None:
    with pytest.raises(ValidationError):
        type(event).model_validate({**event.model_dump(), "provider_error": "secret"})
    with pytest.raises(ValidationError):
        setattr(event, next(iter(type(event).model_fields)), None)


@pytest.mark.parametrize("reason", ["stop", "max_tokens", "interrupted"])
def test_the_documented_finish_reasons_are_accepted(reason: str) -> None:
    done = DoneEvent.model_validate(
        {
            "assistant_message_id": ASSISTANT_MESSAGE,
            "finish_reason": reason,
            "usage": usage().model_dump(),
        }
    )
    assert done.finish_reason == reason


@pytest.mark.parametrize("reason", ["length", "safety", "STOP", "", "content_filter"])
def test_other_finish_reasons_are_rejected(reason: str) -> None:
    with pytest.raises(ValidationError):
        DoneEvent.model_validate(
            {
                "assistant_message_id": ASSISTANT_MESSAGE,
                "finish_reason": reason,
                "usage": usage().model_dump(),
            }
        )


@pytest.mark.parametrize(
    "code", ["content_blocked", "stream_interrupted", "model_unavailable", "internal_error"]
)
def test_the_documented_error_codes_are_accepted(code: str) -> None:
    assert ErrorEvent.model_validate({"code": code, "retryable": True}).code == code


@pytest.mark.parametrize("code", ["rate_limited", "quota_exceeded", "RESOURCE_EXHAUSTED", ""])
def test_an_error_code_outside_the_contract_is_rejected(code: str) -> None:
    with pytest.raises(ValidationError):
        ErrorEvent.model_validate({"code": code, "retryable": False})


def test_an_error_event_cannot_carry_provider_text() -> None:
    with pytest.raises(ValidationError):
        ErrorEvent.model_validate(
            {"code": "internal_error", "retryable": False, "message": "429 RESOURCE_EXHAUSTED"}
        )


# ---------------------------------------------------------------------------------------------
# Cost


def test_an_unknown_cost_serialises_as_null_and_never_as_zero() -> None:
    body = json.loads(usage(cost=None).model_dump_json())
    assert "cost_usd" in body and body["cost_usd"] is None
    assert format_cost_usd(None) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0.0001"), "0.0001"),
        (Decimal("1E-7"), "0.0000001"),
        (Decimal("0E-8"), "0.00000000"),
        (Decimal("12"), "12"),
        (Decimal("1.50"), "1.50"),
        (Decimal("1E+3"), "1000"),
    ],
)
def test_a_known_cost_is_a_plain_decimal_string(value: Decimal, expected: str) -> None:
    text = format_cost_usd(value)
    assert text == expected
    assert usage(cost=text).cost_usd == expected


@pytest.mark.parametrize(
    "cost",
    ["", "abc", "-1", "+1", "1e-7", "1E-7", "NaN", "Infinity", " 1", "1 ", "1,5", ".5", "1."],
)
def test_a_cost_that_is_not_a_plain_decimal_string_is_rejected(cost: str) -> None:
    with pytest.raises(ValidationError):
        usage(cost=cost)


def test_a_decimal_object_is_not_accepted_as_a_cost_so_the_helper_must_be_used() -> None:
    with pytest.raises(ValidationError):
        UsageOut(
            prompt_tokens=1,
            candidates_tokens=1,
            total_tokens=2,
            cost_usd=Decimal("0.1"),
            is_estimate=False,
        )


def test_token_counts_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        UsageOut(
            prompt_tokens=-1, candidates_tokens=0, total_tokens=0, cost_usd=None, is_estimate=True
        )


# ---------------------------------------------------------------------------------------------
# encode_sse


def test_a_delta_frame_has_the_exact_wire_format() -> None:
    assert encode_sse(2, "delta", DeltaEvent(text="Hello")) == (
        'id: 2\nevent: delta\ndata: {"text":"Hello"}\n\n'
    )


def test_every_event_type_is_one_id_event_data_frame() -> None:
    accepted = MessageAcceptedEvent(
        session_id=SESSION,
        user_message_id=USER_MESSAGE,
        assistant_message_id=ASSISTANT_MESSAGE,
        replayed=False,
    )
    done = DoneEvent(
        assistant_message_id=ASSISTANT_MESSAGE, finish_reason="stop", usage=usage(None)
    )
    error = ErrorEvent(code="stream_interrupted", retryable=True)
    frames = [
        encode_sse(1, "message.accepted", accepted),
        encode_sse(2, "delta", DeltaEvent(text="a")),
        encode_sse(3, "done", done),
        encode_sse(4, "error", error),
    ]
    assert frames[0] == (
        "id: 1\nevent: message.accepted\n"
        f'data: {{"session_id":"{SESSION}","user_message_id":"{USER_MESSAGE}",'
        f'"assistant_message_id":"{ASSISTANT_MESSAGE}","replayed":false}}\n\n'
    )
    assert frames[2] == (
        "id: 3\nevent: done\n"
        f'data: {{"assistant_message_id":"{ASSISTANT_MESSAGE}","finish_reason":"stop",'
        '"usage":{"prompt_tokens":10,"candidates_tokens":20,"total_tokens":30,'
        '"cost_usd":null,"is_estimate":false}}\n\n'
    )
    assert frames[3] == (
        'id: 4\nevent: error\ndata: {"code":"stream_interrupted","retryable":true}\n\n'
    )
    for frame in frames:
        lines = frame.split("\n")
        assert [line.split(":", 1)[0] for line in lines[:3]] == ["id", "event", "data"]
        assert lines[3:] == ["", ""]  # the blank line that ends the event


HOSTILE_TEXTS = [
    "line one\nline two",
    "windows\r\nline",
    "old mac\rline",
    "quotes \" and \\ backslash and ' apostrophe",
    "unicode 中文 \U0001f600 é",
    "separators \N{LINE SEPARATOR} \N{PARAGRAPH SEPARATOR} \x85 \x0b \x0c \x1c \x1d \x1e",
    "tab\tseparated and trailing newline\n",
    "data: injected\n\nevent: error\ndata: {}\n\n",
    "id: 999\nretry: 0\n: comment\n",
    "\n\n\n",
    "",
]


@pytest.mark.parametrize("hostile", HOSTILE_TEXTS)
def test_text_cannot_break_out_of_its_data_line(hostile: str) -> None:
    frame = encode_sse(7, "delta", DeltaEvent(text=hostile))
    assert frame.startswith("id: 7\nevent: delta\ndata: ")
    assert frame.endswith("}\n\n")
    body = frame.removesuffix("\n\n")
    # No line terminator of any kind appears before the one that ends the frame, whichever way a
    # reader splits lines (SSE splits on LF, CR and CRLF; Python also splits on U+2028 and more).
    assert len(body.splitlines()) == 3
    assert len(body.split("\n")) == 3
    assert "\r" not in body
    events = parse_sse(frame)
    assert len(events) == 1
    assert events[0].id == "7" and events[0].event == "delta"
    assert len(events[0].data) == 1
    assert json.loads(events[0].data[0]) == {"text": hostile}


def test_a_stream_of_hostile_frames_parses_back_to_the_same_events() -> None:
    frames = "".join(
        encode_sse(index, "delta", DeltaEvent(text=text))
        for index, text in enumerate(HOSTILE_TEXTS, start=1)
    )
    events = parse_sse(frames)
    assert [event.id for event in events] == [str(i) for i in range(1, len(HOSTILE_TEXTS) + 1)]
    assert [json.loads(event.data[0])["text"] for event in events] == HOSTILE_TEXTS


@pytest.mark.parametrize("event_id", [0, -1, True, False])
def test_an_event_id_below_one_or_not_an_integer_is_refused(event_id: int) -> None:
    with pytest.raises(ValueError, match="event_id"):
        encode_sse(event_id, "delta", DeltaEvent(text="x"))


@pytest.mark.parametrize(
    "event",
    [
        "",
        "delta\nevent: error",
        "delta\r",
        "de lta",
        "delta:x",
        "1delta",
        "a" * 65,
        "délta",
        "x\ndata: y",
    ],
)
def test_an_event_name_that_is_not_a_plain_name_is_refused(event: str) -> None:
    with pytest.raises(ValueError, match="event"):
        encode_sse(1, event, DeltaEvent(text="x"))


def test_dotted_event_names_such_as_the_phase_05_task_events_are_allowed() -> None:
    frame = encode_sse(3, "task.step_started", DeltaEvent(text="x"))
    assert frame.startswith("id: 3\nevent: task.step_started\n")
