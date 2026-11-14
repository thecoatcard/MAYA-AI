"""Image requests: the deterministic route (chat_router), the live voice tool (voice/tools.py and
voice/prompts.py) and the voice connection that stores the image, saves it on a message and emits
the FILE_CREATED frame. No model, no network, no database.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from app.auth.models import Principal
from app.services.chat_router import Route, route_message
from app.services.image_gen import RawImage
from app.voice import protocol
from app.voice.connection import VoiceConnection, VoiceDeps
from app.voice.prompts import VOICE_IMAGE_ADDENDUM, build_voice_instruction
from app.voice.tools import VoiceExecutionContext, VoiceToolRegistry
from tests.unit.test_image_gen import FakeImageClient, make_png, make_service

IMAGE_ROUTES: list[str] = [
    "Make an image of a cat",
    "Draw a logo for my bakery",
    "Create a picture of a mountain at sunrise",
    "Ek tasveer banao sunset ki",
    "Can you draw a diagram of the water cycle?",
    "Make an illustration of a robot reading a book, in the background",
    "Generate a wallpaper of a blue ocean",
]

NOT_IMAGE_ROUTES: list[tuple[str, Route]] = [
    ("Make a ppt with pictures about solar energy", Route.TASK),  # a deck is a document
    ("Create a notes.md with an image list", Route.MODEL),  # a text file is a document
    ("What is a logo?", Route.ANSWER),  # a question, no make verb
    ("Send this image to Rahul", Route.APPROVAL),  # an outside action goes to approval
]


@pytest.mark.parametrize("text", IMAGE_ROUTES)
def test_an_image_request_routes_to_the_inline_image_route(text: str) -> None:
    decision = route_message(text)
    assert decision.route is Route.IMAGE and decision.reason == "image_request"


@pytest.mark.parametrize(("text", "route"), NOT_IMAGE_ROUTES)
def test_documents_questions_and_approvals_are_not_image_requests(text: str, route: Route) -> None:
    assert route_message(text).route is route


# ----------------------------------------------------------------------------- voice tools


def _declared(registry: VoiceToolRegistry) -> dict[str, dict[str, Any]]:
    return {d["name"]: d for d in registry.get_tool_declarations()}


def test_the_voice_image_tool_is_not_declared_unless_its_handler_is_wired() -> None:
    registry = VoiceToolRegistry()
    assert "generate_image" not in _declared(registry)
    assert "generate_image" not in registry.allowed_tool_names


def test_the_voice_image_tool_is_declared_with_a_required_prompt() -> None:
    registry = VoiceToolRegistry()

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"status": "created"}

    registry.set_image_handler(handler)
    declaration = _declared(registry)["generate_image"]
    assert declaration["parameters"]["type"] == "OBJECT"
    assert declaration["parameters"]["required"] == ["prompt"]
    assert set(declaration["parameters"]["properties"]) == {"prompt", "style_hint"}
    assert "generate_image" in registry.allowed_tool_names
    assert "ready in the chat" in declaration["description"]


async def test_the_voice_image_tool_dispatches_to_the_image_handler() -> None:
    registry = VoiceToolRegistry()
    seen: list[dict[str, Any]] = []

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        seen.append(args)
        return {"status": "created", "name": "cat.png"}

    registry.set_image_handler(handler)
    context = VoiceExecutionContext(workspace_id=uuid4(), user_id=uuid4())
    result = await registry.execute_tool_async("generate_image", {"prompt": "a cat"}, context)
    assert result == {"status": "created", "name": "cat.png"}
    assert seen == [{"prompt": "a cat"}]


def test_the_live_instruction_has_the_image_addendum_only_when_images_are_on() -> None:
    off = build_voice_instruction(memory_on=False)
    on = build_voice_instruction(memory_on=False, images_on=True)
    assert VOICE_IMAGE_ADDENDUM not in off and "generate_image" not in off
    assert VOICE_IMAGE_ADDENDUM in on
    assert "cannot draw or produce the image yourself" in on
    assert "ready in the chat" in on


# ---------------------------------------------------------------- voice connection: the image


class _Persistence:
    def __init__(self, fail: bool = False) -> None:
        self.saved: list[dict[str, Any]] = []
        self.fail = fail

    async def save_turn(
        self, workspace_id: Any, user_id: Any, session_id: Any, **kwargs: Any
    ) -> Any:
        if self.fail:
            raise RuntimeError("database down")
        self.saved.append({"session_id": session_id, **kwargs})
        return uuid4()


def _connection(images: Any, persistence: _Persistence, session_id: Any) -> tuple[Any, list[Any]]:
    """A voice connection without a socket: only the attributes the image path reads are set."""
    connection = VoiceConnection.__new__(VoiceConnection)
    connection.deps = cast(VoiceDeps, SimpleNamespace(images=images, persistence=persistence))
    connection.principal = cast(Principal, SimpleNamespace(workspace_id=uuid4(), user_id=uuid4()))
    connection.session_id = session_id
    frames: list[Any] = []

    async def emit(frame: dict[str, Any]) -> None:
        frames.append(frame)

    connection._emit = emit  # type: ignore[method-assign]
    return connection, frames


async def test_a_voice_image_is_stored_saved_on_a_message_and_sent_as_a_card(
    tmp_path: Path,
) -> None:
    client = FakeImageClient(RawImage(make_png(10, 4), "image/png"))
    service, _ = make_service(tmp_path, client)
    session_id = uuid4()
    persistence = _Persistence()
    connection, frames = _connection(service, persistence, session_id)

    result = await connection._generate_image({"prompt": "a cat", "style_hint": "flat"})

    assert result["status"] == "created" and result["name"] == "a-cat.png"
    (saved,) = persistence.saved
    assert saved["session_id"] == session_id and saved["role"] == "assistant"
    assert saved["modality"] == "audio" and saved["content"] == "Image created: a-cat.png"
    card = saved["metadata"]["files_created"][0]
    assert card["file_type"] == "image" and card["mime_type"] == "image/png"
    assert saved["metadata"]["chat_files"][0]["storage_key"].endswith("a-cat.png")
    (frame,) = frames
    assert frame["type"] == "FILE_CREATED" and frame["file"] == card
    assert frame["message_id"] is not None


async def test_a_voice_image_refusal_returns_an_error_and_sends_no_card(tmp_path: Path) -> None:
    client = FakeImageClient()
    service, _ = make_service(tmp_path, client)
    persistence = _Persistence()
    connection, frames = _connection(service, persistence, uuid4())

    result = await connection._generate_image({"prompt": "a nude portrait"})

    assert result["status"] == "error" and "Nothing was created" in result["message"]
    assert persistence.saved == [] and frames == [] and client.calls == []


async def test_a_failed_voice_save_still_sends_the_card_without_a_message_id(
    tmp_path: Path,
) -> None:
    client = FakeImageClient(RawImage(make_png(), "image/png"))
    service, _ = make_service(tmp_path, client)
    connection, frames = _connection(service, _Persistence(fail=True), uuid4())

    result = await connection._generate_image({"prompt": "a cat"})

    assert result["status"] == "created"
    (frame,) = frames
    assert frame["message_id"] is None  # the image is shown now; it just is not on a saved message


async def test_voice_images_are_unavailable_without_a_session() -> None:
    connection, frames = _connection(None, _Persistence(), None)
    result = await connection._generate_image({"prompt": "a cat"})
    assert result["status"] == "error" and frames == []


def test_the_file_created_frame_has_the_card_and_message_id() -> None:
    frame = protocol.file_created({"name": "a.png"}, "m-1")
    assert frame == {"type": "FILE_CREATED", "message_id": "m-1", "file": {"name": "a.png"}}
