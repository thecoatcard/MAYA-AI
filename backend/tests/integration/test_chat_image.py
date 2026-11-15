"""Image requests in chat (generate_image): the router sends an image request to the image route,
the chat model may only call generate_image (never submit_task), the image model is called by the
service (a fake here), the image is stored as a chat file and shown as a file.created card that is
saved with the reply.

Fakes only: no real Gemini, no network. Files go to a temporary artifact folder.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from app.services import chat_service as chat_module
from app.services import image_gen
from app.services.artifact_service import ArtifactService
from app.services.context_manager import CHAT_ANSWER_ADDENDUM
from app.services.image_gen import IMAGE_CHAT_ADDENDUM, ImageService, RawImage
from tests.integration.test_chat_inline_files import IdempotentSubmitter, call, card_frames
from tests.integration.test_chat_latency import wire
from tests.integration.test_chat_memory import Prefs, tool_names
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    send,
    sign_in,
    stream_calls,
)
from tests.unit.test_image_gen import make_png


class RecordingImageClient:
    """The fake image model. ``enabled`` False makes the chat build no image service at all."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.result = RawImage(make_png(16, 9), "image/png")
        self.enabled = True

    async def generate(self, prompt: str, *, model: str) -> RawImage:
        self.prompts.append(prompt)
        return self.result


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts") as value:
        yield value


@pytest.fixture
def image_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingImageClient]:
    """Replaces the image model with a fake; the real ImageService (sanitiser, validation, storage,
    card) runs around it."""
    client = RecordingImageClient()

    def factory(settings: Any, *, gateway: Any, artifacts: ArtifactService, limiter: Any) -> Any:
        if not client.enabled:
            return None
        return ImageService(
            client=client,
            artifacts=artifacts,
            limiter=limiter,
            model="gemini-3.1-flash-image",
            daily_limit=20,
        )

    monkeypatch.setattr(chat_module, "build_image_service", factory)
    yield client


def system_text(call_record: dict[str, Any]) -> str:
    return str(call_record["config"].system_instruction)


def stored_files(tmp_path: Path) -> list[Path]:
    root = tmp_path / "artifacts"
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


async def test_an_image_request_is_routed_inline_and_the_image_is_a_card(
    world: World, image_client: RecordingImageClient, tmp_path: Path
) -> None:
    submitter = IdempotentSubmitter()
    wire(world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("generate_image", {"prompt": "a cat wearing a small crown", "style_hint": "flat"}),
        [{"text": "Your cat image is ready in the chat."}],
    ]
    frames = events(await send(world, account, "Make an image of a cat wearing a crown"))

    assert submitter.submitted == []  # never a background task
    first, follow_up = stream_calls(world)
    assert tool_names(first) == ["generate_image"]  # the image tool only, no submit_task
    assert IMAGE_CHAT_ADDENDUM in system_text(first)
    assert "generate_image" not in tool_names(follow_up)  # one image per message
    assert "submit_task" not in tool_names(follow_up)

    assert image_client.prompts and "a cat wearing a small crown" in image_client.prompts[0]
    (finished,) = [f.data for f in frames if f.event == "tool.finished"]
    assert finished["name"] == "generate_image" and finished["status"] == "ok"
    (card,) = card_frames(frames)
    assert card["file_type"] == "image" and card["mime_type"] == "image/png"
    assert (card["width"], card["height"]) == (16, 9)
    assert card["name"] == "a-cat-wearing-a-small-crown.png" and card["action"] == "created"
    assert names(frames)[-1] == "done"

    (saved,) = stored_files(tmp_path)
    assert saved.read_bytes() == make_png(16, 9)

    (assistant,) = world.chat.rows("orchestrator")
    assert assistant.metadata["files_created"][0]["file_id"] == card["file_id"]
    assert assistant.metadata["chat_files"][0]["storage_key"].endswith(".png")


async def test_a_refused_image_request_returns_an_error_and_no_card(
    world: World, image_client: RecordingImageClient, tmp_path: Path
) -> None:
    wire(world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("generate_image", {"prompt": "a nude portrait of a woman"}),
        [{"text": "I cannot make that image. Would you like something else?"}],
    ]
    frames = events(await send(world, account, "Make an image of a nude woman"))

    assert image_client.prompts == []  # the refused prompt never reached the image model
    (finished,) = [f.data for f in frames if f.event == "tool.finished"]
    assert finished["status"] == "error"
    assert card_frames(frames) == [] and stored_files(tmp_path) == []
    assert names(frames)[-1] == "done"


async def test_with_images_off_no_image_tool_is_offered(
    world: World, image_client: RecordingImageClient
) -> None:
    image_client.enabled = False
    wire(world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": "Images are not available here."}]]
    await send(world, account, "Make an image of a tree")

    (only,) = stream_calls(world)
    assert "generate_image" not in tool_names(only)
    assert "submit_task" not in tool_names(only)
    assert CHAT_ANSWER_ADDENDUM in system_text(only)


async def test_an_image_request_with_a_limit_refusal_says_so_without_a_card(
    world: World,
    image_client: RecordingImageClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.image_gen import ImageRequestError

    async def refuse(**_: Any) -> Any:
        raise ImageRequestError(
            "You have used today's 20 images. The limit resets at midnight UTC."
        )

    wire(world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("generate_image", {"prompt": "a tree"}),
        [{"text": "You have reached today's image limit."}],
    ]
    original = image_gen.build_image_service  # the function chat_service imports

    def limited(*args: Any, **kwargs: Any) -> Any:
        service = original(*args, **kwargs)
        if service is not None:
            monkeypatch.setattr(service, "generate", refuse)
        return service

    monkeypatch.setattr(chat_module, "build_image_service", limited)
    frames = events(await send(world, account, "Make an image of a tree"))
    (finished,) = [f.data for f in frames if f.event == "tool.finished"]
    assert finished["status"] == "error"
    assert card_frames(frames) == [] and stored_files(tmp_path) == []
    assert image_client.prompts == []
    assert names(frames)[-1] == "done"
