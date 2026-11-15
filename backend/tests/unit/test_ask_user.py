"""ask_user: argument clamping, answer sanitising, call/response rebuild, voice registry and the
voice handler. No network, no database."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.schemas.chat import InputAnswerIn, InputQuestion, InputRequestedEvent
from app.services import ask_user
from app.voice.connection import VoiceConnection
from app.voice.tools import VoiceApprovalProhibitedError, VoiceExecutionContext, VoiceToolRegistry


def test_clamp_limits_questions_options_and_text_and_strips_control_characters() -> None:
    raw = [
        {
            "question": "Pick\x00 one\x1b" + "?" * 400,
            "header": "Header that is long",
            "options": [{"label": f"L{i}\x07", "description": "d" * 400} for i in range(10)],
            "multi_select": True,
        }
        for _ in range(7)
    ]
    questions = ask_user.clamp_questions(raw)
    assert len(questions) == 4
    first = questions[0]
    assert (
        len(first.question) <= 300 and "\x00" not in first.question and "\x1b" not in first.question
    )
    assert len(first.header) <= 12 and len(first.options) == 6
    assert all("\x07" not in o.label and len(o.description) <= 160 for o in first.options)


def test_fewer_than_two_options_or_duplicates_make_a_text_question() -> None:
    (one,) = ask_user.clamp_questions(
        [{"question": "Which?", "options": [{"label": "A"}, {"label": "a"}], "allow_text": False}]
    )
    assert one.options == [] and one.allow_text is True and one.multi_select is False


def test_defaults_and_junk_are_handled() -> None:
    (q,) = ask_user.clamp_questions(
        [{"question": "Which?", "options": ["Red", {"label": "Blue"}, 5, None]}, "junk", {}]
    )
    assert [o.label for o in q.options] == ["Red", "Blue"] and q.allow_text is True
    assert ask_user.clamp_questions("nope") == [] and ask_user.clamp_questions([]) == []
    assert ask_user.clamp_questions({"question": "Single mapping?"})[0].question


def test_secret_shaped_text_never_reaches_the_card() -> None:
    (q,) = ask_user.clamp_questions([{"question": "Use key sk-" + "a" * 40 + " ?"}])
    assert "sk-" + "a" * 40 not in q.question


def test_model_text_is_plain_data_not_a_link_target() -> None:
    # The card carries strings only; the UI renders them as text. Nothing is parsed or resolved.
    (q,) = ask_user.clamp_questions(
        [{"question": "Open [docs](https://evil.example)?", "options": ["[a](javascript:1)", "b"]}]
    )
    assert isinstance(q.options[0].label, str) and q.options[0].label.startswith("[a]")


def _card() -> InputRequestedEvent:
    return InputRequestedEvent(
        ask_id="ask_1",
        questions=[
            InputQuestion.model_validate(
                {
                    "question": "Language?",
                    "header": "Language",
                    "options": [{"label": "Java"}, {"label": "Go"}],
                }
            ),
            InputQuestion.model_validate({"question": "Subject?", "header": "Subject"}),
        ],
    )


def test_sanitise_answers_checks_labels_and_returns_none_when_empty() -> None:
    card = _card()
    ok = InputAnswerIn.model_validate(
        {"ask_id": "ask_1", "answers": [{"selected": ["java"]}, {"text": " Hello \x00 "}]}
    )
    items = ask_user.sanitise_answers(ok, card.questions)
    assert items is not None
    assert items[0].selected == ["Java"] and items[1].text.strip() == "Hello"
    assert ask_user.answer_text(card.questions, items) == "Language: Java; Subject: Hello"
    empty = InputAnswerIn.model_validate(
        {"ask_id": "ask_1", "answers": [{"selected": ["Rust"]}, {"text": "  "}]}
    )
    assert ask_user.sanitise_answers(empty, card.questions) is None


def test_the_function_response_does_not_authorise_anything() -> None:
    response = ask_user.answer_response([])
    assert response["status"] == "answered"
    assert "approval card" in response["message"]


def test_rebuild_pairs_the_call_and_the_response_even_without_a_model_turn() -> None:
    card = _card()
    stored = ask_user.stored_request(card, "call-7", b"sig")
    items = ask_user.sanitise_answers(
        InputAnswerIn.model_validate({"ask_id": "ask_1", "answers": [{"selected": ["Go"]}]}),
        card.questions,
    )
    assert items is not None
    response = ask_user.answer_response(items)
    contents = [{"role": "user", "parts": [{"text": "answer text"}]}]
    rebuilt = ask_user.rebuild_for_answer(contents, stored, card, response)
    assert [c["role"] for c in rebuilt] == ["model", "user"]
    call = rebuilt[0]["parts"][0]
    assert call["function_call"]["id"] == "call-7" and call["thought_signature"] == b"sig"
    assert rebuilt[1]["parts"][0]["function_response"]["id"] == "call-7"
    with_text = [
        {"role": "user", "parts": [{"text": "write code"}]},
        {"role": "model", "parts": [{"text": "Quick check."}]},
        {"role": "user", "parts": [{"text": "answer text"}]},
    ]
    merged = ask_user.rebuild_for_answer(with_text, stored, card, response)
    assert [c["role"] for c in merged] == ["user", "model", "user"]
    assert [list(p)[0] for p in merged[1]["parts"]] == ["text", "function_call"]


def _record(sender: str, **meta: Any) -> Any:
    return SimpleNamespace(sender=sender, metadata=meta)


def test_pending_means_no_user_message_follows_the_card() -> None:
    card = ask_user.stored_request(_card(), "c", None)
    asked = _record("orchestrator", input_request=card)
    assert ask_user.pending_request([_record("user"), asked]) == (1, card)
    # a spoken assistant turn stored after a voice card does not resolve it
    assert ask_user.pending_request([_record("user"), asked, _record("orchestrator")]) == (1, card)
    assert ask_user.pending_request([_record("user"), asked, _record("user")]) is None
    assert ask_user.pending_request([]) is None


def test_card_state_is_derived_from_the_next_user_message() -> None:
    stored = ask_user.stored_request(_card(), "c", None)
    assert ask_user.card_state(stored, None) is not None
    assert ask_user.card_state(stored, None).state == "awaiting_input"  # type: ignore[union-attr]
    answered = _record("user", answers_to="ask_1")
    assert ask_user.card_state(stored, answered).state == "answered"  # type: ignore[union-attr]
    assert ask_user.card_state(stored, _record("user")).state == "dismissed"  # type: ignore[union-attr]


def test_ask_id_is_stable_per_reply() -> None:
    reply = UUID(int=5)
    assert ask_user.ask_id_for(reply) == ask_user.ask_id_for(reply)
    assert ask_user.ask_id_for(reply) != ask_user.ask_id_for(uuid4())


# ------------------------------------------------------------------------------------- voice


CTX = VoiceExecutionContext(workspace_id=uuid4(), user_id=uuid4())


async def test_voice_registry_declares_and_dispatches_ask_user_only_when_enabled() -> None:
    registry = VoiceToolRegistry()
    assert "ask_user" not in registry.allowed_tool_names
    seen: list[dict[str, Any]] = []

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        seen.append(args)
        return {"status": "shown"}

    registry.set_ask_handler(handler)
    assert "ask_user" in registry.allowed_tool_names
    assert "ask_user" in {d["name"] for d in registry.get_tool_declarations()}
    assert await registry.execute_tool_async("ask_user", {"questions": []}, CTX) == {
        "status": "shown"
    }
    assert seen == [{"questions": []}]
    description = next(d for d in registry.get_tool_declarations() if d["name"] == "ask_user")[
        "description"
    ]
    assert "approval" in description.lower()  # it says it never asks for approvals
    with pytest.raises(VoiceApprovalProhibitedError):
        await registry.execute_tool_async("approve_ask_user", {}, CTX)


class _Persistence:
    engine = object()

    def __init__(self, fail: bool = False) -> None:
        self.saved: list[dict[str, Any]] = []
        self.fail = fail

    async def save_turn(self, *_: Any, **kwargs: Any) -> UUID:
        if self.fail:
            raise RuntimeError("down")
        self.saved.append(kwargs)
        return uuid4()


def _connection(
    persistence: _Persistence, session: UUID | None
) -> tuple[Any, list[dict[str, Any]]]:
    frames: list[dict[str, Any]] = []

    async def emit(frame: dict[str, Any]) -> None:
        frames.append(frame)

    conn: Any = object.__new__(VoiceConnection)
    conn.session_id = session
    conn.principal = SimpleNamespace(workspace_id=uuid4(), user_id=uuid4())
    conn.deps = SimpleNamespace(persistence=persistence)
    conn._emit = emit
    return conn, frames


async def test_voice_handler_stores_the_card_and_emits_one_frame() -> None:
    persistence = _Persistence()
    conn, frames = _connection(persistence, uuid4())
    result = await conn._ask_user(
        {"questions": [{"question": "Which folder?", "header": "Folder"}]}
    )
    assert result["status"] == "shown"
    (saved,) = persistence.saved
    assert saved["role"] == "assistant" and saved["content"] == "Which folder?"
    assert saved["metadata"]["input_request"]["source"] == "voice"
    (frame,) = frames
    assert frame["type"] == "INPUT_REQUESTED" and frame["card"]["state"] == "awaiting_input"


async def test_voice_handler_refuses_bad_input_without_a_card() -> None:
    conn, frames = _connection(_Persistence(), uuid4())
    assert (await conn._ask_user({"questions": []}))["status"] == "error"
    conn, frames = _connection(_Persistence(fail=True), uuid4())
    assert (await conn._ask_user({"questions": [{"question": "Q?"}]}))["status"] == "error"
    assert frames == []
    conn, frames = _connection(_Persistence(), None)
    assert (await conn._ask_user({"questions": [{"question": "Q?"}]}))["status"] == "error"
